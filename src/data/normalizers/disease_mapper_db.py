"""
GlobalID V2 Database-based Disease Mapper

从PostgreSQL数据库读取疾病映射（支持动态更新）
"""
from typing import Optional, Dict, List
from dataclasses import dataclass
import pandas as pd
from sqlalchemy import text

from src.core.database import get_db
from src.core.db_schema import (
    ensure_country_scope_for_code,
    ensure_country_scope_schema,
    ensure_disease_learning_suggestions_schema,
)
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DiseaseInfo:
    """疾病信息"""
    disease_id: str
    standard_name_en: str
    standard_name_zh: str
    category: str
    icd_10: Optional[str]
    icd_11: Optional[str]
    description: Optional[str]


class DiseaseMapperDB:
    """
    数据库版疾病映射器
    
    优势:
    - 支持动态添加疾病
    - 多实例共享数据
    - 自动学习未知疾病
    - 记录使用统计
    """
    
    def __init__(self, country_code: str):
        self.country_code = country_code.upper() if country_code else country_code
        self._local_cache = {}  # 内存缓存
        self._standard_cache = {}
    
    async def map_local_to_id(self, local_name: str) -> Optional[str]:
        """
        本地名称 → disease_id
        
        Args:
            local_name: 本地疾病名称
            
        Returns:
            disease_id 或 None
        """
        # 检查内存缓存
        cache_key = f"{self.country_code}:{local_name}"
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]
        
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT disease_id, usage_count
                    FROM disease_mappings
                    WHERE country_code = :country
                      AND local_name = :name
                      AND is_active = true
                    ORDER BY priority DESC, usage_count DESC
                    LIMIT 1
                """),
                {"country": self.country_code, "name": local_name}
            )
            row = result.fetchone()
            
            if row:
                disease_id = row[0]
                
                # 更新使用统计（异步，不阻塞）
                try:
                    await db.execute(text("""
                        UPDATE disease_mappings
                        SET usage_count = usage_count + 1,
                            last_used_at = CURRENT_TIMESTAMP
                        WHERE country_code = :country
                          AND local_name = :name
                    """), {"country": self.country_code, "name": local_name})
                    await db.commit()
                except Exception as e:
                    logger.debug(f"更新使用统计失败: {e}")
                
                # 缓存结果
                self._local_cache[cache_key] = disease_id
                return disease_id
            else:
                # 记录未知疾病
                await self._record_unknown_disease(local_name)
                return None
    
    async def _record_unknown_disease(self, local_name: str):
        """记录未知疾病到学习建议表"""
        try:
            async with get_db() as db:
                await ensure_disease_learning_suggestions_schema(db)
                await db.execute(text("""
                    INSERT INTO disease_learning_suggestions (
                        country_code, local_name,
                        occurrence_count, first_seen_at, last_seen_at
                    ) VALUES (
                        :country, :name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (country_code, local_name) DO UPDATE SET
                        occurrence_count = disease_learning_suggestions.occurrence_count + 1,
                        last_seen_at = CURRENT_TIMESTAMP
                """), {"country": self.country_code, "name": local_name})
                await db.commit()
        except Exception as e:
            logger.debug(f"记录未知疾病失败: {e}")
    
    async def get_standard_info(self, disease_id: str) -> Optional[DiseaseInfo]:
        """
        disease_id → 标准信息
        
        Args:
            disease_id: 疾病ID (如 D004)
            
        Returns:
            DiseaseInfo 或 None
        """
        # 检查缓存
        if disease_id in self._standard_cache:
            return self._standard_cache[disease_id]
        
        async with get_db() as db:
            result = await db.execute(
                text("""
                    SELECT disease_id, standard_name_en, standard_name_zh,
                           category, icd_10, icd_11, description
                    FROM standard_diseases
                    WHERE disease_id = :did AND is_active = true
                """),
                {"did": disease_id}
            )
            row = result.fetchone()
            
            if row:
                info = DiseaseInfo(
                    disease_id=row[0],
                    standard_name_en=row[1],
                    standard_name_zh=row[2],
                    category=row[3],
                    icd_10=row[4],
                    icd_11=row[5],
                    description=row[6]
                )
                
                # 缓存结果
                self._standard_cache[disease_id] = info
                return info
            
            return None
    
    async def get_standard_name(
        self,
        disease_id: str,
        lang: str = "en"
    ) -> Optional[str]:
        """
        获取标准名称
        
        Args:
            disease_id: 疾病ID
            lang: 语言 (en/zh)
            
        Returns:
            标准名称 或 None
        """
        info = await self.get_standard_info(disease_id)
        if info:
            return info.standard_name_en if lang == "en" else info.standard_name_zh
        return None
    
    async def map_dataframe(
        self,
        df: pd.DataFrame,
        disease_col: str = "disease_name",
        add_id_col: bool = True,
        add_standard_name: bool = True
    ) -> pd.DataFrame:
        """
        批量映射DataFrame
        
        Args:
            df: 数据框
            disease_col: 疾病名称列
            add_id_col: 是否添加disease_id列
            add_standard_name: 是否添加标准名称列
            
        Returns:
            处理后的数据框
        """
        result_df = df.copy()
        
        if add_id_col:
            # 批量查询映射
            unique_diseases = result_df[disease_col].dropna().unique()
            
            # 构建映射字典
            disease_to_id = {}
            for disease_name in unique_diseases:
                disease_id = await self.map_local_to_id(disease_name)
                disease_to_id[disease_name] = disease_id
            
            # 应用映射
            result_df['disease_id'] = result_df[disease_col].map(disease_to_id)
        
        if add_standard_name:
            # 批量查询标准名称
            unique_ids = result_df['disease_id'].dropna().unique()
            
            id_to_name_en = {}
            id_to_name_zh = {}
            
            for disease_id in unique_ids:
                info = await self.get_standard_info(disease_id)
                if info:
                    id_to_name_en[disease_id] = info.standard_name_en
                    id_to_name_zh[disease_id] = info.standard_name_zh
            
            result_df['standard_name_en'] = result_df['disease_id'].map(id_to_name_en)
            result_df['standard_name_zh'] = result_df['disease_id'].map(id_to_name_zh)
        
        return result_df
    
    async def add_disease(
        self,
        disease_id: str,
        standard_name_en: str,
        standard_name_zh: str,
        category: str,
        **kwargs
    ) -> int:
        """
        添加新疾病到标准库
        
        Args:
            disease_id: 疾病ID (如 D142)
            standard_name_en: 英文标准名
            standard_name_zh: 中文标准名
            category: 分类 (Viral/Bacterial/Parasitic/Fungal)
            **kwargs: 其他字段 (icd_10, icd_11, description, created_by, source)
            
        Returns:
            新记录的ID
        """
        async with get_db() as db:
            result = await db.execute(text("""
                INSERT INTO standard_diseases (
                    disease_id, standard_name_en, standard_name_zh,
                    category, icd_10, icd_11, description,
                    created_by, source
                ) VALUES (
                    :disease_id, :name_en, :name_zh,
                    :category, :icd_10, :icd_11, :description,
                    :created_by, :source
                )
                RETURNING id
            """), {
                "disease_id": disease_id,
                "name_en": standard_name_en,
                "name_zh": standard_name_zh,
                "category": category,
                "icd_10": kwargs.get('icd_10'),
                "icd_11": kwargs.get('icd_11'),
                "description": kwargs.get('description'),
                "created_by": kwargs.get('created_by', 'api'),
                "source": kwargs.get('source', 'manual')
            })
            await db.commit()
            
            record_id = result.scalar_one()
            
            # 清除缓存
            self._standard_cache.pop(disease_id, None)
            
            logger.info(f"✅ 新疾病添加成功: {disease_id} - {standard_name_en}")
            return record_id
    
    async def add_mapping(
        self,
        disease_id: str,
        local_name: str,
        **kwargs
    ) -> int:
        """
        添加国家映射
        
        Args:
            disease_id: 疾病ID
            local_name: 本地名称
            **kwargs: 其他字段 (local_code, is_primary, is_alias, category, source, created_by)
            
        Returns:
            新记录的ID
        """
        async with get_db() as db:
            await ensure_country_scope_schema(db)
            await ensure_country_scope_for_code(db, self.country_code)
            result = await db.execute(text("""
                INSERT INTO disease_mappings (
                    disease_id, country_code, local_name, local_code,
                    is_primary, is_alias, category, source, created_by
                ) VALUES (
                    :disease_id, :country, :local_name, :local_code,
                    :is_primary, :is_alias, :category, :source, :created_by
                )
                ON CONFLICT (country_code, local_name) DO UPDATE SET
                    disease_id = EXCLUDED.disease_id,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
            """), {
                "disease_id": disease_id,
                "country": self.country_code,
                "local_name": local_name,
                "local_code": kwargs.get('local_code', ''),
                "is_primary": kwargs.get('is_primary', True),
                "is_alias": kwargs.get('is_alias', False),
                "category": kwargs.get('category', ''),
                "source": kwargs.get('source', 'manual'),
                "created_by": kwargs.get('created_by', 'api')
            })
            await db.commit()
            
            record_id = result.scalar_one()
            
            # 清除缓存
            cache_key = f"{self.country_code}:{local_name}"
            self._local_cache.pop(cache_key, None)
            
            logger.info(f"✅ 映射添加成功: {local_name} → {disease_id}")
            return record_id
    
    async def get_statistics(self) -> Dict:
        """获取统计信息"""
        async with get_db() as db:
            # 标准疾病数
            result = await db.execute(
                text("SELECT COUNT(*) FROM standard_diseases WHERE is_active = true")
            )
            total_diseases = result.scalar()
            
            # 当前国家映射数
            result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM disease_mappings
                    WHERE country_code = :country AND is_active = true
                """),
                {"country": self.country_code}
            )
            total_mappings = result.scalar()
            
            # 主名称和别名数
            result = await db.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE is_primary) as primary_count,
                        COUNT(*) FILTER (WHERE is_alias) as alias_count
                    FROM disease_mappings
                    WHERE country_code = :country AND is_active = true
                """),
                {"country": self.country_code}
            )
            row = result.fetchone()
            primary_count = row[0] if row else 0
            alias_count = row[1] if row else 0
            
            # 待审核建议数
            await ensure_disease_learning_suggestions_schema(db)
            result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM disease_learning_suggestions
                    WHERE country_code = :country AND status = 'pending'
                """),
                {"country": self.country_code}
            )
            pending_suggestions = result.scalar()
            
            return {
                "standard_diseases": total_diseases,
                "total_mappings": total_mappings,
                "primary_mappings": primary_count,
                "alias_mappings": alias_count,
                "pending_suggestions": pending_suggestions,
                "country_code": self.country_code
            }
    
    async def get_unknown_diseases(self, limit: int = 20) -> List[Dict]:
        """获取未知疾病列表"""
        async with get_db() as db:
            await ensure_disease_learning_suggestions_schema(db)
            result = await db.execute(
                text("""
                    SELECT id, local_name, occurrence_count,
                           suggested_disease_id, suggested_standard_name,
                           ai_confidence, first_seen_at, last_seen_at
                    FROM disease_learning_suggestions
                    WHERE country_code = :country AND status = 'pending'
                    ORDER BY occurrence_count DESC, ai_confidence DESC
                    LIMIT :limit
                """),
                {"country": self.country_code, "limit": limit}
            )
            
            rows = result.fetchall()
            
            return [
                {
                    "id": row[0],
                    "local_name": row[1],
                    "occurrence_count": row[2],
                    "suggested_disease_id": row[3],
                    "suggested_standard_name": row[4],
                    "ai_confidence": row[5],
                    "first_seen": row[6].isoformat() if row[6] else None,
                    "last_seen": row[7].isoformat() if row[7] else None
                }
                for row in rows
            ]
    
    def clear_cache(self):
        """清除内存缓存"""
        self._local_cache.clear()
        self._standard_cache.clear()
        logger.info("🗑️  缓存已清除")


# 兼容接口：支持同步调用（用于Data Processor）
class DiseaseMapperDBSync:
    """同步包装器（用于兼容现有代码）"""
    
    def __init__(self, country_code: str):
        self.mapper = DiseaseMapperDB(country_code)
        self.country_code = country_code
    
    def map_local_to_id(self, local_name: str) -> Optional[str]:
        """同步版本"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.mapper.map_local_to_id(local_name))
    
    def get_standard_name(self, disease_id: str, lang: str = "en") -> Optional[str]:
        """同步版本"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.mapper.get_standard_name(disease_id, lang))
    
    def map_dataframe(
        self,
        df: pd.DataFrame,
        disease_col: str = "disease_name",
        add_id_col: bool = True
    ) -> pd.DataFrame:
        """同步版本"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.mapper.map_dataframe(df, disease_col, add_id_col)
        )
