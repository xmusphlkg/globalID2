#!/usr/bin/env python3
"""
获取中国疾病数据缺失月份的数据

针对数据质量检查中发现缺失月份的疾病，重新访问数据源URL，
使用现有的爬虫和解析器获取并清洗数据。

Usage:
  python scripts/cn_missing_data_get.py [--disease-id DISEASE_ID] [--date-range START END]
"""
import asyncio
import sys
import os
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import argparse

import pandas as pd
from sqlalchemy import text

# Add project root to path
sys.path.append(os.getcwd())

from src.core.database import get_db
from src.core import get_logger
from src.data.crawlers.cn_cdc import ChinaCDCCrawler
from src.data.parsers.html_parser import HTMLTableParser
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB

logger = get_logger(__name__)


class MissingDataFetcher:
    """缺失数据获取器"""
    
    def __init__(self, country_code: str = "CN"):
        self.country_code = country_code
        self.crawler = ChinaCDCCrawler()
        self.parser = HTMLTableParser()
        self.disease_mapper = None
        self.mapping_dict = {}  # 本地映射字典
        self.missing_disease_ids = set()  # 缺失的疾病ID集合
        
    async def initialize(self):
        """初始化异步组件"""
        self.disease_mapper = DiseaseMapperDB(country_code=self.country_code)
        
        # 加载所有疾病映射到本地字典（包括英文和中文）
        await self._load_mapping_dict()
        
        logger.info("初始化完成")
    
    async def _load_mapping_dict(self):
        """加载疾病映射字典（包括中文名和英文名）"""
        async with get_db() as db:
            # 1. 从disease_mappings加载本地名称（主要是中文）
            result = await db.execute(text("""
                SELECT dm.local_name, d.id, d.name
                FROM disease_mappings dm
                JOIN diseases d ON dm.disease_id = d.name
                WHERE dm.country_code = :code AND dm.is_active = true
            """), {"code": self.country_code})
            
            # 使用normalized key进行匹配
            def _norm(s):
                try:
                    return s.strip().lower().replace(' ', '').replace('-', '').replace('_', '')
                except:
                    return None
            
            # 加载中文映射
            for row in result:
                local_name = row[0]
                db_id = row[1]
                disease_name = row[2]  # D004, D043 etc.
                
                normalized = _norm(local_name)
                if normalized:
                    self.mapping_dict[normalized] = {
                        'db_id': db_id,
                        'disease_name': disease_name,
                        'local_name': local_name
                    }
            
            cn_count = len(self.mapping_dict)
            
            # 2. 从diseases表加载英文名称映射
            result = await db.execute(text("""
                SELECT d.name_en, d.id, d.name
                FROM diseases d
                WHERE d.name_en IS NOT NULL AND d.name_en != ''
            """))
            
            # 加载英文映射
            en_added = 0
            for row in result:
                name_en = row[0]
                db_id = row[1]
                disease_name = row[2]  # D004, D043 etc
                
                normalized = _norm(name_en)
                if normalized and normalized not in self.mapping_dict:
                    self.mapping_dict[normalized] = {
                        'db_id': db_id,
                        'disease_name': disease_name,
                        'local_name': name_en  # \u4f7f\u7528\u82f1\u6587\u540d\u4f5c\u4e3a\u672c\u5730\u540d\u79f0
                    }
                    en_added += 1
            
            logger.info(f"\u52a0\u8f7d\u4e86 {len(self.mapping_dict)} \u4e2a\u75be\u75c5\u6620\u5c04\uff08\u4e2d\u6587: {cn_count}, \u82f1\u6587: {en_added}\uff09")
    
    def map_disease_name(self, disease_name: str) -> Optional[Dict]:
        """映射疾病名称到数据库ID（支持中英文）"""
        if not disease_name or pd.isna(disease_name):
            return None
        
        def _norm(s):
            try:
                return s.strip().lower().replace(' ', '').replace('-', '').replace('_', '')
            except:
                return None
        
        normalized = _norm(str(disease_name))
        if normalized and normalized in self.mapping_dict:
            return self.mapping_dict[normalized]
        
        return None
    
    async def get_missing_months_info(
        self, 
        disease_id: Optional[int] = None
    ) -> List[Dict]:
        """
        获取缺失月份的疾病信息
        
        Args:
            disease_id: 指定疾病ID，如果为None则获取所有有缺失的疾病
            
        Returns:
            包含疾病ID、名称、时间范围和缺失月份的列表
        """
        logger.info("正在查询数据库中的缺失月份信息...")
        
        async with get_db() as db:
            # 查询每个疾病的时间范围
            query = """
                SELECT 
                    disease_id,
                    d.name AS disease_name,
                    d.name_en AS disease_name_en,
                    MIN(DATE_TRUNC('month', time)::date) AS min_month,
                    MAX(DATE_TRUNC('month', time)::date) AS max_month,
                    COUNT(DISTINCT DATE_TRUNC('month', time)::date) AS actual_months
                FROM disease_records dr
                JOIN diseases d ON dr.disease_id = d.id
                WHERE country_id = (SELECT id FROM countries WHERE code = 'CN')
            """
            
            if disease_id:
                query += f" AND disease_id = {disease_id}"
            
            query += """
                GROUP BY disease_id, d.name, d.name_en
                HAVING COUNT(DISTINCT DATE_TRUNC('month', time)::date) > 0
                ORDER BY disease_id
            """
            
            result = await db.execute(text(query))
            diseases_info = result.fetchall()
            
            missing_info = []
            
            for row in diseases_info:
                did, name, name_en, min_m, max_m, actual = row
                
                # 生成期望的所有月份
                expected_months = []
                current = min_m
                while current <= max_m:
                    expected_months.append(current)
                    # 移动到下个月
                    if current.month == 12:
                        current = current.replace(year=current.year + 1, month=1)
                    else:
                        current = current.replace(month=current.month + 1)
                
                expected_count = len(expected_months)
                
                # 只处理有缺失的疾病
                if actual < expected_count:
                    # 查询实际存在的月份
                    actual_query = """
                        SELECT DISTINCT DATE_TRUNC('month', time)::date AS month
                        FROM disease_records
                        WHERE disease_id = :disease_id
                        ORDER BY month
                    """
                    actual_result = await db.execute(
                        text(actual_query), 
                        {"disease_id": did}
                    )
                    actual_months_set = {row[0] for row in actual_result.fetchall()}
                    
                    # 找出缺失的月份
                    missing_months = [m for m in expected_months if m not in actual_months_set]
                    
                    missing_info.append({
                        'disease_id': did,
                        'disease_name': name,
                        'disease_name_en': name_en,
                        'min_month': min_m,
                        'max_month': max_m,
                        'actual_months': actual,
                        'expected_months': expected_count,
                        'missing_count': len(missing_months),
                        'missing_months': missing_months
                    })
            
            logger.info(f"找到 {len(missing_info)} 个有缺失月份的疾病")
            return missing_info
    
    async def get_data_source_urls(
        self, 
        date_range: Optional[Tuple[date, date]] = None
    ) -> List[Dict]:
        """
        获取指定时间范围内的数据源URL
        
        Args:
            date_range: (开始日期, 结束日期)，如果为None则获取所有
            
        Returns:
            包含URL和元数据的列表
        """
        logger.info("正在查询数据源URL...")
        
        async with get_db() as db:
            query = """
                SELECT DISTINCT
                    time,
                    metadata->>'url' AS url,
                    metadata->>'source' AS source,
                    metadata->>'title' AS title,
                    data_source
                FROM disease_records
                WHERE country_id = (SELECT id FROM countries WHERE code = 'CN')
                  AND metadata->>'url' IS NOT NULL
            """
            
            if date_range:
                query += """
                  AND time >= :start_date
                  AND time <= :end_date
                """
            
            query += " ORDER BY time DESC"
            
            params = {}
            if date_range:
                params['start_date'] = date_range[0]
                params['end_date'] = date_range[1]
            
            result = await db.execute(text(query), params)
            urls_info = []
            
            for row in result.fetchall():
                time_val, url, source, title, data_source = row
                # 跳过无效的URL
                if url and url.lower() not in ['missing', 'null', 'none', '']:
                    urls_info.append({
                        'time': time_val,
                        'url': url,
                        'source': source or data_source,
                        'title': title or ''
                    })
            
            logger.info(f"找到 {len(urls_info)} 个有效数据源URL")
            if not urls_info and date_range:
                logger.warning(f"指定日期范围 {date_range[0]} ~ {date_range[1]} 内没有找到有效URL")
                logger.warning("建议：移除 --start-date 和 --end-date 参数，使用所有可用的URL")
            return urls_info
    
    async def fetch_and_parse_url(
        self, 
        url: str, 
        metadata: Optional[Dict] = None
    ) -> Optional[pd.DataFrame]:
        """
        访问URL并解析数据
        
        Args:
            url: 数据源URL
            metadata: 额外的元数据
            
        Returns:
            解析后的DataFrame，如果失败则返回None
        """
        # 检查URL有效性
        if not url or url.lower() in ['missing', 'null', 'none', '']:
            logger.warning(f"跳过无效URL: {url}")
            return None
        
        try:
            logger.info(f"正在访问: {url}")
            
            # 解析HTML
            parse_result = self.parser.parse(
                url,
                url=url,
                **metadata if metadata else {}
            )
            
            if not parse_result.success:
                logger.warning(f"解析失败: {parse_result.error_message}")
                return None
            
            if not parse_result.has_data:
                logger.warning("未找到数据")
                return None
            
            df = parse_result.data
            logger.info(f"解析成功，获得 {len(df)} 行数据")
            
            # 标准化疾病名称
            if self.disease_mapper:
                language = metadata.get('language', 'zh') if metadata else 'zh'
                
                # 确定疾病名称列（优先使用Diseases列，因为它包含中文名称）
                disease_col = None
                for col in ['Diseases', 'DiseasesCN', '疾病名称', 'Disease']:
                    if col in df.columns:
                        # 检查列中是否有非空值
                        if df[col].notna().any() and (df[col] != '').any():
                            disease_col = col
                            break
                
                if disease_col:
                    logger.info(f"正在标准化疾病名称（使用列: {disease_col}）...")
                    
                    # 使用本地映射字典（更快且支持英文名）
                    disease_ids = []
                    disease_names = []
                    db_ids = []
                    
                    for disease_name in df[disease_col]:
                        mapping_result = self.map_disease_name(str(disease_name))
                        
                        if mapping_result:
                            disease_ids.append(mapping_result['disease_name'])  # D004, D043
                            disease_names.append(mapping_result['local_name'])
                            db_ids.append(mapping_result['db_id'])
                        else:
                            disease_ids.append(None)
                            disease_names.append(None)
                            db_ids.append(None)
                    
                    df['disease_id'] = disease_ids  # D004, D043
                    df['disease_db_id'] = db_ids  # 数据库内部ID
                    df['mapped_name'] = disease_names
                    
                    mapped_count = df['disease_id'].notna().sum()
                    logger.info(f"成功映射 {mapped_count}/{len(df)} 个疾病名称")
            
            return df
            
        except Exception as e:
            logger.error(f"处理URL失败 {url}: {e}", exc_info=True)
            return None
    
    async def fetch_missing_data_by_disease(
        self, 
        disease_id: Optional[int] = None,
        date_range: Optional[Tuple[date, date]] = None,
        max_urls: Optional[int] = None
    ) -> List[pd.DataFrame]:
        """
        为指定疾病获取缺失数据
        
        Args:
            disease_id: 疾病ID，如果为None则处理所有有缺失的疾病
            date_range: 时间范围
            max_urls: 最多访问的URL数量
            
        Returns:
            解析成功的DataFrame列表
        """
        # 获取缺失信息
        missing_info = await self.get_missing_months_info(disease_id)
        
        if not missing_info:
            logger.info("未找到缺失数据的疾病")
            return []
        
        # 打印缺失信息摘要
        print("\n" + "=" * 70)
        print("缺失数据摘要")
        print("=" * 70)
        for info in missing_info:
            print(f"\n疾病: {info['disease_name']} (ID: {info['disease_id']})")
            print(f"  英文名: {info['disease_name_en']}")
            print(f"  时间范围: {info['min_month']} ~ {info['max_month']}")
            print(f"  实际月份: {info['actual_months']}, 期望: {info['expected_months']}, 缺失: {info['missing_count']}")
            print(f"  缺失月份数: {len(info['missing_months'])}")
            if info['missing_months']:
                # 显示前5个缺失月份
                sample_months = info['missing_months'][:5]
                months_str = ', '.join([m.strftime('%Y-%m') for m in sample_months])
                if len(info['missing_months']) > 5:
                    months_str += f" ... (还有 {len(info['missing_months']) - 5} 个)"
                print(f"  示例缺失月份: {months_str}")
        
        # 确定时间范围
        if not date_range and missing_info:
            # 使用缺失月份的时间范围
            all_missing = []
            for info in missing_info:
                all_missing.extend(info['missing_months'])
            
            if all_missing:
                min_date = min(all_missing)
                max_date = max(all_missing)
                date_range = (min_date, max_date)
                logger.info(f"使用缺失数据的时间范围: {min_date} ~ {max_date}")
        
        # 获取数据源URL
        urls_info = await self.get_data_source_urls(date_range)
        
        if not urls_info:
            logger.warning("未找到匹配的数据源URL")
            logger.info("提示：可以移除 --start-date 和 --end-date 参数，使用所有可用的URL")
            return []
        
        # 限制URL数量
        if max_urls and len(urls_info) > max_urls:
            logger.info(f"限制访问URL数量为 {max_urls} (总共 {len(urls_info)} 个)")
            urls_info = urls_info[:max_urls]
        else:
            logger.info(f"将访问所有 {len(urls_info)} 个URL")
        
        # 访问并解析URL
        print("\n" + "=" * 70)
        print("开始获取数据")
        print("=" * 70)
        
        results = []
        for i, url_info in enumerate(urls_info, 1):
            print(f"\n[{i}/{len(urls_info)}] 处理: {url_info['url']}")
            print(f"  来源: {url_info['source']}")
            print(f"  时间: {url_info.get('time', 'N/A')}")
            
            df = await self.fetch_and_parse_url(
                url_info['url'],
                metadata={
                    'source': url_info['source'],
                    'title': url_info.get('title', ''),
                    'time': url_info.get('time')
                }
            )
            
            if df is not None:
                # 填充缺失的Date字段（从metadata的time获取）
                if 'Date' in df.columns and (df['Date'].isna().all() or (df['Date'] == 'None').all()):
                    if url_info.get('time'):
                        df['Date'] = url_info['time']
                        logger.info(f"填充Date列为: {url_info['time']}")
                
                results.append(df)
                print(f"  ✓ 成功获取 {len(df)} 行数据")
                
                # 打印数据预览
                self._print_dataframe_preview(df, url_info)
            else:
                print(f"  ✗ 获取失败")
            
            # 添加延迟，避免请求过快
            if i < len(urls_info):
                await asyncio.sleep(2)
        
        return results
    
    def _print_dataframe_preview(self, df: pd.DataFrame, url_info: Dict):
        """打印DataFrame预览"""
        print("\n  数据预览:")
        print(f"  列: {list(df.columns)}")
        print(f"  形状: {df.shape}")
        
        # 如果有标准化的疾病数据，显示统计
        if 'standard_disease' in df.columns:
            mapped = df['standard_disease'].notna().sum()
            print(f"  已映射疾病: {mapped}/{len(df)}")
            
            # 显示前几个疾病
            if mapped > 0:
                disease_sample = df[df['standard_disease'].notna()][
                    ['standard_disease', 'disease_id']
                ].drop_duplicates().head(5)
                print("\n  疾病样例:")
                for _, row in disease_sample.iterrows():
                    print(f"    - {row['standard_disease']} (ID: {row['disease_id']})")
        
        # 显示数据样例
        if len(df) > 0:
            print("\n  前3行数据:")
            preview_cols = [c for c in df.columns if c not in ['metadata', 'raw_data']]
            print(df[preview_cols].head(3).to_string(index=False))    
    async def filter_missing_data(
        self,
        df: pd.DataFrame,
        missing_info: List[Dict]
    ) -> pd.DataFrame:
        """
        筛选出缺失疾病的数据（只保留D004 COVID-19和D043 Typhus等缺失疾病）
        
        Args:
            df: 原始数据DataFrame
            missing_info: 缺失月份信息列表
            
        Returns:
            只包含缺失疾病的DataFrame
        """
        if df.empty or not missing_info:
            return df
        
        # 获取缺失的疾病ID集合
        missing_disease_ids = set()
        for info in missing_info:
            disease_name = info['disease_name']  # 这是"D004"之类的ID
            missing_disease_ids.add(disease_name)
        
        logger.info(f"缺失疾病ID: {list(missing_disease_ids)}")
        self.missing_disease_ids = missing_disease_ids
        
        # 解析Date列为datetime（如果尚未解析）
        date_col = None
        if 'Date' in df.columns and df['Date'].notna().any():
            date_col = 'Date'
        elif 'YearMonthDay' in df.columns and df['YearMonthDay'].notna().any():
            date_col = 'YearMonthDay'
        elif 'time' in df.columns:
            date_col = 'time'
        
        if not date_col:
            logger.warning("没有找到有效的日期列，无法筛选缺失数据")
            return df
        
        logger.info(f"使用日期列: {date_col}")
        
        # 筛选只包含缺失疾病的数据
        df = df.copy()
        
        # 简单筛选：只保留disease_id在missing_disease_ids中的行
        if 'disease_id' in df.columns:
            filtered_df = df[df['disease_id'].isin(missing_disease_ids)].copy()
            logger.info(f"筛选完成: 从 {len(df)} 行中筛选出 {len(filtered_df)} 行缺失疾病的数据")
            
            # 显示筛选结果统计
            if not filtered_df.empty and 'disease_id' in filtered_df.columns:
                disease_counts = filtered_df['disease_id'].value_counts()
                logger.info("筛选后的疾病分布:")
                for disease_id, count in disease_counts.items():
                    logger.info(f"  {disease_id}: {count} 条")
            
            return filtered_df
        else:
            logger.warning("DataFrame中没有disease_id列，无法筛选")
            return df
    
    async def insert_to_database(
        self,
        df: pd.DataFrame,
        dry_run: bool = False
    ) -> int:
        """
        将数据写入disease_records表
        
        Args:
            df: 要插入的数据DataFrame
            dry_run: 是否只是测试而不实际写入
            
        Returns:
            成功插入的记录数
        """
        if df.empty:
            logger.info("没有数据需要插入")
            return 0
        
        logger.info(f"准备插入 {len(df)} 条记录到数据库...")
        
        async with get_db() as db:
            # 获取国家ID
            result = await db.execute(
                text("SELECT id FROM countries WHERE code = :code"),
                {"code": self.country_code}
            )
            country_row = result.fetchone()
            if not country_row:
                logger.error(f"未找到国家: {self.country_code}")
                return 0
            
            country_id = country_row[0]
            
            # 准备批量插入数据
            inserted = 0
            skipped = 0
            errors = 0
            
            for idx, row in df.iterrows():
                try:
                    # 提取字段
                    disease_id_str = row.get('disease_id')
                    if not disease_id_str or pd.isna(disease_id_str):
                        skipped += 1
                        continue
                    
                    # 从diseases表获取数据库ID
                    disease_result = await db.execute(
                        text("SELECT id FROM diseases WHERE name = :name"),
                        {"name": disease_id_str}
                    )
                    disease_row = disease_result.fetchone()
                    if not disease_row:
                        logger.debug(f"未找到疾病: {disease_id_str}")
                        skipped += 1
                        continue
                    
                    disease_db_id = disease_row[0]
                    
                    # 解析时间
                    time_val = None
                    if 'Date' in row and pd.notna(row['Date']):
                        time_val = pd.to_datetime(row['Date'], errors='coerce')
                    elif 'YearMonthDay' in row and pd.notna(row['YearMonthDay']):
                        time_val = pd.to_datetime(row['YearMonthDay'], errors='coerce')
                    
                    if pd.isna(time_val):
                        logger.debug(f"无法解析时间: {row.get('Date')} / {row.get('YearMonthDay')}")
                        skipped += 1
                        continue
                    
                    # 提取病例和死亡数
                    cases = None
                    deaths = None
                    
                    if 'Cases' in row and pd.notna(row['Cases']):
                        try:
                            cases = int(float(str(row['Cases']).replace(',', '')))
                        except (ValueError, TypeError):
                            pass
                    
                    if 'Deaths' in row and pd.notna(row['Deaths']):
                        try:
                            deaths = int(float(str(row['Deaths']).replace(',', '')))
                        except (ValueError, TypeError):
                            pass
                    
                    # 提取发病率和死亡率
                    incidence = None
                    mortality = None
                    
                    if 'Incidence' in row and pd.notna(row['Incidence']):
                        try:
                            inc_val = float(str(row['Incidence']).replace(',', ''))
                            if inc_val >= 0:  # 只接受非负值，-10表示缺失
                                incidence = inc_val
                        except (ValueError, TypeError):
                            pass
                    
                    if 'Mortality' in row and pd.notna(row['Mortality']):
                        try:
                            mort_val = float(str(row['Mortality']).replace(',', ''))
                            if mort_val >= 0:
                                mortality = mort_val
                        except (ValueError, TypeError):
                            pass
                    
                    # 构建元数据
                    metadata = {}
                    if 'URL' in row and pd.notna(row['URL']):
                        metadata['url'] = str(row['URL'])
                    if 'Source' in row and pd.notna(row['Source']):
                        metadata['original_source'] = str(row['Source'])
                    if 'DOI' in row and pd.notna(row['DOI']):
                        metadata['doi'] = str(row['DOI'])
                    
                    # 数据来源
                    data_source = row.get('Source', 'GOV Data')
                    
                    if dry_run:
                        logger.debug(
                            f"[DRY RUN] 将插入: time={time_val.date()}, "
                            f"disease_id={disease_db_id}, cases={cases}, deaths={deaths}"
                        )
                        inserted += 1
                    else:
                        # 只插入缺失的数据，不覆盖已存在的记录
                        import json
                        
                        # 先检查记录是否存在
                        check_result = await db.execute(text("""
                            SELECT 1 FROM disease_records
                            WHERE time = :time
                              AND disease_id = :disease_id
                              AND country_id = :country_id
                        """), {
                            'time': time_val,
                            'disease_id': disease_db_id,
                            'country_id': country_id
                        })
                        
                        exists = check_result.fetchone() is not None
                        
                        if exists:
                            # 记录已存在，跳过
                            logger.debug(
                                f"记录已存在，跳过: time={time_val.date()}, "
                                f"disease_id={disease_db_id}"
                            )
                            skipped += 1
                        else:
                            # 记录不存在，插入新数据
                            await db.execute(text("""
                                INSERT INTO disease_records (
                                    time, disease_id, country_id,
                                    cases, deaths,
                                    incidence_rate, mortality_rate,
                                    data_source, metadata
                                ) VALUES (
                                    :time, :disease_id, :country_id,
                                    :cases, :deaths,
                                    :incidence_rate, :mortality_rate,
                                    :data_source, CAST(:metadata AS jsonb)
                                )
                            """), {
                                'time': time_val,
                                'disease_id': disease_db_id,
                                'country_id': country_id,
                                'cases': cases,
                                'deaths': deaths,
                                'incidence_rate': incidence,
                                'mortality_rate': mortality,
                                'data_source': data_source,
                                'metadata': json.dumps(metadata) if metadata else '{}'
                            })
                            inserted += 1
                
                except Exception as e:
                    logger.error(f"插入记录失败: {e}")
                    errors += 1
                    continue
            
            if not dry_run:
                await db.commit()
                logger.info(f"✓ 成功插入 {inserted} 条缺失记录")
            else:
                logger.info(f"[DRY RUN] 模拟插入 {inserted} 条记录")
            
            if skipped > 0:
                logger.info(f"跳过 {skipped} 条记录（已存在或缺少必要字段）")
            if errors > 0:
                logger.warning(f"失败 {errors} 条记录")
            
            return inserted

