#!/usr/bin/env python3
"""
完整重建数据库 - 一体式脚本

功能：
1. 清空所有疾病相关表
2. 从 CSV 导入标准疾病库和映射关系
3. 同步 diseases 表
4. 导入历史数据到 disease_records
5. 验证数据完整性

一次运行，全部完成！
"""
import asyncio
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
from typing import Dict, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.core.database import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)


class DatabaseRebuilder:
    def __init__(self, auto_confirm=False):
        self.standard_file = ROOT / "configs/standard_diseases.csv"
        self.mapping_file = ROOT / "configs/cn/disease_mapping.csv"
        self.history_file = ROOT / "data/processed/history_merged.csv"
        self.country_code = "CN"
        self.auto_confirm = auto_confirm
        
    async def run(self):
        """执行完整的数据库重建流程"""
        logger.info("=" * 80)
        logger.info("🚀 完整数据库重建流程")
        logger.info("=" * 80)
        
        # 显示警告和统计信息
        async with get_db() as db:
            await self._show_warning_and_stats(db)
            
            # 询问确认
            if not self.auto_confirm:
                if not self._confirm_rebuild():
                    logger.info("❌ 用户取消操作")
                    return
            
            logger.info("\n" + "=" * 80)
            logger.info("开始执行数据库重建...")
            logger.info("=" * 80)
            
            # 步骤 1: 清空数据
            await self.clear_data(db)
            
            # 步骤 2: 导入标准疾病库
            await self.import_standard_diseases(db)
            
            # 步骤 3: 导入疾病映射
            await self.import_disease_mappings(db)
            
            # 步骤 4: 同步 diseases 表
            await self.sync_diseases_table(db)
            
            # 步骤 5: 导入历史数据
            await self.import_history_data(db)
            
            # 步骤 6: 验证结果
            await self.verify_results(db)
            
        logger.info("\n" + "=" * 80)
        logger.info("✅ 数据库重建完成！")
        logger.info("=" * 80)
    
    async def _show_warning_and_stats(self, db):
        """显示警告信息和当前数据统计"""
        logger.warning("\n⚠️  警告：此操作将清空以下数据表：")
        logger.warning("   • disease_records (疾病记录)")
        logger.warning("   • diseases (疾病)")
        logger.warning("   • disease_mappings (疾病映射)")
        logger.warning("   • standard_diseases (标准疾病库)")
        
        logger.info("\n📊 当前数据统计：")
        
        tables = {
            "disease_records": "疾病记录",
            "diseases": "疾病",
            "disease_mappings": "疾病映射",
            "standard_diseases": "标准疾病"
        }
        
        for table, name in tables.items():
            try:
                result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                logger.info(f"   • {name:12s}: {count:,} 条")
            except Exception:
                logger.info(f"   • {name:12s}: (表不存在)")
        
        logger.info("\n📥 将要导入：")
        logger.info(f"   • 标准疾病库: {self.standard_file.name}")
        logger.info(f"   • 疾病映射: {self.mapping_file.name}")
        logger.info(f"   • 历史数据: {self.history_file.name}")
        
    def _confirm_rebuild(self):
        """询问用户确认"""
        logger.info("\n" + "=" * 80)
        try:
            response = input("🔔 确认要继续吗？所有现有数据将被删除！(yes/no): ")
            return response.lower() in ('yes', 'y')
        except (KeyboardInterrupt, EOFError):
            print()  # 新行
            return False
    
    async def clear_data(self, db):
        """清空所有疾病相关数据"""
        logger.info("\n📦 步骤 1/6: 清空现有数据...")
        
        # 按照外键依赖顺序删除
        tables = [
            "disease_records",
            "diseases", 
            "disease_mappings",
            "standard_diseases"
        ]
        
        for table in tables:
            result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            
            await db.execute(text(f"DELETE FROM {table}"))
            logger.info(f"  ✓ 清空 {table}: 删除 {count} 条记录")
        
        await db.commit()
        logger.info("✓ 数据清空完成")
    
    async def import_standard_diseases(self, db):
        """导入标准疾病库"""
        logger.info("\n📚 步骤 2/6: 导入标准疾病库...")
        
        if not self.standard_file.exists():
            raise FileNotFoundError(f"标准疾病文件不存在: {self.standard_file}")
        
        df = pd.read_csv(self.standard_file).fillna('')
        logger.info(f"  读取 {len(df)} 条标准疾病")
        
        # 调整 category 列允许 NULL
        await db.execute(text("""
            ALTER TABLE standard_diseases 
            ALTER COLUMN category DROP NOT NULL
        """))
        
        inserted = 0
        for _, row in df.iterrows():
            await db.execute(text("""
                INSERT INTO standard_diseases 
                (disease_id, standard_name_en, standard_name_zh, category, icd_10, icd_11, 
                 description, source, is_active)
                VALUES 
                (:disease_id, :name_en, :name_zh, :category, :icd_10, :icd_11, 
                 :description, :source, true)
                ON CONFLICT (disease_id) DO UPDATE SET
                    standard_name_en = EXCLUDED.standard_name_en,
                    standard_name_zh = EXCLUDED.standard_name_zh,
                    category = EXCLUDED.category,
                    icd_10 = EXCLUDED.icd_10,
                    icd_11 = EXCLUDED.icd_11,
                    description = EXCLUDED.description,
                    source = EXCLUDED.source,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                'disease_id': row['disease_id'],
                'name_en': row['standard_name_en'],
                'name_zh': row['standard_name_zh'],
                'category': row['category'] if row['category'] else None,
                'icd_10': row.get('icd_10', ''),
                'icd_11': row.get('icd_11', ''),
                'description': row.get('description', ''),
                'source': row.get('source', 'Manual')
            })
            inserted += 1
        
        await db.commit()
        logger.info(f"✓ 导入 {inserted} 条标准疾病")
    
    async def import_disease_mappings(self, db):
        """导入疾病映射关系"""
        logger.info("\n🗺️  步骤 3/6: 导入疾病映射...")
        
        if not self.mapping_file.exists():
            raise FileNotFoundError(f"映射文件不存在: {self.mapping_file}")
        
        df = pd.read_csv(self.mapping_file).fillna('')
        logger.info(f"  读取 {len(df)} 条映射关系")
        
        # 调整 category 列允许 NULL
        await db.execute(text("""
            ALTER TABLE disease_mappings 
            ALTER COLUMN category DROP NOT NULL
        """))
        
        inserted = 0
        for _, row in df.iterrows():
            disease_id = row['disease_id']
            local_name = row['local_name']
            
            # 主要名称
            await db.execute(text("""
                INSERT INTO disease_mappings 
                (disease_id, country_code, local_name, is_primary, is_alias, priority, 
                 category, source, is_active)
                VALUES 
                (:disease_id, :country, :local_name, true, false, 100, 
                 :category, :source, true)
                ON CONFLICT (disease_id, country_code, local_name) DO UPDATE SET
                    is_primary = true,
                    category = EXCLUDED.category,
                    source = EXCLUDED.source,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                'disease_id': disease_id,
                'country': self.country_code,
                'local_name': local_name,
                'category': row['category'] if row['category'] else None,
                'source': row.get('data_source', row.get('source', 'Manual'))
            })
            inserted += 1
            
            # 别名
            if row.get('aliases'):
                aliases = [a.strip() for a in str(row['aliases']).split(',') if a.strip()]
                for alias in aliases:
                    await db.execute(text("""
                        INSERT INTO disease_mappings 
                        (disease_id, country_code, local_name, is_primary, is_alias, priority,
                         category, source, is_active)
                        VALUES 
                        (:disease_id, :country, :alias, false, true, 50,
                         :category, :source, true)
                        ON CONFLICT (disease_id, country_code, local_name) DO UPDATE SET
                            is_alias = true,
                            updated_at = CURRENT_TIMESTAMP
                    """), {
                        'disease_id': disease_id,
                        'country': self.country_code,
                        'alias': alias,
                        'category': row['category'] if row['category'] else None,
                        'source': row.get('source', 'Manual')
                    })
                    inserted += 1
        
        await db.commit()
        logger.info(f"✓ 导入 {inserted} 条映射关系")
    
    async def sync_diseases_table(self, db):
        """同步 diseases 表"""
        logger.info("\n🔄 步骤 4/6: 同步 diseases 表...")
        
        # 从 standard_diseases 导入到 diseases
        result = await db.execute(text("""
            INSERT INTO diseases (name, name_en, category, icd_10, icd_11, description, 
                                aliases, keywords, metadata, is_active, created_at, updated_at)
            SELECT 
                disease_id,
                standard_name_en,
                COALESCE(category, 'Other'),
                icd_10,
                icd_11,
                description,
                '[]'::json,
                '[]'::json,
                '{}'::json,
                is_active,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM standard_diseases
            ON CONFLICT (name) DO UPDATE SET
                name_en = EXCLUDED.name_en,
                category = EXCLUDED.category,
                icd_10 = EXCLUDED.icd_10,
                icd_11 = EXCLUDED.icd_11,
                description = EXCLUDED.description,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
        """))
        
        count = len(result.fetchall())
        await db.commit()
        logger.info(f"✓ 同步 {count} 条疾病到 diseases 表")
    
    async def import_history_data(self, db):
        """导入历史数据（包含完整字段：data_source, incidence_rate, metadata等）"""
        logger.info("\n📊 步骤 5/6: 导入历史数据...")
        
        if not self.history_file.exists():
            logger.warning(f"历史数据文件不存在: {self.history_file}")
            return
        
        # 读取历史数据
        df = pd.read_csv(self.history_file)
        logger.info(f"  读取 {len(df)} 条历史记录")
        
        # 获取中国的 country_id
        result = await db.execute(text("SELECT id FROM countries WHERE code = 'CN'"))
        country_row = result.fetchone()
        if not country_row:
            logger.error("未找到中国的 country_id")
            return
        country_id = country_row[0]
        
        # 构建映射字典（支持归一化匹配）
        result = await db.execute(text("""
            SELECT dm.local_name, d.id
            FROM disease_mappings dm
            JOIN diseases d ON dm.disease_id = d.name
            WHERE dm.country_code = 'CN' AND dm.is_active = true
        """))
        
        # 使用归一化键提高匹配容错性
        def _norm(s):
            try:
                return s.strip().lower()
            except Exception:
                return None
        
        mapping_dict = {}
        for row in result:
            local_name = row[0]
            db_id = row[1]
            normalized = _norm(local_name)
            if normalized:
                mapping_dict[normalized] = db_id
        
        logger.info(f"  加载 {len(mapping_dict)} 个疾病映射（归一化）")
        
        # 确定列名
        date_col = self._find_column(df, ['Date', 'date', 'time', 'Time', 'YearMonthDay'])
        disease_cn_col = self._find_column(df, ['DiseasesCN', 'disease_cn', '疾病名称', '病名'])
        disease_en_col = self._find_column(df, ['Diseases', 'disease_en', 'Disease'])
        cases_col = self._find_column(df, ['Cases', 'cases', 'case', '发病数'])
        deaths_col = self._find_column(df, ['Deaths', 'deaths', 'death', '死亡数'])
        
        if not all([date_col, disease_cn_col, cases_col, deaths_col]):
            logger.error("CSV 缺少必要列")
            return
        
        # 批量导入数据（包含完整字段）
        inserted = 0
        skipped = 0
        batch_size = 1000
        batch_data = []
        
        for idx, row in df.iterrows():
            try:
                # 提取基本字段
                disease_cn = str(row[disease_cn_col]) if pd.notna(row[disease_cn_col]) else None
                if not disease_cn or disease_cn == 'nan':
                    skipped += 1
                    continue
                
                disease_en = str(row[disease_en_col]) if disease_en_col and pd.notna(row[disease_en_col]) else None
                
                # 查找映射（使用归一化）
                db_disease_id = None
                if disease_en:
                    db_disease_id = mapping_dict.get(_norm(disease_en))
                if not db_disease_id:
                    db_disease_id = mapping_dict.get(_norm(disease_cn))
                
                if not db_disease_id:
                    skipped += 1
                    continue
                
                # 解析日期
                date_str = str(row[date_col])
                try:
                    if '/' in date_str:
                        date_obj = datetime.strptime(date_str, '%Y/%m/%d')
                    else:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                except:
                    skipped += 1
                    continue
                
                # 提取数值
                cases = int(row[cases_col]) if pd.notna(row[cases_col]) and str(row[cases_col]) not in ['', '-10', 'nan'] else 0
                deaths = int(row[deaths_col]) if pd.notna(row[deaths_col]) and str(row[deaths_col]) not in ['', '-10', 'nan'] else 0
                
                # 提取额外字段
                incidence = None
                if 'Incidence' in df.columns and pd.notna(row['Incidence']):
                    val = float(row['Incidence'])
                    incidence = val if val >= 0 else None
                
                mortality = None
                if 'Mortality' in df.columns and pd.notna(row['Mortality']):
                    val = float(row['Mortality'])
                    mortality = val if val >= 0 else None
                
                region = None
                if 'ProvinceCN' in df.columns and pd.notna(row['ProvinceCN']) and str(row['ProvinceCN']) != '全国':
                    region = str(row['ProvinceCN'])
                elif 'Province' in df.columns and pd.notna(row['Province']) and str(row['Province']) != 'China':
                    region = str(row['Province'])
                
                # 真实的数据来源
                data_source = 'Historical Data Import'
                if 'Source' in df.columns and pd.notna(row['Source']):
                    data_source = str(row['Source'])
                
                # 构建 metadata
                metadata_obj = {
                    'source_csv': self.history_file.name,
                    'row_index': int(idx)
                }
                
                if '__source_file' in df.columns and pd.notna(row.get('__source_file')):
                    metadata_obj['source_file'] = str(row['__source_file'])
                if 'DOI' in df.columns and pd.notna(row['DOI']):
                    metadata_obj['doi'] = str(row['DOI'])
                if 'URL' in df.columns and pd.notna(row['URL']):
                    metadata_obj['url'] = str(row['URL'])
                if 'ADCode' in df.columns and pd.notna(row['ADCode']):
                    metadata_obj['adcode'] = str(int(row['ADCode']))
                
                batch_data.append({
                    'time': date_obj,
                    'disease_id': db_disease_id,
                    'country_id': country_id,
                    'cases': max(0, cases),
                    'deaths': max(0, deaths),
                    'incidence_rate': incidence,
                    'mortality_rate': mortality,
                    'region': region,
                    'data_source': data_source,
                    'metadata': json.dumps(metadata_obj)
                })
                
                # 批量插入
                if len(batch_data) >= batch_size:
                    inserted += await self._batch_insert_enhanced(db, batch_data)
                    batch_data = []
                    
                    if inserted % 5000 == 0:
                        logger.info(f"  已导入 {inserted} 条记录...")
                        
            except Exception as e:
                skipped += 1
                continue
        
        # 插入剩余数据
        if batch_data:
            inserted += await self._batch_insert_enhanced(db, batch_data)
        
        await db.commit()
        logger.info(f"✓ 导入 {inserted} 条历史记录 (跳过 {skipped} 条)")
    
    async def _batch_insert(self, db, batch_data):
        """批量插入数据"""
        if not batch_data:
            return 0
        
        try:
            # 使用 executemany 批量插入
            await db.execute(text("""
                INSERT INTO disease_records 
                (time, disease_id, country_id, cases, deaths, new_cases, new_deaths,
                 recoveries, active_cases, new_recoveries, metadata)
                VALUES 
                (:time, :disease_id, :country_id, :cases, :deaths, 0, 0, 0, 0, 0, :metadata)
                ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                    cases = EXCLUDED.cases, 
                    deaths = EXCLUDED.deaths
            """), batch_data)
            return len(batch_data)
        except Exception as e:
            logger.warning(f"批量插入失败，尝试单条插入: {str(e)[:200]}")
            # 回滚当前事务
            await db.rollback()
            # 回退到单条插入
            success = 0
            for data in batch_data:
                try:
                    await db.execute(text("""
                        INSERT INTO disease_records 
                        (time, disease_id, country_id, cases, deaths, new_cases, new_deaths,
                         recoveries, active_cases, new_recoveries, metadata)
                        VALUES 
                        (:time, :disease_id, :country_id, :cases, :deaths, 0, 0, 0, 0, 0, :metadata)
                        ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                            cases = EXCLUDED.cases, deaths = EXCLUDED.deaths
                    """), data)
                    success += 1
                except Exception as inner_e:
                    await db.rollback()
                    continue
            return success
    
    async def _batch_insert_enhanced(self, db, batch_data):
        """批量插入数据（包含完整字段）"""
        if not batch_data:
            return 0
        
        try:
            # 使用 executemany 批量插入（包含所有字段）
            await db.execute(text("""
                INSERT INTO disease_records 
                (time, disease_id, country_id, cases, deaths, 
                 incidence_rate, mortality_rate, region, data_source,
                 new_cases, new_deaths, recoveries, active_cases, new_recoveries, 
                 metadata)
                VALUES 
                (:time, :disease_id, :country_id, :cases, :deaths, 
                 :incidence_rate, :mortality_rate, :region, :data_source,
                 0, 0, 0, 0, 0, :metadata)
                ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                    cases = EXCLUDED.cases, 
                    deaths = EXCLUDED.deaths,
                    incidence_rate = EXCLUDED.incidence_rate,
                    mortality_rate = EXCLUDED.mortality_rate,
                    region = EXCLUDED.region,
                    data_source = EXCLUDED.data_source,
                    metadata = EXCLUDED.metadata
            """), batch_data)
            return len(batch_data)
        except Exception as e:
            logger.warning(f"批量插入失败，尝试单条插入: {str(e)[:200]}")
            # 回滚当前事务
            await db.rollback()
            # 回退到单条插入
            success = 0
            for data in batch_data:
                try:
                    await db.execute(text("""
                        INSERT INTO disease_records 
                        (time, disease_id, country_id, cases, deaths, 
                         incidence_rate, mortality_rate, region, data_source,
                         new_cases, new_deaths, recoveries, active_cases, new_recoveries, 
                         metadata)
                        VALUES 
                        (:time, :disease_id, :country_id, :cases, :deaths, 
                         :incidence_rate, :mortality_rate, :region, :data_source,
                         0, 0, 0, 0, 0, :metadata)
                        ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                            cases = EXCLUDED.cases, 
                            deaths = EXCLUDED.deaths,
                            incidence_rate = EXCLUDED.incidence_rate,
                            mortality_rate = EXCLUDED.mortality_rate,
                            region = EXCLUDED.region,
                            data_source = EXCLUDED.data_source,
                            metadata = EXCLUDED.metadata
                    """), data)
                    success += 1
                except Exception as inner_e:
                    await db.rollback()
                    continue
            return success
    
    def _find_column(self, df, candidates):
        """查找列名"""
        for col in candidates:
            if col in df.columns:
                return col
        return None
    
    async def verify_results(self, db):
        """验证导入结果"""
        logger.info("\n✅ 步骤 6/6: 验证数据...")
        
        # 标准疾病数
        result = await db.execute(text("SELECT COUNT(*) FROM standard_diseases"))
        std_count = result.scalar()
        logger.info(f"  • 标准疾病库: {std_count} 条")
        
        # 映射关系数
        result = await db.execute(text("""
            SELECT COUNT(*), COUNT(DISTINCT disease_id) 
            FROM disease_mappings WHERE country_code = 'CN'
        """))
        map_total, map_diseases = result.fetchone()
        logger.info(f"  • 疾病映射: {map_total} 条映射，覆盖 {map_diseases} 个疾病")
        
        # diseases 表
        result = await db.execute(text("SELECT COUNT(*) FROM diseases"))
        diseases_count = result.scalar()
        logger.info(f"  • Diseases 表: {diseases_count} 条")
        
        # 历史记录
        result = await db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT disease_id) as diseases,
                MIN(time) as earliest,
                MAX(time) as latest
            FROM disease_records
        """))
        rec = result.fetchone()
        logger.info(f"  • 历史记录: {rec[0]} 条")
        logger.info(f"  • 覆盖疾病: {rec[1]} 个")
        logger.info(f"  • 时间范围: {rec[2]} 至 {rec[3]}")
        
        # 痢疾数据验证
        result = await db.execute(text("""
            SELECT d.name, sd.standard_name_zh, COUNT(*) as cnt
            FROM disease_records r
            JOIN diseases d ON r.disease_id = d.id
            LEFT JOIN standard_diseases sd ON d.name = sd.disease_id
            WHERE d.name = 'D024'
            GROUP BY d.name, sd.standard_name_zh
        """))
        dysentery = result.fetchone()
        if dysentery:
            logger.info(f"\n  🎯 痢疾数据: {dysentery[1]} ({dysentery[0]}) - {dysentery[2]} 条记录")


async def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='完整重建数据库（清空现有数据并重新导入）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 交互式运行（会询问确认）
  python scripts/full_rebuild_database.py
  
  # 自动确认（跳过询问）
  python scripts/full_rebuild_database.py --yes
  
注意：此操作会清空所有疾病相关数据，请谨慎使用！
        """
    )
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='自动确认，跳过询问（用于自动化脚本）'
    )
    
    args = parser.parse_args()
    
    try:
        rebuilder = DatabaseRebuilder(auto_confirm=args.yes)
        await rebuilder.run()
    except Exception as e:
        logger.error(f"❌ 重建失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
