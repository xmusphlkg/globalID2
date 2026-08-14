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
from datetime import datetime, time, timezone
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from src.core.database import get_db  # noqa: E402
from src.core.country_library import (  # noqa: E402
    get_country_bootstrap_config,
    get_country_profile,
)
from src.core.mapping_paths import (  # noqa: E402
    available_mapping_codes,
    expected_mapping_file,
    resolve_mapping_file,
)
from src.core.db_schema import (  # noqa: E402
    ensure_country_scope,
    ensure_country_scope_schema,
    ensure_disease_mapping_source_schema,
    ensure_disease_learning_suggestions_schema,
)
from src.core.logging import get_logger  # noqa: E402
from src.core.missing_values import normalize_rate_value  # noqa: E402
from src.generation.site_data_database import ensure_standard_country_rows  # noqa: E402
from src.services.database_rebuild_plan import (  # noqa: E402
    RebuildPlan,
    build_rebuild_plan,
    validate_us_nndss_history_scope,
)
from src.services.database_rebuild_import import (  # noqa: E402
    insert_with_savepoint_fallback,
)
from src.services.database_rebuild_run import (  # noqa: E402
    RebuildRunTracker,
    RebuildStage,
    execute_rebuild_stages,
)

logger = get_logger(__name__)
REPORT_TIME_UTC = time(hour=12)
class DatabaseRebuilder:
    def __init__(
        self,
        country_code='cn',
        auto_confirm=False,
        rebuild_mode=None,
        checkpoint_file=None,
    ):
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
        self.rebuild_plan: RebuildPlan | None = None
        self.checkpoint_file = Path(checkpoint_file) if checkpoint_file else None
        self._run_tracker: RebuildRunTracker | None = None
        
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
        self.mapping_file = resolve_mapping_file(ROOT, self.country_code_lower)
        self.history_file = ROOT / f"data/history/{self.country_code_lower}/history_merged.csv"

        expected_main_mapping = expected_mapping_file(ROOT, self.country_code_lower)
        if self.mapping_file != expected_main_mapping:
            logger.warning(
                f"Using legacy mapping path: {self.mapping_file}. "
                f"Please migrate to: {expected_main_mapping}"
            )
        
        # 多语言映射文件
        self.mapping_files = [
            # 中文映射（主映射）
            (self.mapping_file, f"{self.country_code}"),
        ]
        
        # 检查并添加英文映射（独立目录，使用 {country_code}_EN 格式）
        # 英文映射使用 CN_EN 这样的格式存储，与中文映射分开
        en_mapping_file = resolve_mapping_file(ROOT, "en")
        if en_mapping_file.exists():
            self.mapping_files.append((en_mapping_file, f"{self.country_code}_EN"))
            logger.info(f"Found English mapping file: {en_mapping_file}")
        
        # Validate country configuration exists
        if not self.mapping_file.exists():
            raise FileNotFoundError(
                f"Country mapping file not found: {expected_main_mapping}\n"
                f"Available mapping codes: {', '.join(available_mapping_codes(ROOT))}"
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
            await ensure_country_scope_schema(db)
            await db.commit()
            await self._show_warning_and_stats(db)
            
            # Ask for confirmation
            if not self.auto_confirm:
                if not self._confirm_rebuild():
                    logger.info("❌ Operation cancelled by user")
                    return
            
            logger.info("\n" + "=" * 80)
            logger.info(f"Starting database rebuild... (Mode: {self.rebuild_mode})")
            logger.info("=" * 80)
            
            stages = self._build_rebuild_stages(db)
            checkpoint_file = self.checkpoint_file or (
                ROOT
                / "logs/database-rebuild"
                / f"{self.country_code_lower}-{self.rebuild_mode}-latest.json"
            )
            tracker = RebuildRunTracker(
                checkpoint_file,
                country_code=self.country_code,
                mode=self.rebuild_mode,
                stage_names=(stage.name for stage in stages),
            )
            self._run_tracker = tracker
            report = await execute_rebuild_stages(
                db,
                stages,
                tracker,
                on_stage_start=self._log_stage_start,
            )
            logger.info(f"Rebuild checkpoint: {report['checkpoint_file']}")
            
        logger.info("\n" + "=" * 80)
        logger.info("✅ Database rebuild completed successfully!")
        logger.info("=" * 80)

    def _build_rebuild_stages(self, db):
        """Build the ordered, independently committed execution stages."""
        stages = []
        if self.rebuild_options['clear_data']:
            stages.append(
                RebuildStage(
                    "clear_data", "Clearing existing data", lambda: self.clear_data(db)
                )
            )
        stages.append(
            RebuildStage(
                "ensure_country",
                "Ensuring country data exists",
                lambda: self.ensure_country_exists(db),
            )
        )
        if self.rebuild_options['import_standard']:
            stages.append(
                RebuildStage(
                    "import_standard",
                    "Importing standard diseases",
                    lambda: self.import_standard_diseases(db),
                )
            )
        if self.rebuild_options['sync_diseases']:
            stages.append(
                RebuildStage(
                    "sync_diseases",
                    "Synchronizing diseases table",
                    lambda: self.sync_diseases_table(db),
                )
            )
        if self.rebuild_options['import_mappings']:
            stages.append(
                RebuildStage(
                    "import_mappings",
                    f"Importing disease mappings ({self.country_code})",
                    lambda: self.import_disease_mappings(db),
                )
            )
        if self.rebuild_options['import_history']:
            stages.append(
                RebuildStage(
                    "import_history",
                    "Importing historical data",
                    lambda: self.import_history_data(db),
                )
            )
        stages.append(
            RebuildStage(
                "cleanup_suggestions",
                "Cleaning up invalid suggestions",
                lambda: self.cleanup_suggestions(db),
            )
        )
        stages.append(
            RebuildStage(
                "verify",
                "Verifying data",
                lambda: self.verify_results(db),
                commits_changes=False,
            )
        )
        return stages

    @staticmethod
    def _log_stage_start(index, total, stage):
        logger.info(f"\nStep {index}/{total}: {stage.label}...")
    
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
        custom_options = None
        if self.rebuild_mode == 'custom':
            self._select_custom_options()
            custom_options = self.rebuild_options

        self.rebuild_plan = build_rebuild_plan(
            self.rebuild_mode,
            custom_options=custom_options,
        )
        self.rebuild_options = self.rebuild_plan.options()
    
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
        if self.rebuild_plan is None:
            raise RuntimeError("Rebuild plan must be configured before showing statistics")
        tables_to_clear = self.rebuild_plan.tables_to_clear
        preserved_tables = self.rebuild_plan.preserved_tables
        
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

    @staticmethod
    def _normalize_report_time(value) -> datetime | None:
        """Use report calendar day directly and anchor it to stable UTC noon."""
        ts = pd.to_datetime(value, errors='coerce')
        if pd.isna(ts):
            return None
        return datetime.combine(ts.date(), REPORT_TIME_UTC, tzinfo=timezone.utc)
        
    def _confirm_rebuild(self):
        """Ask user for confirmation"""
        logger.info("\n" + "=" * 80)
        try:
            response = input("🔔 Confirm to continue? All existing data will be deleted! (yes/no): ")
            return response.lower() in ('yes', 'y')
        except (KeyboardInterrupt, EOFError):
            print()  # new line
            return False

    async def _get_country_id(self, db):
        """Resolve country_id for the current country code."""
        result = await db.execute(
            text("SELECT id FROM countries WHERE code = :code"),
            {"code": self.country_code},
        )
        row = result.fetchone()
        return row[0] if row else None

    async def _count_country_records(self, db, table: str):
        """Count rows affected by a scoped rebuild action."""
        if table == "disease_records":
            country_id = await self._get_country_id(db)
            if country_id is None:
                return 0
            result = await db.execute(
                text("SELECT COUNT(*) FROM disease_records WHERE country_id = :country_id"),
                {"country_id": country_id},
            )
            return result.scalar() or 0

        if table == "disease_mappings":
            result = await db.execute(
                text(
                    "SELECT COUNT(*) FROM disease_mappings "
                    "WHERE split_part(country_code, '_', 1) = :code"
                ),
                {"code": self.country_code},
            )
            return result.scalar() or 0

        if table == "disease_learning_suggestions":
            result = await db.execute(
                text("SELECT COUNT(*) FROM disease_learning_suggestions WHERE country_code = :code"),
                {"code": self.country_code},
            )
            return result.scalar() or 0

        result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
        return result.scalar() or 0

    async def _delete_country_records(self, db, table: str):
        """Delete only the rows owned by the current country scope."""
        if table == "disease_records":
            country_id = await self._get_country_id(db)
            if country_id is None:
                return 0
            result = await db.execute(
                text("DELETE FROM disease_records WHERE country_id = :country_id"),
                {"country_id": country_id},
            )
            return result.rowcount or 0

        if table == "disease_mappings":
            result = await db.execute(
                text(
                    "DELETE FROM disease_mappings "
                    "WHERE split_part(country_code, '_', 1) = :code"
                ),
                {"code": self.country_code},
            )
            return result.rowcount or 0

        if table == "disease_learning_suggestions":
            result = await db.execute(
                text("DELETE FROM disease_learning_suggestions WHERE country_code = :code"),
                {"code": self.country_code},
            )
            return result.rowcount or 0

        result = await db.execute(text(f"DELETE FROM {table}"))
        return result.rowcount or 0
    
    async def clear_data(self, db):
        """Clear all disease-related data"""
        if self.rebuild_plan is None:
            raise RuntimeError("Rebuild plan must be configured before clearing data")
        # In a multi-country database, clearing must stay scoped to the target country.
        tables = self.rebuild_plan.deletion_tables
        
        for table in tables:
            count = await self._count_country_records(db, table)
            deleted = await self._delete_country_records(db, table)
            logger.info(
                f"  ✓ Cleared {table} for {self.country_code}: deleted {deleted:,} records"
                + (f" (matched {count:,})" if count != deleted else "")
            )
        
        await db.commit()
        logger.info("✓ Data clearing completed")
    
    async def ensure_country_exists(self, db):
        """Ensure country data exists in database"""
        await ensure_country_scope_schema(db)
        profile = await self._ensure_canonical_country_exists(db, self.country_code)

        await ensure_country_scope(
            db,
            scope_code=profile.code,
            country_code=profile.code,
            scope_type="canonical",
            language_code=profile.language,
            display_name=profile.name,
            is_default=True,
            is_active=True,
            metadata={
                "origin": "country_library",
                "source": profile.source,
            },
        )

        await db.commit()

    async def _ensure_canonical_country_exists(self, db, country_code: str):
        """Ensure a canonical country or country/region jurisdiction exists."""
        canonical = country_code.upper()
        profile = get_country_profile(canonical)
        await ensure_standard_country_rows(db, [canonical])
        result = await db.execute(
            text("SELECT id FROM countries WHERE code = :code"),
            {"code": canonical},
        )
        row = result.fetchone()
        if row is None:
            raise RuntimeError(f"Failed to initialize country/region: {canonical}")
        logger.info(
            f"  ✓ Ensured country/region {canonical} (id: {row[0]}) "
            f"from {profile.source}"
        )
        return profile
    
    async def import_standard_diseases(self, db):
        """Import standard disease library"""
        if not self.standard_file.exists():
            raise FileNotFoundError(f"Standard disease file not found: {self.standard_file}")
        
        df = pd.read_csv(self.standard_file).fillna('')
        logger.info(f"  Read {len(df):,} standard diseases")
        
        # Note: category column can be NULL in new schema
        
        inserted = 0
        for _, row in df.iterrows():
            await db.execute(text("""
                INSERT INTO standard_diseases 
                (disease_id, standard_name_en, standard_name_zh, category, icd_10, icd_11, 
                 description, source, metadata, is_active, created_at, updated_at)
                VALUES 
                (:disease_id, :name_en, :name_zh, :category, :icd_10, :icd_11, 
                 :description, :source, '{}'::json, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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
        await ensure_disease_mapping_source_schema(db)
        
        # 首先确保所有需要的country_code都存在（包括语言变体如CN_EN）
        for mapping_file, country_code in self.mapping_files:
            if not mapping_file.exists():
                continue
            
            # 检查并创建country_code（如果不存在）
            await self._ensure_country_code_exists(db, country_code)
        
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
    
    async def _ensure_country_code_exists(self, db, country_code: str):
        """确保映射作用域代码存在（支持语言变体，如 CN_EN）"""
        await ensure_country_scope_schema(db)

        scope_code = country_code.upper()
        is_variant = '_' in scope_code
        base_code = scope_code.split('_', 1)[0]

        profile = await self._ensure_canonical_country_exists(db, base_code)

        if is_variant:
            language_suffix = scope_code.split('_', 1)[1]
            await ensure_country_scope(
                db,
                scope_code=scope_code,
                country_code=base_code,
                scope_type="language_variant",
                language_code=language_suffix.lower(),
                display_name=f"{base_code} ({language_suffix})",
                is_default=False,
                is_active=True,
                metadata={
                    "origin": "mapping_file",
                    "base_country_code": base_code,
                    "variant": language_suffix,
                },
            )
        else:
            await ensure_country_scope(
                db,
                scope_code=base_code,
                country_code=base_code,
                scope_type="canonical",
                language_code=profile.language,
                display_name=profile.name,
                is_default=True,
                is_active=True,
                metadata={
                    "origin": "country_library",
                    "source": profile.source,
                },
            )

        logger.info(f"  ✓ Ensured mapping scope: {scope_code}")
    
    async def _import_single_mapping_file(self, db, df, country_code):
        """导入单个映射文件"""
        inserted = 0
        
        # Allow NULL in category column
        await db.execute(text("""
            ALTER TABLE disease_mappings 
            ALTER COLUMN category DROP NOT NULL
        """))
        
        # Note: category column can be NULL in new schema
        
        inserted = 0
        for _, row in df.iterrows():
            raw_disease_id = row.get('disease_id')
            raw_local_name = row.get('local_name')
            disease_id = (
                '' if pd.isna(raw_disease_id)
                else str(raw_disease_id or '').strip().upper()
            )
            local_name = (
                '' if pd.isna(raw_local_name)
                else str(raw_local_name or '').strip()
            )
            # Registry-only source categories may intentionally have no
            # canonical disease target.  They are explicit no-projection
            # decisions, not legacy disease_mappings rows.
            if not disease_id or not local_name:
                continue
            source_id = str(row.get('source_id') or '').strip().upper() or '*'
            series_id = str(row.get('series_id') or '').strip() or None
            
            # Primary mapping
            await db.execute(text("""
                INSERT INTO disease_mappings 
                (disease_id, country_code, local_name, source_id, series_id, is_primary, is_alias, priority,
                 usage_count, confidence_score, category, source, metadata, is_active, created_at, updated_at)
                VALUES 
                (:disease_id, :country, :local_name, :source_id, :series_id, true, false, 100,
                 0, 1.0, :category, :source, '{}'::json, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (disease_id, country_code, source_id, local_name) DO UPDATE SET
                    is_primary = true,
                    category = EXCLUDED.category,
                    source = EXCLUDED.source,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                'disease_id': disease_id,
                'country': country_code,
                'local_name': local_name,
                'source_id': source_id,
                'series_id': series_id,
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
                        (disease_id, country_code, local_name, source_id, series_id, is_primary, is_alias, priority,
                         usage_count, confidence_score, category, source, metadata, is_active, created_at, updated_at)
                        VALUES 
                        (:disease_id, :country, :alias, :source_id, :series_id, false, true, 50,
                         0, 1.0, :category, :source, '{}'::json, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ON CONFLICT (disease_id, country_code, source_id, local_name) DO UPDATE SET
                            is_alias = true,
                            updated_at = CURRENT_TIMESTAMP
                    """), {
                        'disease_id': disease_id,
                        'country': country_code,
                        'alias': alias,
                        'source_id': source_id,
                        'series_id': series_id,
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
            raise FileNotFoundError(f"Historical data file not found: {self.history_file}")
        
        # Read historical data
        df = pd.read_csv(self.history_file)
        logger.info(f"  Read {len(df):,} historical records")
        if self.country_code == "US":
            validate_us_nndss_history_scope(df)
        
        # Get country_id for the configured country
        result = await db.execute(text("SELECT id FROM countries WHERE code = :code"), {"code": self.country_code})
        country_row = result.fetchone()
        if not country_row:
            raise RuntimeError(f"Country not found in database: {self.country_code}")
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
            missing = [
                label
                for label, column in (
                    ("date", date_col),
                    ("disease", disease_cn_col),
                    ("cases", cases_col),
                    ("deaths", deaths_col),
                )
                if column is None
            ]
            raise ValueError("Historical CSV missing required columns: " + ", ".join(missing))
        
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
                date_obj = self._normalize_report_time(date_str)
                if date_obj is None:
                    skipped += 1
                    continue
                
                # Real data source from CSV. Resolve it before parsing counts so
                # source-specific missing-value contracts can fail closed.
                data_source = 'Historical Data Import'
                if 'Source' in df.columns and pd.notna(row['Source']):
                    data_source = str(row['Source'])

                # A blank NNDSS cell is missing/unknown, never a reported zero.
                cases_missing = not pd.notna(row[cases_col]) or str(row[cases_col]) in ['', '-10', 'nan']
                if cases_missing and self.country_code == "US" and data_source == US_NNDSS_SOURCE_NAME:
                    skipped += 1
                    continue
                cases = int(row[cases_col]) if not cases_missing else 0
                # A blank death field means the source does not report deaths;
                # preserving NULL prevents diagnosis-only feeds such as NNDSS
                # and NHSS from being misrepresented as zero mortality.
                deaths = int(row[deaths_col]) if pd.notna(row[deaths_col]) and str(row[deaths_col]) not in ['', '-10', 'nan'] else None
                
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
                
                # Build metadata object
                metadata_obj = {
                    'source_csv': self.history_file.name,
                    'row_index': int(idx)
                }
                if data_source == 'US CDC NHSS':
                    metadata_obj.update({
                        'frequency': 'annual',
                        'measure': 'hiv_diagnoses_or_aids_classifications',
                        'death_reporting': 'not_provided_by_source',
                    })
                elif data_source == US_NNDSS_SOURCE_NAME:
                    metadata_obj.update({
                        'reporting_area': str(row.get('ReportingArea', '')),
                        'population_scope': 'us_residents_excluding_territories',
                        'death_reporting': 'not_provided_by_source',
                    })
                
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
                    'deaths': max(0, deaths) if deaths is not None else None,
                    'incidence_rate': normalize_rate_value(incidence),
                    'mortality_rate': normalize_rate_value(mortality),
                    'region': region,
                    'data_source': data_source,
                    'metadata': json.dumps(metadata_obj),
                    'raw_data': json.dumps(raw_obj) if raw_obj else None
                })
                
            except Exception:
                skipped += 1
                continue

            # Database writes, commits and checkpoint persistence must stay
            # outside the row-normalization catch above.  A failure here is a
            # stage failure, not a malformed CSV row to silently skip.
            if len(batch_data) >= batch_size:
                inserted += await self._batch_insert_enhanced(db, batch_data)
                batch_data = []

                if inserted % 1000 == 0:
                    await self._commit_history_progress(
                        db,
                        rows_processed=idx + 1,
                        total_rows=len(df),
                        inserted=inserted,
                        skipped=skipped,
                    )
        
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
        if self._run_tracker is not None:
            self._run_tracker.record_partial_commit(
                "import_history",
                {
                    "rows_processed": len(df),
                    "records_imported": inserted,
                    "records_skipped": skipped,
                    "import_committed": True,
                },
            )
        cleaned = await self._cleanup_adjacent_duplicate_snapshots(db, country_id)
        if cleaned > 0:
            await db.commit()
            logger.info(f"✓ Removed {cleaned:,} adjacent duplicate snapshot record(s)")
        logger.info(f"✓ Imported {inserted:,} historical records (skipped {skipped:,})")

    async def _commit_history_progress(
        self,
        db,
        *,
        rows_processed: int,
        total_rows: int,
        inserted: int,
        skipped: int,
    ) -> None:
        """Commit an import chunk and durably expose its partial completion."""
        await db.commit()
        if self._run_tracker is not None:
            self._run_tracker.record_partial_commit(
                "import_history",
                {
                    "rows_processed": rows_processed,
                    "records_imported": inserted,
                    "records_skipped": skipped,
                },
            )
        logger.info(
            f"  Progress: {rows_processed:,}/{total_rows:,} rows processed, "
            f"{inserted:,} records imported, {skipped:,} skipped"
        )
        logger.info(f"  Imported {inserted:,} records...")

    async def _cleanup_adjacent_duplicate_snapshots(self, db, country_id: int) -> int:
        """Remove adjacent-day duplicate snapshots with identical counts for same disease/country."""
        result = await db.execute(
            text(
                """
                WITH candidate AS (
                    SELECT
                        a.ctid AS old_ctid,
                        b.ctid AS new_ctid,
                        EXTRACT(DAY FROM b.time::date) AS new_day
                    FROM disease_records a
                    JOIN disease_records b
                        ON b.country_id = a.country_id
                        AND b.disease_id = a.disease_id
                        AND b.time::date = a.time::date + 1
                        AND COALESCE(b.cases, -1) = COALESCE(a.cases, -1)
                        AND COALESCE(b.deaths, -1) = COALESCE(a.deaths, -1)
                    WHERE a.country_id = :country_id
                ),
                targets AS (
                    SELECT CASE WHEN new_day = 1 THEN old_ctid ELSE new_ctid END AS del_ctid
                    FROM candidate
                )
                DELETE FROM disease_records d
                USING targets t
                WHERE d.ctid = t.del_ctid
                """
            ),
            {"country_id": country_id},
        )
        return result.rowcount or 0
    
    async def _batch_insert(self, db, batch_data):
        """Batch insert data"""
        if not batch_data:
            return 0

        statement = text("""
                INSERT INTO disease_records 
                (time, disease_id, country_id, cases, deaths, new_cases, new_deaths,
                 recoveries, active_cases, new_recoveries, metadata)
                VALUES 
                (:time, :disease_id, :country_id, :cases, :deaths, 0, 0, 0, 0, 0, :metadata)
                ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                    cases = EXCLUDED.cases, 
                    deaths = EXCLUDED.deaths
            """)
        outcome = await insert_with_savepoint_fallback(db, statement, batch_data)
        if outcome.batch_error:
            logger.warning(
                "Batch insert failed; savepoint fallback imported "
                f"{outcome.inserted}/{outcome.attempted} rows: {outcome.batch_error}"
            )
        if outcome.failed:
            raise RuntimeError(
                f"Disease record batch remained incomplete after savepoint fallback: "
                f"{outcome.failed}/{outcome.attempted} rows failed"
            )
        return outcome.inserted
    
    async def _batch_insert_enhanced(self, db, batch_data):
        """Batch insert data with complete fields"""
        if not batch_data:
            return 0
        
        statement = text("""
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
            """)
        outcome = await insert_with_savepoint_fallback(db, statement, batch_data)
        if outcome.batch_error:
            logger.warning(
                "Enhanced batch insert failed; savepoint fallback imported "
                f"{outcome.inserted}/{outcome.attempted} rows: {outcome.batch_error}"
            )
        if outcome.failed:
            raise RuntimeError(
                f"Enhanced disease record batch remained incomplete after savepoint fallback: "
                f"{outcome.failed}/{outcome.attempted} rows failed"
            )
        return outcome.inserted
    
    def _find_column(self, df, candidates):
        """Find column name from candidates"""
        for col in candidates:
            if col in df.columns:
                return col
        return None
    
    async def cleanup_suggestions(self, db):
        """Cleanup invalid suggestions"""
        logger.info("  • Ensuring disease_learning_suggestions schema...")
        await ensure_disease_learning_suggestions_schema(db)

        # 1. 删除空白建议
        result = await db.execute(text(
            "DELETE FROM disease_learning_suggestions "
            "WHERE country_code = :code AND COALESCE(local_name, '') = ''"
        ), {"code": self.country_code})
        blank_count = result.rowcount

        variant_scope = f"{self.country_code}_EN"

        # 2. 删除已有语言变体映射的建议（例如 CN_EN / US_EN）
        result = await db.execute(text('''
            DELETE FROM disease_learning_suggestions
            WHERE id IN (
                SELECT dls.id
                FROM disease_learning_suggestions dls
                JOIN disease_mappings dm ON dls.local_name = dm.local_name
                WHERE dm.country_code = :variant_scope
                  AND dls.country_code = :code
                  AND dls.status = 'pending'
            )
        '''), {"variant_scope": variant_scope, "code": self.country_code})
        en_count = result.rowcount

        await db.commit()

        logger.info(f'  ✓ Deleted blank suggestions: {blank_count} records')
        logger.info(f'  ✓ Deleted mapped English suggestions: {en_count} records')
        logger.info(f'  ✓ Total deleted: {blank_count + en_count} records')

        # 查看剩余
        result = await db.execute(text(
            "SELECT COUNT(*) FROM disease_learning_suggestions "
            "WHERE country_code = :code AND status = 'pending'"
        ), {"code": self.country_code})
        remaining = result.scalar()
        logger.info(f'  📊 Remaining pending suggestions: {remaining} records')

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
            FROM disease_mappings
            WHERE split_part(country_code, '_', 1) = :code
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
            WHERE country_id = (
                SELECT id FROM countries WHERE code = :code
            )
        """), {"code": self.country_code})
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
    parser.add_argument(
        '--checkpoint-file',
        type=Path,
        help=(
            'Persistent JSON run report path '
            '(default: logs/database-rebuild/<country>-<mode>-latest.json)'
        ),
    )
    
    args = parser.parse_args()
    
    try:
        rebuilder = DatabaseRebuilder(
            country_code=args.country,
            auto_confirm=args.yes,
            rebuild_mode=args.mode,
            checkpoint_file=args.checkpoint_file,
        )
        await rebuilder.run()
    except Exception as e:
        logger.exception("❌ Rebuild failed: {}", e)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
