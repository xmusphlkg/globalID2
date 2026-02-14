"""
GlobalID V2 Data Processor

数据处理器，整合爬虫、解析器、标准化器的完整数据处理流程
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.core import get_logger
from src.data.crawlers.base import CrawlerResult
from src.data.parsers.html_parser import HTMLTableParser
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB

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
    ) -> List[pd.DataFrame]:
        """
        处理爬虫结果
        
        Args:
            results: 爬虫结果列表
            save_to_file: 是否保存到文件
            
        Returns:
            处理后的数据框列表
        """
        # 初始化异步映射器
        self.disease_mapper = DiseaseMapperDB(country_code=self.country_code)
        
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
                
                # 标准化疾病名称
                df = await self._normalize_disease_names(
                    parse_result.data,
                    language=result.metadata.get("language", "en"),
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
                
            except Exception as e:
                logger.error(f"处理失败: {result.title}, 错误: {e}")
                continue
        
        logger.info(f"成功处理 {len(processed_data)}/{len(results)} 条数据")
        
        # 导出未识别的疾病（数据库版会自动记录到learning_suggestions表）
        stats = await self.disease_mapper.get_statistics()
        pending_count = stats.get('pending_suggestions', 0)
        if pending_count > 0:
            logger.warning(f"发现 {pending_count} 个待审核的未识别疾病，请运行: python scripts/disease_cli.py suggestions")
        
        return processed_data
    
    async def _normalize_disease_names(
        self,
        df: pd.DataFrame,
        language: str = "en",
    ) -> pd.DataFrame:
        """
        标准化疾病名称
        
        Args:
            df: 数据框
            language: 源语言 ("en" 或 "zh")
            
        Returns:
            标准化后的数据框
        """
        logger.info(f"_normalize_disease_names 输入 DataFrame 形状: {df.shape}, 索引: {df.index}")
        
        # 使用数据库映射器: 本地名称 -> 标准英文名 + disease_id
        source_col = "DiseasesCN" if language == "zh" else "Diseases"
        logger.info(f"使用源列: {source_col}")
        
        df = await self.disease_mapper.map_dataframe(
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