async def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="获取中国疾病数据缺失月份的数据"
    )
    parser.add_argument(
        '--disease-id',
        type=int,
        help='指定疾病ID（不指定则处理所有有缺失的疾病）'
    )
    parser.add_argument(
        '--start-date',
        help='开始日期 (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        help='结束日期 (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--max-urls',
        type=int,
        default=None,
        help='最多访问的URL数量（默认不限制）'
    )
    parser.add_argument(
        '--output',
        help='输出CSV文件路径（可选）'
    )
    parser.add_argument(
        '--write-db',
        action='store_true',
        help='将数据写入数据库'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='测试模式，不实际写入数据库'
    )
    parser.add_argument(
        '--filter-missing',
        action='store_true',
        help='只保留缺失月份的数据'
    )
    
    args = parser.parse_args()
    
    # 解析日期范围
    date_range = None
    if args.start_date and args.end_date:
        try:
            start = datetime.strptime(args.start_date, '%Y-%m-%d').date()
            end = datetime.strptime(args.end_date, '%Y-%m-%d').date()
            date_range = (start, end)
            logger.info(f"使用指定的日期范围: {start} ~ {end}")
        except ValueError as e:
            logger.error(f"日期格式错误: {e}")
            return
    
    # 创建获取器
    fetcher = MissingDataFetcher(country_code="CN")
    await fetcher.initialize()
    
    # 获取缺失信息（用于筛选）
    missing_info = await fetcher.get_missing_months_info(args.disease_id)
    
    # 获取数据
    results = await fetcher.fetch_missing_data_by_disease(
        disease_id=args.disease_id,
        date_range=date_range,
        max_urls=args.max_urls
    )
    
    # 合并结果
    if results:
        print("\n" + "=" * 70)
        print("获取结果汇总")
        print("=" * 70)
        print(f"成功从 {len(results)} 个URL获取数据")
        
        # 合并所有DataFrame
        combined_df = pd.concat(results, ignore_index=True)
        print(f"总共获得 {len(combined_df)} 行数据")
        
        # 筛选缺失数据
        if args.filter_missing and missing_info:
            print("\n筛选缺失月份的数据...")
            filtered_df = await fetcher.filter_missing_data(combined_df, missing_info)
            print(f"筛选后剩余 {len(filtered_df)} 行数据（只包含缺失月份）")
            combined_df = filtered_df
        
        # 如果指定了输出文件，保存结果
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            combined_df.to_csv(output_path, index=False, encoding='utf-8-sig')
            print(f"\n✓ 数据已保存到: {output_path}")
        
        # 显示统计信息
        if 'standard_disease' in combined_df.columns:
            disease_stats = combined_df['standard_disease'].value_counts()
            print(f"\n疾病数据统计（前10）:")
            for disease, count in disease_stats.head(10).items():
                print(f"  {disease}: {count} 条记录")
        
        # 写入数据库
        if args.write_db and not combined_df.empty:
            print("\n" + "=" * 70)
            print("写入数据库（只插入缺失记录，不覆盖已有数据）")
            print("=" * 70)
            
            if args.dry_run:
                print("[DRY RUN] 测试模式，不会实际写入数据")
            
            inserted_count = await fetcher.insert_to_database(
                combined_df,
                dry_run=args.dry_run
            )
            
            if not args.dry_run:
                print(f"\n✓ 成功插入 {inserted_count} 条缺失记录到数据库")
            else:
                print(f"\n[DRY RUN] 模拟写入 {inserted_count} 条记录")
    else:
        print("\n未能获取任何数据")


if __name__ == "__main__":
    asyncio.run(main())
