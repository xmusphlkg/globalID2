"""
GlobalID V2 Data Processor

数据处理器，整合爬虫、解析器、标准化器的完整数据处理流程
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import re

import pandas as pd
from bs4 import BeautifulSoup

from src.core import get_logger
from src.core.database import get_db
from src.domain import CrawlRawPage, DiseaseRecord, Country
from src.data.crawlers.base import CrawlerResult
from src.data.parsers.html_parser import HTMLTableParser
from src.data.normalizers.english_mapper import create_disease_mapper

logger = get_logger(__name__)


class DataProcessor:
    """
    数据处理器
    
    负责处理从爬虫获取的数据，包括：
    1. 解析HTML表格
    2. 疾病名称标准化
    3. 数据清洗
    4. 数据验证
    5. 数据存储
    """
    
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        country_code: str = "cn",
    ):
        """
        初始化数据处理器
        
        Args:
            output_dir: 输出目录
            country_code: 国家代码（cn/us/uk等）
        """
        self.output_dir = output_dir or Path("data/processed")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.country_code = country_code
        
        # 初始化解析器和映射器
        self.parser = HTMLTableParser()
        # 注意：disease_mapper 将在异步方法中初始化
        
        logger.info(f"数据处理器初始化完成 (country: {country_code}, 使用数据库映射器)")
    
    async def process_crawler_results(
        self,
        results: List[CrawlerResult],
        save_to_file: bool = True,
        save_raw: bool = False,
        crawl_run_id: Optional[int] = None,
        raw_dir: Optional[Path] = None,
    ) -> List[pd.DataFrame]:
        """
        处理爬虫结果
        
        Args:
            results: 爬虫结果列表
            save_to_file: 是否保存到文件
            
        Returns:
            处理后的数据框列表
        """
        processed_data = []
        
        for i, result in enumerate(results, 1):
            try:
                logger.info(f"处理 {i}/{len(results)}: {result.title}")
                
                # 解析HTML表格
                parse_result = self.parser.parse(
                    result.url or result.content,
                    url=result.url,
                    title=result.title,
                    date=result.date,
                    year_month=result.year_month,
                    source=result.metadata.get("source"),
                    language=result.metadata.get("language", "en"),
                    doi=result.metadata.get("doi"),
                )
                
                if not parse_result.success or not parse_result.has_data:
                    logger.warning(f"解析失败: {parse_result.error_message}")
                    continue

                if save_raw and crawl_run_id and raw_dir and parse_result.raw_content:
                    await self._save_raw_content(
                        run_id=crawl_run_id,
                        raw_dir=raw_dir,
                        result=result,
                        raw_html=parse_result.raw_content,
                        fetched_at=parse_result.parse_date,
                    )
                
                # 根据数据源创建合适的疾病映射器
                data_source = result.metadata.get("source", "")
                language = result.metadata.get("language", "en")
                
                # 新版多语言架构：支持国家和语言分离
                disease_mapper = await create_disease_mapper(
                    country_code=self.country_code or "CN",  # 使用处理器的国家代码
                    language=language, 
                    data_source=data_source
                )
                
                # 标准化疾病名称
                df = await self._normalize_disease_names(
                    parse_result.data,
                    language=language,
                    disease_mapper=disease_mapper,
                )
                
                # 计算发病率和死亡率（如果有人口数据）
                df = self._calculate_rates(df)
                
                # 验证数据
                if not self._validate_data(df):
                    logger.warning(f"数据验证失败: {result.title}")
                    continue
                
                processed_data.append(df)
                
                # 保存到文件
                if save_to_file and result.year_month:
                    self._save_to_file(df, result.year_month)
                
                # 保存到数据库
                await self._save_to_database(df, self.country_code)
                
            except Exception as e:
                logger.error(f"处理失败: {result.title}, 错误: {e}")
                continue
        
        logger.info(f"成功处理 {len(processed_data)}/{len(results)} 条数据")
        
        # 显示统计信息（使用最后一个映射器，或默认中文映射器）
        try:
            if 'disease_mapper' in locals():
                stats = await disease_mapper.get_statistics()
            else:
                # 如果没有处理任何数据，使用默认映射器获取统计
                default_mapper = await create_disease_mapper(
                    country_code=self.country_code or "CN",
                    language="zh"
                )
                stats = await default_mapper.get_statistics()
            
            pending_count = stats.get('pending_suggestions', 0)
            if pending_count > 0:
                logger.warning(f"发现 {pending_count} 个待审核的未识别疾病，请运行: python scripts/disease_cli.py suggestions")
        except Exception as e:
            logger.warning(f"获取统计信息失败: {e}")
        
        return processed_data

    async def save_raw_pages(
        self,
        results: List[CrawlerResult],
        crawl_run_id: int,
        raw_dir: Path,
    ) -> int:
        """仅保存原始页面文本（不进行标准化处理）"""
        saved = 0
        for i, result in enumerate(results, 1):
            try:
                parse_result = self.parser.parse(
                    result.url or result.content,
                    url=result.url,
                    title=result.title,
                    date=result.date,
                    year_month=result.year_month,
                    source=result.metadata.get("source"),
                    language=result.metadata.get("language", "en"),
                    doi=result.metadata.get("doi"),
                )

                if parse_result.raw_content:
                    await self._save_raw_content(
                        run_id=crawl_run_id,
                        raw_dir=raw_dir,
                        result=result,
                        raw_html=parse_result.raw_content,
                        fetched_at=parse_result.parse_date,
                    )
                    saved += 1
            except Exception as e:
                logger.warning(f"原始内容保存失败: {result.title}, 错误: {e}")
                continue

        return saved
    
    async def _normalize_disease_names(
        self,
        df: pd.DataFrame,
        language: str = "en",
        disease_mapper = None,
    ) -> pd.DataFrame:
        """
        标准化疾病名称
        
        Args:
            df: 数据框
            language: 源语言 ("en" 或 "zh")
            disease_mapper: 疾病映射器实例
            
        Returns:
            标准化后的数据框
        """
        logger.info(f"_normalize_disease_names 输入 DataFrame 形状: {df.shape}, 索引: {df.index}")
        
        # 如果没有提供映射器，使用默认的
        if disease_mapper is None:
            disease_mapper = await create_disease_mapper(
                country_code="CN",  # 默认中国
                language=language
            )
        
        # 使用数据库映射器: 本地名称 -> 标准英文名 + disease_id
        source_col = "DiseasesCN" if language == "zh" else "Diseases"
        logger.info(f"使用源列: {source_col}")
        
        df = await disease_mapper.map_dataframe(
            df,
            disease_col=source_col,
        )
        
        logger.info(f"map_dataframe 返回 DataFrame 形状: {df.shape}, 索引: {df.index}")
        logger.info(f"map_dataframe 返回的列: {list(df.columns)}")
        
        # map_dataframe 已经添加了 disease_id, standard_name_en, standard_name_zh
        # 重命名标准列
        if 'standard_name_en' in df.columns:
            logger.info(f"standard_name_en 列长度: {len(df['standard_name_en'])}, 前5个值: {df['standard_name_en'].head().tolist()}")
            df['Diseases'] = df['standard_name_en']
            logger.info("成功设置 Diseases 列")
        if 'standard_name_zh' in df.columns:
            logger.info(f"standard_name_zh 列长度: {len(df['standard_name_zh'])}, 前5个值: {df['standard_name_zh'].head().tolist()}")
            df['DiseasesCN'] = df['standard_name_zh']
            logger.info("成功设置 DiseasesCN 列")
        
        # 移除映射失败的行（疾病名称为空）
        before_count = len(df)
        logger.info(f"移除映射失败的行前: {before_count} 行")
        df = df[df["Diseases"].notna() & (df["Diseases"] != "")]
        after_count = len(df)
        
        if before_count > after_count:
            logger.warning(
                f"移除了 {before_count - after_count} 行无法映射的数据"
            )
        
        logger.info(f"_normalize_disease_names 返回 DataFrame 形状: {df.shape}, 索引: {df.index}")
        return df

    def _slugify(self, text: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
        return safe or "page"

    def _html_to_text(self, html: str) -> str:
        """提取HTML主要文本内容，去除导航栏、页眉等杂项"""
        soup = BeautifulSoup(html, "html.parser")
        
        # 移除常见的导航和页面结构元素
        for element in soup.find_all(['nav', 'header', 'footer', 'aside', 'script', 'style']):
            element.decompose()
        
        # 移除常见的导航类名
        for element in soup.find_all(class_=lambda x: x and any(
            nav in str(x).lower() for nav in ['nav', 'menu', 'sidebar', 'header', 'footer', 'breadcrumb']
        )):
            element.decompose()
        
        # 尝试找到主要内容区域
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=lambda x: x and 'content' in str(x).lower())
        if main_content:
            soup = main_content
        
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return cleaned

    async def _save_raw_content(
        self,
        run_id: int,
        raw_dir: Path,
        result: CrawlerResult,
        raw_html: str,
        fetched_at: datetime,
    ) -> None:
        # 使用年份作为文件夹名（从 year_month 提取，如 "2025 December" -> "2025"）
        year_str = "unknown"
        if result.year_month:
            # 提取年份部分（支持 "2025 December", "2025-12", "202512" 等格式）
            import re
            year_match = re.search(r'(20\d{2})', result.year_month)
            if year_match:
                year_str = year_match.group(1)
        
        year_dir = raw_dir / year_str
        year_dir.mkdir(parents=True, exist_ok=True)

        label = result.year_month or result.title or "report"
        plain_text = self._html_to_text(raw_html)
        content_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()
        filename = f"{self._slugify(label)}_{content_hash[:8]}.txt"
        file_path = year_dir / filename

        # 添加元数据标签到文本开头
        metadata_header = f"""# ========================================
