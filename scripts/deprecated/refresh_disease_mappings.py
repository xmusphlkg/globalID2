#!/usr/bin/env python3
"""
刷新疾病映射数据库

完整重建疾病标准库和映射表，用于修复数据不一致或更新配置文件后同步数据库。

功能：
1. 清理现有的 standard_diseases 和 disease_mappings 表
2. 从 configs/standard_diseases.csv 重新导入标准疾病库
3. 从 configs/{country}/disease_mapping.csv 重新导入国家映射
4. 验证数据完整性和一致性
5. 生成详细的导入报告

使用方法：
    python scripts/refresh_disease_mappings.py
    python scripts/refresh_disease_mappings.py --country cn
    python scripts/refresh_disease_mappings.py --dry-run  # 仅验证不执行
"""
import asyncio
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set
import pandas as pd

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.core.database import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)


class DiseaseRefreshService:
    """疾病映射刷新服务"""
    
    def __init__(self, country_code: str = "cn", dry_run: bool = False):
        self.country_code = country_code.upper()
        self.dry_run = dry_run
        self.stats = {
            "standard_diseases_added": 0,
            "standard_diseases_updated": 0,
            "mappings_added": 0,
            "mappings_updated": 0,
            "errors": [],
            "warnings": []
        }
        
    async def refresh_all(self, skip_confirmation: bool = False):
        """执行完整的刷新流程"""
        logger.info("=" * 70)
        logger.info("疾病映射数据库刷新工具")
        logger.info("=" * 70)
        
        if self.dry_run:
            logger.info("🔍 DRY RUN 模式 - 仅验证不执行实际操作")
        
        # 1. 验证文件存在
        logger.info("\n📋 步骤 1/5: 验证配置文件...")
        if not await self._validate_files():
            return False
        
        # 2. 确认操作
        if not skip_confirmation and not self.dry_run:
            logger.warning("\n⚠️  警告：此操作将删除并重建 standard_diseases 和 disease_mappings 表！")
            response = input("确认继续? 输入 'YES' 继续: ")
            if response != "YES":
                logger.info("操作已取消")
                return False
        
        # 3. 备份现有数据（可选）
        logger.info("\n💾 步骤 2/5: 备份现有数据...")
        await self._backup_existing_data()
        
        # 4. 清理现有表
        logger.info("\n🗑️  步骤 3/5: 清理现有数据...")
        if not self.dry_run:
            await self._clear_tables()
        else:
            logger.info("  (跳过 - DRY RUN)")
        
        # 5. 导入标准疾病库
        logger.info("\n📥 步骤 4/5: 导入标准疾病库...")
        await self._import_standard_diseases()
        
        # 6. 导入国家映射
        logger.info(f"\n🌏 步骤 5/5: 导入 {self.country_code} 国家映射...")
        await self._import_country_mappings()
        
        # 7. 验证完整性
        logger.info("\n✅ 验证数据完整性...")
        await self._validate_integrity()
        
        # 8. 生成报告
        logger.info("\n" + "=" * 70)
        self._print_summary()
        logger.info("=" * 70)
        
        return len(self.stats["errors"]) == 0
    
    async def _validate_files(self) -> bool:
        """验证所需的配置文件是否存在"""
        standard_file = ROOT / "configs/standard_diseases.csv"
        mapping_file = ROOT / f"configs/{self.country_code.lower()}/disease_mapping.csv"
        
        issues = []
        
        if not standard_file.exists():
            issues.append(f"❌ 找不到标准疾病库文件: {standard_file}")
        else:
            df = pd.read_csv(standard_file)
            logger.info(f"  ✓ 标准疾病库文件: {len(df)} 条记录")
            
            # 验证必需列
            required_cols = ['disease_id', 'standard_name_en', 'standard_name_zh']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                issues.append(f"❌ 标准疾病库缺少必需列: {missing_cols}")
        
        if not mapping_file.exists():
            issues.append(f"❌ 找不到国家映射文件: {mapping_file}")
        else:
            df = pd.read_csv(mapping_file)
            logger.info(f"  ✓ 国家映射文件: {len(df)} 条记录")
            
            # 验证必需列
            required_cols = ['disease_id', 'local_name']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                issues.append(f"❌ 国家映射缺少必需列: {missing_cols}")
        
        if issues:
            for issue in issues:
                logger.error(issue)
            return False
        
        return True
    
    async def _backup_existing_data(self):
        """备份现有数据到 CSV"""
        backup_dir = ROOT / "data/backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        try:
            async with get_db() as db:
                # 备份 standard_diseases
                result = await db.execute(text("SELECT * FROM standard_diseases"))
                rows = result.fetchall()
                if rows:
                    df = pd.DataFrame(rows, columns=result.keys())
                    backup_file = backup_dir / f"standard_diseases_backup_{timestamp}.csv"
                    df.to_csv(backup_file, index=False)
                    logger.info(f"  ✓ 备份标准疾病库: {len(df)} 条记录 -> {backup_file.name}")
                
                # 备份 disease_mappings
                result = await db.execute(text(
                    f"SELECT * FROM disease_mappings WHERE country_code = '{self.country_code}'"
                ))
                rows = result.fetchall()
                if rows:
                    df = pd.DataFrame(rows, columns=result.keys())
                    backup_file = backup_dir / f"disease_mappings_{self.country_code}_backup_{timestamp}.csv"
                    df.to_csv(backup_file, index=False)
                    logger.info(f"  ✓ 备份国家映射: {len(df)} 条记录 -> {backup_file.name}")
        except Exception as e:
            logger.warning(f"  ⚠️  备份失败（可能表不存在）: {e}")
    
    async def _clear_tables(self):
        """清空相关表"""
        async with get_db() as db:
            # 删除映射（有外键依赖）- 同时清理大小写变体
            await db.execute(text(f"""
                DELETE FROM disease_mappings 
                WHERE UPPER(country_code) = '{self.country_code}'
            """))
            
            # 删除标准疾病
            await db.execute(text("DELETE FROM standard_diseases"))
            
            await db.commit()
            logger.info("  ✓ 已清空表数据")
    
    async def _import_standard_diseases(self):
        """导入标准疾病库"""
        standard_file = ROOT / "configs/standard_diseases.csv"
        df = pd.read_csv(standard_file).fillna('')
        
        async with get_db() as db:
            # 确保表存在并允许 category 为 NULL
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS standard_diseases (
                    id SERIAL PRIMARY KEY,
                    disease_id VARCHAR(20) UNIQUE NOT NULL,
                    standard_name_en VARCHAR(200) NOT NULL,
                    standard_name_zh VARCHAR(200),
                    category VARCHAR(50),
                    icd_10 VARCHAR(50),
                    icd_11 VARCHAR(50),
                    description TEXT,
                    source VARCHAR(100),
                    is_active BOOLEAN DEFAULT true,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # 如果表已存在，确保 category 列允许 NULL
            await db.execute(text("""
                ALTER TABLE standard_diseases 
                ALTER COLUMN category DROP NOT NULL
            """))
            await db.commit()
            
            # 批量插入
            for idx, row in df.iterrows():
                try:
                    disease_id = str(row['disease_id']).strip()
                    standard_name_en = str(row['standard_name_en']).strip()
                    standard_name_zh = str(row.get('standard_name_zh', '')).strip()
                    category = str(row.get('category', '')).strip()
                    icd_10 = str(row.get('icd_10', '')).strip() if pd.notna(row.get('icd_10')) and row.get('icd_10') else None
                    icd_11 = str(row.get('icd_11', '')).strip() if pd.notna(row.get('icd_11')) and row.get('icd_11') else None
                    description = str(row.get('description', '')).strip() if pd.notna(row.get('description')) and row.get('description') else None
                    source = str(row.get('source', 'CSV')).strip()
                    
                    if not disease_id or not standard_name_en:
                        logger.warning(f"  ⚠️  跳过无效记录 (行 {idx+2}): disease_id 或 standard_name_en 为空")
                        continue
                    
                    if not self.dry_run:
                        await db.execute(text("""
                            INSERT INTO standard_diseases (
                                disease_id, standard_name_en, standard_name_zh,
                                category, icd_10, icd_11, description, source
                            ) VALUES (
                                :disease_id, :name_en, :name_zh,
                                :category, :icd_10, :icd_11, :description, :source
                            )
                            ON CONFLICT (disease_id) DO UPDATE SET
                                standard_name_en = EXCLUDED.standard_name_en,
                                standard_name_zh = EXCLUDED.standard_name_zh,
                                category = EXCLUDED.category,
                                icd_10 = EXCLUDED.icd_10,
                                icd_11 = EXCLUDED.icd_11,
                                description = EXCLUDED.description,
                                updated_at = CURRENT_TIMESTAMP
                        """), {
                            "disease_id": disease_id,
                            "name_en": standard_name_en,
                            "name_zh": standard_name_zh,
                            "category": category if category else None,
                            "icd_10": icd_10,
                            "icd_11": icd_11,
                            "description": description,
                            "source": source
                        })
                    
                    self.stats["standard_diseases_added"] += 1
                    
                    if (idx + 1) % 20 == 0:
                        logger.info(f"  进度: {idx + 1}/{len(df)}")
                
                except Exception as e:
                    error_msg = f"导入标准疾病失败 (行 {idx+2}): {e}"
                    logger.error(f"  ❌ {error_msg}")
                    self.stats["errors"].append(error_msg)
            
            if not self.dry_run:
                await db.commit()
            
            logger.info(f"  ✓ 已导入 {self.stats['standard_diseases_added']} 条标准疾病")
    
    async def _import_country_mappings(self):
        """导入国家映射"""
        mapping_file = ROOT / f"configs/{self.country_code.lower()}/disease_mapping.csv"
        df = pd.read_csv(mapping_file).fillna('')
        
        # 首先获取所有有效的 disease_id
        valid_disease_ids = set()
        async with get_db() as db:
            result = await db.execute(text("SELECT disease_id FROM standard_diseases"))
            valid_disease_ids = {row[0] for row in result.fetchall()}
        
        async with get_db() as db:
            # 确保表存在
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS disease_mappings (
                    id SERIAL PRIMARY KEY,
                    country_code VARCHAR(10) NOT NULL,
                    local_name VARCHAR(200) NOT NULL,
                    disease_id VARCHAR(20) NOT NULL,
                    local_code VARCHAR(50),
                    category VARCHAR(50),
                    priority INTEGER DEFAULT 100,
                    usage_count INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT true,
                    last_used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(country_code, local_name, disease_id)
                )
            """))
            await db.commit()
            
            # 批量插入
            for idx, row in df.iterrows():
                try:
                    disease_id = str(row['disease_id']).strip()
                    local_name = str(row['local_name']).strip()
                    local_code = str(row.get('local_code', '')).strip() if pd.notna(row.get('local_code')) and row.get('local_code') else None
                    category = str(row.get('category', '')).strip() if pd.notna(row.get('category')) and row.get('category') else None
                    
                    if not disease_id or not local_name:
                        logger.warning(f"  ⚠️  跳过无效记录 (行 {idx+2}): disease_id 或 local_name 为空")
                        continue
                    
                    # 验证 disease_id 是否存在于 standard_diseases
                    if disease_id not in valid_disease_ids:
                        warning_msg = f"映射的 disease_id 不存在于标准库: {disease_id} (本地名称: {local_name})"
                        logger.warning(f"  ⚠️  {warning_msg}")
                        self.stats["warnings"].append(warning_msg)
                        continue
                    
                    if not self.dry_run:
                        await db.execute(text("""
                            INSERT INTO disease_mappings (
                                country_code, local_name, disease_id,
                                local_code, category, priority
                            ) VALUES (
                                :country, :local_name, :disease_id,
                                :local_code, :category, :priority
                            )
                            ON CONFLICT (country_code, local_name, disease_id) DO UPDATE SET
                                local_code = EXCLUDED.local_code,
                                category = EXCLUDED.category,
                                updated_at = CURRENT_TIMESTAMP
                        """), {
                            "country": self.country_code,
                            "local_name": local_name,
                            "disease_id": disease_id,
                            "local_code": local_code,
                            "category": category,
                            "priority": 100
                        })
                    
                    self.stats["mappings_added"] += 1
                    
                    # 处理别名（如果有 aliases 列）
                    if 'aliases' in row and pd.notna(row['aliases']) and row['aliases']:
                        aliases = [a.strip() for a in str(row['aliases']).split('|') if a.strip()]
                        for alias in aliases:
                            if not self.dry_run:
                                await db.execute(text("""
                                    INSERT INTO disease_mappings (
                                        country_code, local_name, disease_id, priority
                                    ) VALUES (
                                        :country, :alias, :disease_id, :priority
                                    )
                                    ON CONFLICT (country_code, local_name, disease_id) DO NOTHING
                                """), {
                                    "country": self.country_code,
                                    "alias": alias,
                                    "disease_id": disease_id,
                                    "priority": 90  # 别名优先级稍低
                                })
                            self.stats["mappings_added"] += 1
                    
                    if (idx + 1) % 20 == 0:
                        logger.info(f"  进度: {idx + 1}/{len(df)}")
                
                except Exception as e:
                    error_msg = f"导入映射失败 (行 {idx+2}): {e}"
                    logger.error(f"  ❌ {error_msg}")
                    self.stats["errors"].append(error_msg)
            
            if not self.dry_run:
                await db.commit()
            
            logger.info(f"  ✓ 已导入 {self.stats['mappings_added']} 条映射（含别名）")
    
    async def _validate_integrity(self):
        """验证数据完整性"""
        async with get_db() as db:
            # 1. 检查标准疾病数量
            result = await db.execute(text("SELECT COUNT(*) FROM standard_diseases"))
            std_count = result.scalar()
            logger.info(f"  ✓ 标准疾病库: {std_count} 条记录")
            
            # 2. 检查映射数量
            result = await db.execute(text(
                f"SELECT COUNT(*) FROM disease_mappings WHERE country_code = '{self.country_code}'"
            ))
            map_count = result.scalar()
            logger.info(f"  ✓ {self.country_code} 国家映射: {map_count} 条记录")
            
            # 3. 检查孤立映射（映射的 disease_id 不存在于标准库）
            result = await db.execute(text("""
                SELECT dm.disease_id, COUNT(*) as cnt
                FROM disease_mappings dm
                LEFT JOIN standard_diseases sd ON dm.disease_id = sd.disease_id
                WHERE dm.country_code = :country AND sd.disease_id IS NULL
                GROUP BY dm.disease_id
            """), {"country": self.country_code})
            orphaned = result.fetchall()
            
            if orphaned:
                logger.warning(f"  ⚠️  发现 {len(orphaned)} 个孤立映射（disease_id 不存在于标准库）:")
                for disease_id, cnt in orphaned[:5]:  # 只显示前5个
                    logger.warning(f"      - {disease_id}: {cnt} 条映射")
                if len(orphaned) > 5:
                    logger.warning(f"      ... 还有 {len(orphaned) - 5} 个")
            
            # 4. 检查重复映射
            result = await db.execute(text("""
                SELECT local_name, COUNT(DISTINCT disease_id) as cnt
                FROM disease_mappings
                WHERE country_code = :country
                GROUP BY local_name
                HAVING COUNT(DISTINCT disease_id) > 1
            """), {"country": self.country_code})
            duplicates = result.fetchall()
            
            if duplicates:
                logger.info(f"  ℹ️  发现 {len(duplicates)} 个本地名称有多个映射（这是正常的，会按优先级选择）:")
                for local_name, cnt in duplicates[:5]:
                    logger.info(f"      - {local_name}: {cnt} 个映射")
    
    def _print_summary(self):
        """打印汇总报告"""
        logger.info("📊 刷新结果汇总:")
        logger.info(f"  • 标准疾病: +{self.stats['standard_diseases_added']} 条")
        logger.info(f"  • 国家映射: +{self.stats['mappings_added']} 条")
        
        if self.stats["warnings"]:
            logger.warning(f"\n⚠️  警告 ({len(self.stats['warnings'])} 条):")
            for warning in self.stats["warnings"][:10]:
                logger.warning(f"  - {warning}")
            if len(self.stats["warnings"]) > 10:
                logger.warning(f"  ... 还有 {len(self.stats['warnings']) - 10} 条警告")
        
        if self.stats["errors"]:
            logger.error(f"\n❌ 错误 ({len(self.stats['errors'])} 条):")
            for error in self.stats["errors"][:10]:
                logger.error(f"  - {error}")
            if len(self.stats["errors"]) > 10:
                logger.error(f"  ... 还有 {len(self.stats['errors']) - 10} 条错误")
        
        if not self.stats["errors"]:
            logger.info("\n✅ 刷新完成！所有数据已成功导入")
        else:
            logger.error(f"\n❌ 刷新完成但有 {len(self.stats['errors'])} 个错误")


async def main():
    parser = argparse.ArgumentParser(
        description="刷新疾病映射数据库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/refresh_disease_mappings.py                    # 刷新 CN 映射
  python scripts/refresh_disease_mappings.py --country us       # 刷新 US 映射
  python scripts/refresh_disease_mappings.py --dry-run          # 仅验证不执行
  python scripts/refresh_disease_mappings.py --yes              # 跳过确认
        """
    )
    
    parser.add_argument(
        '--country',
        default='cn',
        help='国家代码 (默认: cn)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅验证不执行实际操作'
    )
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='跳过确认提示'
    )
    
    args = parser.parse_args()
    
    service = DiseaseRefreshService(
        country_code=args.country,
        dry_run=args.dry_run
    )
    
    success = await service.refresh_all(skip_confirmation=args.yes)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
