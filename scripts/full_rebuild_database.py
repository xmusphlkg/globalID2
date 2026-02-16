#!/usr/bin/env python3
"""
Complete Database Rebuild - All-in-One Script

Features:
1. Clear all disease-related tables
2. Import standard diseases and mappings from CSV
3. Sync diseases table
4. Import historical data to disease_records
5. Verify data integrity

One-stop solution for complete database initialization!
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
    def __init__(self, country_code='cn', auto_confirm=False, rebuild_mode=None):
        """Initialize DatabaseRebuilder with country-specific configuration
        
        Args:
            country_code: Country code (cn, us, au, jp, etc.), default: cn
            auto_confirm: Skip confirmation prompt if True
            rebuild_mode: Rebuild mode (full, mappings, history, custom), None for interactive
        """
        self.country_code = country_code.upper()
        self.country_code_lower = country_code.lower()
        self.auto_confirm = auto_confirm
        self.rebuild_mode = rebuild_mode
        
        # 重建选项配置
        self.rebuild_options = {
            'clear_data': True,
            'import_standard': True,
            'import_mappings': True,
            'sync_diseases': True,
            'import_history': True,
        }
        
        # Configuration file paths
        self.standard_file = ROOT / "configs/standard_diseases.csv"
        self.mapping_file = ROOT / f"configs/{self.country_code_lower}/disease_mapping.csv"
        self.history_file = ROOT / f"data/processed/{self.country_code_lower}/history_merged.csv"
        
        # 多语言映射文件
        self.mapping_files = [
            # 中文映射（主映射）
            (self.mapping_file, f"{self.country_code}"),
        ]
        
        # 检查并添加英文映射（独立目录，但文件名统一）
        en_mapping_file = ROOT / "configs/en/disease_mapping.csv"
        if en_mapping_file.exists():
            self.mapping_files.append((en_mapping_file, f"{self.country_code}_EN"))
            logger.info(f"Found English mapping file: {en_mapping_file}")
        
        # Validate country configuration exists
        if not self.mapping_file.parent.exists():
            raise FileNotFoundError(
                f"Country configuration not found: {self.mapping_file.parent}\n"
                f"Available countries: {', '.join([d.name for d in (ROOT / 'configs').iterdir() if d.is_dir() and d.name != '__pycache__'])}"
            )
        
    async def run(self):
        """Execute complete database rebuild workflow"""
        logger.info("=" * 80)
        logger.info(f"🚀 Database Rebuild - Country: {self.country_code}")
        logger.info("=" * 80)
        
        # 选择重建模式（如果未指定）
        if self.rebuild_mode is None and not self.auto_confirm:
            self.rebuild_mode = self._select_rebuild_mode()
        elif self.rebuild_mode is None:
            self.rebuild_mode = 'full'
        
        # 根据模式设置重建选项
        self._configure_rebuild_options()
        
        # Show warnings and statistics
        async with get_db() as db:
            await self._show_warning_and_stats(db)
            
            # Ask for confirmation
            if not self.auto_confirm:
                if not self._confirm_rebuild():
                    logger.info("❌ Operation cancelled by user")
                    return
            
            logger.info("\n" + "=" * 80)
            logger.info(f"Starting database rebuild... (Mode: {self.rebuild_mode})")
            logger.info("=" * 80)
            
            # 根据配置执行步骤
            step_num = 1
            total_steps = sum(self.rebuild_options.values()) + 1  # +1 for verify
            
            # Step: Clear existing data
            if self.rebuild_options['clear_data']:
                logger.info(f"\n📦 Step {step_num}/{total_steps}: Clearing existing data...")
                await self.clear_data(db)
                step_num += 1
            
            # Step: Import standard diseases
            if self.rebuild_options['import_standard']:
                logger.info(f"\n📚 Step {step_num}/{total_steps}: Importing standard diseases...")
                await self.import_standard_diseases(db)
                step_num += 1
            
            # Step: Import disease mappings
            if self.rebuild_options['import_mappings']:
                logger.info(f"\n🗺️  Step {step_num}/{total_steps}: Importing disease mappings ({self.country_code})...")
                await self.import_disease_mappings(db)
                step_num += 1
            
            # Step: Sync diseases table
            if self.rebuild_options['sync_diseases']:
                logger.info(f"\n🔄 Step {step_num}/{total_steps}: Synchronizing diseases table...")
                await self.sync_diseases_table(db)
                step_num += 1
            
            # Step: Import historical data
            if self.rebuild_options['import_history']:
                logger.info(f"\n📊 Step {step_num}/{total_steps}: Importing historical data...")
                await self.import_history_data(db)
                step_num += 1
            
            # Step: Verify results
            logger.info(f"\n✅ Step {step_num}/{total_steps}: Verifying data...")
            await self.verify_results(db)
            
        logger.info("\n" + "=" * 80)
        logger.info("✅ Database rebuild completed successfully!")
        logger.info("=" * 80)
    
    def _select_rebuild_mode(self):
        """交互式选择重建模式"""
        print("\n" + "=" * 80)
        print("🔧 请选择重建模式:")
        print("=" * 80)
        print("1. 完整重建 (Full Rebuild)")
        print("   • 清空所有表")
        print("   • 导入标准疾病库")
        print("   • 导入疾病映射（中文 + 英文）")
        print("   • 同步疾病表")
        print("   • 导入历史数据")
        print()
        print("2. 仅更新映射 (Mappings Only)")
        print("   • 清空映射相关表（standard_diseases, disease_mappings, diseases）")
        print("   • 导入标准疾病库")
        print("   • 导入疾病映射（中文 + 英文）")
        print("   • 同步疾病表")
        print("   • 保留历史数据不动")
        print()
        print("3. 仅导入历史数据 (History Only)")
        print("   • 仅清空 disease_records 表")
        print("   • 重新导入历史数据")
        print("   • 不修改映射表")
        print()
        print("4. 自定义选择 (Custom)")
        print("   • 手动选择要执行的步骤")
        print()
        print("=" * 80)
        
        while True:
            choice = input("请输入选项 (1-4) [默认: 1]: ").strip() or "1"
            if choice in ['1', '2', '3', '4']:
                mode_map = {'1': 'full', '2': 'mappings', '3': 'history', '4': 'custom'}
                return mode_map[choice]
            print("❌ 无效选项，请重新输入")
    
    def _configure_rebuild_options(self):
        """根据重建模式配置选项"""
        if self.rebuild_mode == 'full':
            # 完整重建：所有步骤
            self.rebuild_options = {
                'clear_data': True,
                'import_standard': True,
                'import_mappings': True,
                'sync_diseases': True,
                'import_history': True,
            }
        
        elif self.rebuild_mode == 'mappings':
            # 仅更新映射：不导入历史数据
            self.rebuild_options = {
                'clear_data': True,
                'import_standard': True,
                'import_mappings': True,
                'sync_diseases': True,
                'import_history': False,
            }
        
        elif self.rebuild_mode == 'history':
            # 仅导入历史：只清空和导入 disease_records
            self.rebuild_options = {
                'clear_data': True,  # 会清空 disease_records 表
                'import_standard': False,
                'import_mappings': False,
                'sync_diseases': False,
                'import_history': True,
            }
        
        elif self.rebuild_mode == 'custom':
            # 自定义：交互式选择
            self._select_custom_options()
    
    def _select_custom_options(self):
        """交互式选择自定义步骤"""
        print("\n" + "=" * 80)
        print("🎯 自定义重建步骤:")
        print("=" * 80)
        
        options = [
            ('clear_data', '清空现有数据'),
            ('import_standard', '导入标准疾病库'),
            ('import_mappings', '导入疾病映射（中文 + 英文）'),
            ('sync_diseases', '同步疾病表'),
            ('import_history', '导入历史数据'),
        ]
        
        for key, desc in options:
            while True:
                answer = input(f"  • {desc}? (y/n) [默认: y]: ").strip().lower() or 'y'
                if answer in ['y', 'n', 'yes', 'no']:
                    self.rebuild_options[key] = answer in ['y', 'yes']
                    break
                print("    ❌ 无效输入，请输入 y 或 n")
        
        print("=" * 80)
        print("✓ 自定义配置完成")
        print("=" * 80)
    
    async def _show_warning_and_stats(self, db):
        """Display warning message and current data statistics"""
        # 根据 rebuild_mode 显示将要清空的表
        if self.rebuild_mode == 'history':
            tables_to_clear = ["disease_records"]
            preserved_tables = ["diseases", "disease_mappings", "standard_diseases", "crawl_runs", "crawl_raw_pages"]
        elif self.rebuild_mode == 'mappings':
            tables_to_clear = ["disease_mappings", "standard_diseases"]
            preserved_tables = ["disease_records (历史数据)", "crawl_runs", "crawl_raw_pages"]
        else:  # full or custom
            tables_to_clear = ["disease_records", "diseases", "disease_mappings", "standard_diseases"]
            preserved_tables = ["crawl_runs", "crawl_raw_pages"]
        
        logger.warning("\n⚠️  WARNING: This operation will clear the following tables:")
        for table in tables_to_clear:
            logger.warning(f"   • {table}")
        
        if preserved_tables:
            logger.warning(f"   • (preserved) {', '.join(preserved_tables)}")
        
        logger.info("\n📊 Current Data Statistics:")
        
        tables = {
            "disease_records": "Disease Records",
            "diseases": "Diseases",
            "disease_mappings": "Disease Mappings",
            "standard_diseases": "Standard Diseases"
        }
        
        for table, label in tables.items():
            try:
                result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                logger.info(f"   • {label:20s}: {count:,} records")
            except Exception:
                logger.info(f"   • {label:20s}: (table not found)")

        preserved_tables = {
            "crawl_runs": "Crawl Runs",
            "crawl_raw_pages": "Crawl Raw Pages"
        }

        logger.info("\n📌 Preserved Tables:")
        for table, label in preserved_tables.items():
            try:
                result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                logger.info(f"   • {label:20s}: {count:,} records")
            except Exception:
                logger.info(f"   • {label:20s}: (table not found)")
        
        logger.info("\n📥 Files to Import:")
        logger.info(f"   • Standard Diseases: {self.standard_file.name}")
        logger.info(f"   • Disease Mappings:  {self.mapping_file.name} (country: {self.country_code})")
        logger.info(f"   • Historical Data:   {self.history_file.name}")
        
    def _confirm_rebuild(self):
        """Ask user for confirmation"""
        logger.info("\n" + "=" * 80)
        try:
            response = input("🔔 Confirm to continue? All existing data will be deleted! (yes/no): ")
            return response.lower() in ('yes', 'y')
        except (KeyboardInterrupt, EOFError):
            print()  # new line
            return False
    
    async def clear_data(self, db):
        """Clear all disease-related data"""
        # Delete in proper order to respect foreign key constraints
        if self.rebuild_mode == 'history':
            # 仅清空历史数据表
            tables = ["disease_records"]
        elif self.rebuild_mode == 'mappings':
            # 仅清空映射相关表，保留 diseases 表以避免级联删除历史数据
            # diseases 表会通过 sync_diseases_table 进行 UPSERT 更新
            tables = [
                "disease_mappings",
                "standard_diseases"
            ]
        else:
            # 完整重建 或 自定义模式：清空所有表
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
            logger.info(f"  ✓ Cleared {table}: deleted {count:,} records")
        
        await db.commit()
        logger.info("✓ Data clearing completed")
    
    async def import_standard_diseases(self, db):
        """Import standard disease library"""
        if not self.standard_file.exists():
            raise FileNotFoundError(f"Standard disease file not found: {self.standard_file}")
        
        df = pd.read_csv(self.standard_file).fillna('')
        logger.info(f"  Read {len(df):,} standard diseases")
        
        # Allow NULL in category column
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
        logger.info(f"✓ Imported {inserted:,} standard diseases")
    
    async def import_disease_mappings(self, db):
        """Import disease mapping relationships (支持多语言映射)"""
        total_inserted = 0
        
        # 处理所有映射文件（中文 + 英文）
        for mapping_file, country_code in self.mapping_files:
            if not mapping_file.exists():
                logger.warning(f"  Mapping file not found: {mapping_file}, skipping...")
                continue
            
            df = pd.read_csv(mapping_file).fillna('')
            logger.info(f"  Loading {mapping_file.name} ({country_code}): {len(df):,} entries")
            
            inserted = await self._import_single_mapping_file(db, df, country_code)
            total_inserted += inserted
        
        await db.commit()
        logger.info(f"✓ Imported {total_inserted:,} total mapping relationships")
    
    async def _import_single_mapping_file(self, db, df, country_code):
        """导入单个映射文件"""
        inserted = 0
        
        # Allow NULL in category column
        await db.execute(text("""
            ALTER TABLE disease_mappings 
            ALTER COLUMN category DROP NOT NULL
        """))
        
        inserted = 0
        for _, row in df.iterrows():
            disease_id = row['disease_id']
            local_name = row['local_name']
            
            # Primary mapping
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
                'country': country_code,
                'local_name': local_name,
                'category': row['category'] if row['category'] else None,
                'source': row.get('data_source', row.get('source', 'Manual'))
            })
            inserted += 1
            
            # Aliases (split by | or ,)
            if row.get('aliases'):
                # Support both | and , as separators  
                alias_str = str(row['aliases'])
                # First try pipe separator (primary format in CSV)
                if '|' in alias_str:
                    aliases = [a.strip() for a in alias_str.split('|') if a.strip()]
                else:
                    # Fallback to comma separator
                    aliases = [a.strip() for a in alias_str.split(',') if a.strip()]
                
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
                        'country': country_code,
                        'alias': alias,
                        'category': row['category'] if row['category'] else None,
                        'source': row.get('source', 'Manual')
                    })
                    inserted += 1
        
        return inserted
    
    async def sync_diseases_table(self, db):
        """Synchronize diseases table"""
        # Import from standard_diseases to diseases
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
        logger.info(f"✓ Synced {count:,} diseases to diseases table")
    
    async def import_history_data(self, db):
        """Import historical data with complete fields (data_source, incidence_rate, metadata, etc.)"""
        logger.info("\n📊 Step 5/6: Importing historical data...")
        
        if not self.history_file.exists():
            logger.warning(f"Historical data file not found: {self.history_file}")
            return
        
        # Read historical data
        df = pd.read_csv(self.history_file)
        logger.info(f"  Read {len(df):,} historical records")
        
        # Get country_id for the configured country
        result = await db.execute(text(f"SELECT id FROM countries WHERE code = :code"), {"code": self.country_code})
        country_row = result.fetchone()
        if not country_row:
            logger.error(f"Country not found in database: {self.country_code}")
            return
        country_id = country_row[0]
        
        # Build mapping dictionary (with normalization for tolerance)
        result = await db.execute(text("""
            SELECT dm.local_name, d.id
            FROM disease_mappings dm
            JOIN diseases d ON dm.disease_id = d.name
            WHERE dm.country_code = :code AND dm.is_active = true
        """), {"code": self.country_code})
        
        # Use normalized keys for better matching tolerance
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
        
        logger.info(f"  Loaded {len(mapping_dict):,} disease mappings (normalized)")
        
        # Determine column names
        date_col = self._find_column(df, ['Date', 'date', 'time', 'Time', 'YearMonthDay'])
        disease_cn_col = self._find_column(df, ['DiseasesCN', 'disease_cn', 'DiseaseName', 'DiseaseCN'])
        disease_en_col = self._find_column(df, ['Diseases', 'disease_en', 'Disease'])
        cases_col = self._find_column(df, ['Cases', 'cases', 'case', 'CaseCount'])
        deaths_col = self._find_column(df, ['Deaths', 'deaths', 'death', 'DeathCount'])
        
        if not all([date_col, disease_cn_col, cases_col, deaths_col]):
            logger.error("CSV missing required columns")
            return
        
        # Batch import data with complete fields
        inserted = 0
        skipped = 0
        batch_size = 1000
        batch_data = []
        error_diseases = set()  # Track diseases without mapping
        
        for idx, row in df.iterrows():
            try:
                # Extract basic fields
                disease_cn = str(row[disease_cn_col]) if pd.notna(row[disease_cn_col]) else None
                if not disease_cn or disease_cn == 'nan':
                    skipped += 1
                    continue
                
                disease_en = str(row[disease_en_col]) if disease_en_col and pd.notna(row[disease_en_col]) else None
                
                # Find mapping (using normalization)
                db_disease_id = None
                if disease_en:
                    db_disease_id = mapping_dict.get(_norm(disease_en))
                if not db_disease_id:
                    db_disease_id = mapping_dict.get(_norm(disease_cn))
                
                if not db_disease_id:
                    # Track unmapped diseases for reporting
                    if disease_cn not in error_diseases:
                        error_diseases.add(disease_cn)
                    skipped += 1
                    continue
                
                # Parse date
                date_str = str(row[date_col])
                try:
                    if '/' in date_str:
                        date_obj = datetime.strptime(date_str, '%Y/%m/%d')
                    else:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                except:
                    skipped += 1
                    continue
                
                # Extract numeric values
                cases = int(row[cases_col]) if pd.notna(row[cases_col]) and str(row[cases_col]) not in ['', '-10', 'nan'] else 0
                deaths = int(row[deaths_col]) if pd.notna(row[deaths_col]) and str(row[deaths_col]) not in ['', '-10', 'nan'] else 0
                
                # Extract additional fields
                incidence = None
                if 'Incidence' in df.columns and pd.notna(row['Incidence']):
                    val = float(row['Incidence'])
                    incidence = val if val >= 0 else None
                
                mortality = None
                if 'Mortality' in df.columns and pd.notna(row['Mortality']):
                    val = float(row['Mortality'])
                    mortality = val if val >= 0 else None
                
                region = None
                if 'ProvinceCN' in df.columns and pd.notna(row['ProvinceCN']) and str(row['ProvinceCN']) not in ['China', 'National', 'Nationwide']:
                    region = str(row['ProvinceCN'])
                elif 'Province' in df.columns and pd.notna(row['Province']) and str(row['Province']) not in ['China', 'National', 'Nationwide']:
                    region = str(row['Province'])
                
                # Real data source from CSV
                data_source = 'Historical Data Import'
                if 'Source' in df.columns and pd.notna(row['Source']):
                    data_source = str(row['Source'])
                
                # Build metadata object
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
                
                # Prepare raw data for traceability
                raw_obj = None
                try:
                    raw_obj = {k: (None if pd.isna(v) else v) for k, v in row.items()}
                except Exception:
                    pass
                
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
                    'metadata': json.dumps(metadata_obj),
                    'raw_data': json.dumps(raw_obj) if raw_obj else None
                })
                
                # Batch insert
                if len(batch_data) >= batch_size:
                    inserted += await self._batch_insert_enhanced(db, batch_data)
                    batch_data = []
                    
                    # Progress update every 1000 records
                    if inserted % 1000 == 0:
                        await db.commit()
                        logger.info(f"  Progress: {idx + 1:,}/{len(df):,} rows processed, {inserted:,} records imported, {skipped:,} skipped")
                        logger.info(f"  Imported {inserted:,} records...")
                        
            except Exception as e:
                skipped += 1
                continue
        
        # Insert remaining data
        if batch_data:
            inserted += await self._batch_insert_enhanced(db, batch_data)
        
        # Report unmapped diseases
        if error_diseases:
            logger.warning(f"\n⚠️  {len(error_diseases)} diseases without mapping:")
            for disease in sorted(error_diseases)[:20]:
                logger.warning(f"    - {disease}")
            if len(error_diseases) > 20:
                logger.warning(f"    ... and {len(error_diseases) - 20} more")
        
        await db.commit()
        logger.info(f"✓ Imported {inserted:,} historical records (skipped {skipped:,})")
    
    async def _batch_insert(self, db, batch_data):
        """Batch insert data"""
        if not batch_data:
            return 0
        
        try:
            # Use executemany for batch insert
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
            logger.warning(f"Batch insert failed, trying individual inserts: {str(e)[:200]}")
            # Rollback current transaction
            await db.rollback()
            # Fallback to single inserts
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
        """Batch insert data with complete fields"""
        if not batch_data:
            return 0
        
        try:
            # Use executemany for batch insert with all fields
            await db.execute(text("""
                INSERT INTO disease_records 
                (time, disease_id, country_id, cases, deaths, 
                 incidence_rate, mortality_rate, region, data_source,
                 new_cases, new_deaths, recoveries, active_cases, new_recoveries, 
                 metadata, raw_data)
                VALUES 
                (:time, :disease_id, :country_id, :cases, :deaths, 
                 :incidence_rate, :mortality_rate, :region, :data_source,
                 0, 0, 0, 0, 0, :metadata, :raw_data)
                ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                    cases = EXCLUDED.cases, 
                    deaths = EXCLUDED.deaths,
                    incidence_rate = EXCLUDED.incidence_rate,
                    mortality_rate = EXCLUDED.mortality_rate,
                    region = EXCLUDED.region,
                    data_source = EXCLUDED.data_source,
                    metadata = EXCLUDED.metadata,
                    raw_data = EXCLUDED.raw_data
            """), batch_data)
            return len(batch_data)
        except Exception as e:
            logger.warning(f"Batch insert failed, trying individual inserts: {str(e)[:200]}")
            # Rollback current transaction
            await db.rollback()
            # Fallback to single inserts
            success = 0
            for data in batch_data:
                try:
                    await db.execute(text("""
                        INSERT INTO disease_records 
                        (time, disease_id, country_id, cases, deaths, 
                         incidence_rate, mortality_rate, region, data_source,
                         new_cases, new_deaths, recoveries, active_cases, new_recoveries, 
                         metadata, raw_data)
                        VALUES 
                        (:time, :disease_id, :country_id, :cases, :deaths, 
                         :incidence_rate, :mortality_rate, :region, :data_source,
                         0, 0, 0, 0, 0, :metadata, :raw_data)
                        ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                            cases = EXCLUDED.cases, 
                            deaths = EXCLUDED.deaths,
                            incidence_rate = EXCLUDED.incidence_rate,
                            mortality_rate = EXCLUDED.mortality_rate,
                            region = EXCLUDED.region,
                            data_source = EXCLUDED.data_source,
                            metadata = EXCLUDED.metadata,
                            raw_data = EXCLUDED.raw_data
                    """), data)
                    success += 1
                except Exception as inner_e:
                    await db.rollback()
                    continue
            return success
    
    def _find_column(self, df, candidates):
        """Find column name from candidates"""
        for col in candidates:
            if col in df.columns:
                return col
        return None
    
    async def verify_results(self, db):
        """Verify import results"""
        logger.info("\n✅ Step 6/6: Verifying data...")
        
        # Standard diseases count
        result = await db.execute(text("SELECT COUNT(*) FROM standard_diseases"))
        std_count = result.scalar()
        logger.info(f"  • Standard Diseases: {std_count:,} records")
        
        # Mapping relationships count
        result = await db.execute(text("""
            SELECT COUNT(*), COUNT(DISTINCT disease_id) 
            FROM disease_mappings WHERE country_code = :code
        """), {"code": self.country_code})
        map_total, map_diseases = result.fetchone()
        logger.info(f"  • Disease Mappings ({self.country_code}): {map_total:,} mappings covering {map_diseases:,} diseases")
        
        # Diseases table
        result = await db.execute(text("SELECT COUNT(*) FROM diseases"))
        diseases_count = result.scalar()
        logger.info(f"  • Diseases Table: {diseases_count:,} records")
        
        # Historical records
        result = await db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT disease_id) as diseases,
                MIN(time) as earliest,
                MAX(time) as latest
            FROM disease_records
        """))
        rec = result.fetchone()
        logger.info(f"  • Historical Records: {rec[0]:,} records")
        logger.info(f"  • Disease Coverage: {rec[1]:,} diseases")
        logger.info(f"  • Time Range: {rec[2]} to {rec[3]}")

async def main():
    parser = argparse.ArgumentParser(
        description="Complete database rebuild for GlobalID system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/full_rebuild_database.py                        # Interactive mode
  python scripts/full_rebuild_database.py --yes                  # Auto-confirm (full rebuild)
  python scripts/full_rebuild_database.py --mode mappings        # Only rebuild mappings
  python scripts/full_rebuild_database.py --mode history --yes   # Only reimport history data
  python scripts/full_rebuild_database.py --country us           # Rebuild US data
        """
    )
    
    parser.add_argument(
        '--country',
        default='cn',
        help='Country code (default: cn)'
    )
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='Auto-confirm without prompting'
    )
    parser.add_argument(
        '--mode', '-m',
        choices=['full', 'mappings', 'history', 'custom'],
        help='Rebuild mode: full (all), mappings (only mappings), history (only history), custom (interactive)'
    )
    
    args = parser.parse_args()
    
    try:
        rebuilder = DatabaseRebuilder(
            country_code=args.country,
            auto_confirm=args.yes,
            rebuild_mode=args.mode
        )
        await rebuilder.run()
    except Exception as e:
        logger.error(f"❌ Rebuild failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