# 原始数据文件元数据
# ========================================
# URL: {result.url or 'N/A'}
# 标题: {result.title or 'N/A'}
# 报告时间: {result.year_month or 'N/A'}
# 数据源: {result.metadata.get('source', 'N/A')}
# 抓取时间: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}
# 内容哈希: {content_hash}
# DOI: {result.metadata.get('doi', 'N/A')}
# ========================================

"""
        full_content = metadata_header + plain_text
        file_path.write_text(full_content, encoding="utf-8")

        async with get_db() as db:
            db.add(
                CrawlRawPage(
                    run_id=run_id,
                    url=result.url or "",
                    title=result.title,
                    content_path=str(file_path),
                    content_hash=content_hash,
                    content_type="text/plain",
                    fetched_at=fetched_at,
                    source=result.metadata.get("source"),
                    metadata_={
                        "year_month": result.year_month,
                        "has_url": bool(result.url),
                    },
                )
            )
    
    def _calculate_rates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算发病率和死亡率
        
        Args:
            df: 数据框
            
        Returns:
            添加了发病率和死亡率的数据框
        """
        # TODO: 从人口数据库获取人口数
        # 目前保持-10作为占位符
        
        # 如果有人口数据，可以这样计算：
        # df["Incidence"] = df["Cases"] / population * 100000
        # df["Mortality"] = df["Deaths"] / population * 100000
        
        return df
    
    def _validate_data(self, df: pd.DataFrame) -> bool:
        """
        验证数据质量
        
        Args:
            df: 数据框
            
        Returns:
            是否通过验证
        """
        if df.empty:
            logger.warning("数据为空")
            return False
        
        # 检查必需的列
        required_columns = [
            "Date", "YearMonth", "Diseases", "Cases", "Deaths"
        ]
        for col in required_columns:
            if col not in df.columns:
                logger.warning(f"缺少必需的列: {col}")
                return False
        
        # 检查数值列
        numeric_columns = ["Cases", "Deaths"]
        for col in numeric_columns:
            try:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                # 检查是否有太多NaN
                nan_ratio = df[col].isna().sum() / len(df)
                if nan_ratio > 0.5:
                    logger.warning(f"列 {col} 有超过50%的无效数据")
                    return False
            except Exception as e:
                logger.error(f"无法转换列 {col} 为数值: {e}")
                return False
        
        # 检查负数
        for col in numeric_columns:
            if (df[col] < 0).any():
                logger.warning(f"列 {col} 包含负数")
                # 不直接返回False，只是警告
        
        return True
    
    def _save_to_file(self, df: pd.DataFrame, year_month: str):
        """
        保存数据到文件
        
        Args:
            df: 数据框
            year_month: 年月字符串 (如 "2024 January")
        """
        try:
            # 构造文件名
            filename = f"{year_month}.csv"
            filepath = self.output_dir / filename
            
            # 保存为CSV
            df.to_csv(filepath, index=False, encoding="utf-8-sig")
            logger.info(f"保存数据到: {filepath}")
            
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
    
    async def _save_to_database(self, df: pd.DataFrame, country_code: str):
        """
        保存数据到数据库
        
        Args:
            df: 数据框
            country_code: 国家代码
        """
        if df.empty:
            logger.warning("数据为空，跳过数据库保存")
            return
        
        try:
            async with get_db() as db:
                # 获取国家ID
                from sqlalchemy import select
                from src.domain import Disease
                country_query = select(Country).where(Country.code == country_code.upper())
                country_result = await db.execute(country_query)
                country = country_result.scalar_one_or_none()
                
                if not country:
                    logger.warning(f"国家不存在: {country_code}")
                    return
                
                # 准备记录列表
                records_to_save = []
                skipped_count = 0
                
                for _, row in df.iterrows():
                    # 检查必需字段
                    if 'disease_id' not in df.columns or pd.isna(row.get('disease_id')):
                        skipped_count += 1
                        continue
                    
                    if 'Date' not in df.columns or pd.isna(row.get('Date')):
                        skipped_count += 1
                        continue
                    
                    # disease_id是疾病代码（如"D001"），需要查询实际的数据库ID
                    disease_code = str(row['disease_id'])
                    disease_query = select(Disease).where(Disease.name == disease_code)
                    disease_result = await db.execute(disease_query)
                    disease = disease_result.scalar_one_or_none()
                    
                    if not disease:
                        logger.warning(f"疾病不存在: {disease_code}")
                        skipped_count += 1
                        continue
                    
                    # 创建记录
                    record = DiseaseRecord(
                        time=pd.to_datetime(row['Date']),
                        disease_id=disease.id,  # 使用数据库中的实际ID
                        country_id=country.id,
                        cases=int(row['Cases']) if pd.notna(row.get('Cases')) else None,
                        deaths=int(row['Deaths']) if pd.notna(row.get('Deaths')) else None,
                        incidence_rate=float(row['Incidence']) if pd.notna(row.get('Incidence')) else None,
                        mortality_rate=float(row['Mortality']) if pd.notna(row.get('Mortality')) else None,
                        data_source=row.get('Source'),
                        metadata_={
                            'disease_name_en': row.get('Diseases'),
                            'disease_name_zh': row.get('DiseasesCN'),
                            'province': row.get('Province'),
                            'province_cn': row.get('ProvinceCN'),
                            'year_month': row.get('YearMonth'),
                            'disease_code': disease_code,
                        }
                    )
                    records_to_save.append(record)
                
                if skipped_count > 0:
                    logger.warning(f"跳过 {skipped_count} 条记录（缺少必需字段或疾病不存在）")
                
                # 批量保存
                if records_to_save:
                    db.add_all(records_to_save)
                    await db.commit()
                    logger.info(f"成功保存 {len(records_to_save)} 条记录到数据库")
                else:
                    logger.warning("没有有效记录可保存到数据库")
                    
        except Exception as e:
            logger.error(f"保存到数据库失败: {e}")
            raise
    
    def merge_data(
        self,
        data_list: List[pd.DataFrame],
        output_file: Optional[Path] = None,
    ) -> pd.DataFrame:
        """
        合并多个数据框
        
        Args:
            data_list: 数据框列表
            output_file: 输出文件路径
            
        Returns:
            合并后的数据框
        """
        if not data_list:
            logger.warning("没有数据可合并")
            return pd.DataFrame()
        
        try:
            # 合并所有数据
            merged_df = pd.concat(data_list, ignore_index=True)
            
            # 按日期排序
            merged_df = merged_df.sort_values("Date", ascending=True)
            
            # 去重
            before_count = len(merged_df)
            merged_df = merged_df.drop_duplicates(
                subset=["Date", "Diseases", "Province"],
                keep="last"
            )
            after_count = len(merged_df)
            
            if before_count > after_count:
                logger.info(f"去除了 {before_count - after_count} 条重复数据")
            
            # 保存到文件
            if output_file:
                merged_df.to_csv(output_file, index=False, encoding="utf-8-sig")
                logger.info(f"合并数据保存到: {output_file}")
            
            return merged_df
            
        except Exception as e:
            logger.error(f"合并数据失败: {e}")
            return pd.DataFrame()
    
    def process_single_url(
        self,
        url: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[pd.DataFrame]:
        """
        处理单个URL（便捷方法）
        
        Args:
            url: 页面URL
            metadata: 元数据
            
        Returns:
            处理后的数据框，如果失败则返回None
        """
        metadata = metadata or {}
        
        try:
            # 解析
            parse_result = self.parser.parse(url, **metadata)
            
            if not parse_result.success or not parse_result.has_data:
                logger.error(f"解析失败: {parse_result.error_message}")
                return None
            
            # 标准化
            df = self._normalize_disease_names(
                parse_result.data,
                language=metadata.get("language", "en"),
            )
            
            # 计算
            df = self._calculate_rates(df)
            
            # 验证
            if not self._validate_data(df):
                logger.error("数据验证失败")
                return None
            
            return df
            
        except Exception as e:
            logger.error(f"处理URL失败: {url}, 错误: {e}")
            return None
