# 疾病数据管理策略 - 应对动态增长

## 🎯 核心挑战

1. **疾病种类不断增加**：新疾病、变种、本地名称
2. **多国家部署**：需要共享标准库
3. **实时更新需求**：无法等待代码发布

## 🏗️ 解决方案：数据库为主 + CSV初始化

### 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    应用启动流程                            │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  1. 启动时检查数据库                                       │
│     ├─ 如果疾病表为空 → 从CSV导入初始数据                   │
│     └─ 如果已有数据 → 直接使用数据库                        │
│                                                           │
│  2. 运行时从数据库读取                                      │
│     ├─ 标准疾病库 (standard_diseases表)                   │
│     ├─ 国家映射 (disease_mappings表)                      │
│     └─ 缓存热数据到Redis                                  │
│                                                           │
│  3. 动态添加新疾病                                         │
│     ├─ 管理API（/api/diseases/add）                       │
│     ├─ 命令行工具（python main.py add-disease）            │
│     └─ 自动学习（爬虫发现未知疾病 → 建议添加）               │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

### 数据流向

```
初始化阶段:
CSV文件 → 导入脚本 → PostgreSQL → 应用启动

运行时阶段:
PostgreSQL ⇄ Redis缓存 ⇄ 应用程序
     ↑              ↑
     │              │
  管理API      爬虫学习（发现新疾病）

版本控制:
PostgreSQL → 导出脚本 → CSV文件 → Git提交
（数据库作为真实来源，定期导出备份）
```

## 📋 数据库表设计

### 1. standard_diseases（标准疾病库）

```sql
CREATE TABLE standard_diseases (
    id SERIAL PRIMARY KEY,
    disease_id VARCHAR(10) UNIQUE NOT NULL,      -- D001, D002, ...
    standard_name_en VARCHAR(200) NOT NULL,      -- COVID-19
    standard_name_zh VARCHAR(200) NOT NULL,      -- 新冠肺炎
    category VARCHAR(50) NOT NULL,                -- Viral, Bacterial, ...
    icd_10 VARCHAR(20),                           -- U07.1
    icd_11 VARCHAR(20),                           -- RA01
    description TEXT,                             -- 疾病描述
    symptoms TEXT,                                -- 症状
    transmission TEXT,                            -- 传播途径
    source VARCHAR(100),                          -- 数据来源
    
    -- AI增强字段
    embedding VECTOR(1536),                       -- OpenAI embedding（语义搜索）
    keywords JSONB,                               -- 关键词列表
    
    -- 元数据
    is_active BOOLEAN DEFAULT TRUE,
    confidence_score FLOAT DEFAULT 1.0,           -- 数据可信度
    last_verified_at TIMESTAMP,                   -- 最后验证时间
    created_by VARCHAR(100),                      -- 创建人
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- 索引
CREATE INDEX idx_standard_disease_name_en ON standard_diseases(standard_name_en);
CREATE INDEX idx_standard_disease_name_zh ON standard_diseases(standard_name_zh);
CREATE INDEX idx_standard_disease_category ON standard_diseases(category);
CREATE INDEX idx_standard_disease_icd10 ON standard_diseases(icd_10);
CREATE INDEX idx_standard_disease_active ON standard_diseases(is_active);

-- 全文搜索索引
CREATE INDEX idx_standard_disease_search ON standard_diseases 
USING GIN(to_tsvector('english', standard_name_en || ' ' || COALESCE(description, '')));
```

### 2. disease_mappings（国家映射表）

```sql
CREATE TABLE disease_mappings (
    id SERIAL PRIMARY KEY,
    disease_id VARCHAR(10) NOT NULL,              -- 关联到标准疾病
    country_code VARCHAR(10) NOT NULL,            -- cn, us, uk, ...
    local_name VARCHAR(500) NOT NULL,             -- 本地名称
    local_code VARCHAR(50),                       -- 本地疾病代码
    
    -- 映射类型
    is_primary BOOLEAN DEFAULT TRUE,              -- 是否主名称
    is_alias BOOLEAN DEFAULT FALSE,               -- 是否别名
    priority INTEGER DEFAULT 0,                   -- 匹配优先级
    category VARCHAR(50),                         -- 本地分类
    
    -- 学习和验证
    source VARCHAR(50),                           -- manual, crawler, ai_learned
    confidence_score FLOAT DEFAULT 1.0,           -- 映射可信度
    verified_by VARCHAR(100),                     -- 验证人
    verified_at TIMESTAMP,                        -- 验证时间
    usage_count INTEGER DEFAULT 0,                -- 使用次数（热度）
    last_used_at TIMESTAMP,                       -- 最后使用时间
    
    -- 元数据
    is_active BOOLEAN DEFAULT TRUE,
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',
    
    FOREIGN KEY (disease_id) REFERENCES standard_diseases(disease_id) ON DELETE CASCADE,
    UNIQUE (country_code, local_name)             -- 同一国家内名称唯一
);

-- 索引
CREATE INDEX idx_mapping_disease ON disease_mappings(disease_id);
CREATE INDEX idx_mapping_country ON disease_mappings(country_code);
CREATE INDEX idx_mapping_local_name ON disease_mappings(local_name);
CREATE INDEX idx_mapping_country_name ON disease_mappings(country_code, local_name);
CREATE INDEX idx_mapping_active ON disease_mappings(is_active);
CREATE INDEX idx_mapping_usage ON disease_mappings(usage_count DESC);
```

### 3. disease_learning_suggestions（学习建议表）

```sql
-- 爬虫发现的未知疾病，等待人工审核
CREATE TABLE disease_learning_suggestions (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(10) NOT NULL,
    local_name VARCHAR(500) NOT NULL,
    
    -- 上下文信息
    source_url TEXT,                              -- 发现来源
    context TEXT,                                 -- 上下文（周围文字）
    occurrence_count INTEGER DEFAULT 1,           -- 出现次数
    first_seen_at TIMESTAMP,                      -- 首次发现
    last_seen_at TIMESTAMP,                       -- 最后发现
    
    -- AI分析
    suggested_disease_id VARCHAR(10),             -- AI建议的disease_id
    suggested_standard_name VARCHAR(200),         -- AI建议的标准名
    ai_confidence FLOAT,                          -- AI置信度
    ai_reasoning TEXT,                            -- AI推理过程
    
    -- 人工处理
    status VARCHAR(20) DEFAULT 'pending',         -- pending, approved, rejected, merged
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMP,
    review_notes TEXT,
    
    -- 最终决策
    final_disease_id VARCHAR(10),                 -- 最终关联的disease_id
    final_mapping_id INTEGER,                     -- 创建的mapping记录ID
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE (country_code, local_name)
);

CREATE INDEX idx_suggestion_status ON disease_learning_suggestions(status);
CREATE INDEX idx_suggestion_country ON disease_learning_suggestions(country_code);
CREATE INDEX idx_suggestion_confidence ON disease_learning_suggestions(ai_confidence DESC);
```

### 4. disease_audit_log（审计日志）

```sql
CREATE TABLE disease_audit_log (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,              -- standard_diseases, disease_mappings
    record_id INTEGER NOT NULL,                   -- 记录ID
    action VARCHAR(20) NOT NULL,                  -- INSERT, UPDATE, DELETE, VERIFY
    
    -- 变更内容
    old_values JSONB,                             -- 旧值
    new_values JSONB,                             -- 新值
    changed_fields TEXT[],                        -- 变更字段列表
    
    -- 操作者信息
    changed_by VARCHAR(100) NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address INET,
    user_agent TEXT,
    
    -- 原因和备注
    reason TEXT,
    notes TEXT,
    
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_audit_table ON disease_audit_log(table_name, record_id);
CREATE INDEX idx_audit_time ON disease_audit_log(changed_at DESC);
CREATE INDEX idx_audit_user ON disease_audit_log(changed_by);
```

## 🔄 数据迁移流程

### 步骤1: 创建迁移脚本

```python
# scripts/migrate_diseases_to_db.py

import pandas as pd
import asyncio
from sqlalchemy import text
from src.core.database import get_db
from src.domain.disease import Disease

async def migrate_standard_diseases():
    """导入标准疾病库"""
    print("📥 导入标准疾病库...")
    
    df = pd.read_csv('configs/standard_diseases.csv')
    
    async with get_db() as db:
        for _, row in df.iterrows():
            # 检查是否已存在
            result = await db.execute(
                text("SELECT id FROM standard_diseases WHERE disease_id = :did"),
                {"did": row['disease_id']}
            )
            if result.scalar_one_or_none():
                print(f"  ⏭️  跳过已存在: {row['disease_id']} - {row['standard_name_en']}")
                continue
            
            # 插入新记录
            await db.execute(text("""
                INSERT INTO standard_diseases (
                    disease_id, standard_name_en, standard_name_zh,
                    category, icd_10, icd_11, description, source,
                    created_by
                ) VALUES (
                    :disease_id, :name_en, :name_zh,
                    :category, :icd_10, :icd_11, :description, :source,
                    'migration_script'
                )
            """), {
                "disease_id": row['disease_id'],
                "name_en": row['standard_name_en'],
                "name_zh": row['standard_name_zh'],
                "category": row.get('category', ''),
                "icd_10": row.get('icd_10'),
                "icd_11": row.get('icd_11'),
                "description": row.get('description'),
                "source": row.get('source', 'CSV')
            })
            
            print(f"  ✅ 导入: {row['disease_id']} - {row['standard_name_en']}")
        
        await db.commit()
    
    print(f"✅ 导入完成，共 {len(df)} 种疾病\n")

async def migrate_country_mappings(country_code='cn'):
    """导入国家映射"""
    print(f"📥 导入 {country_code.upper()} 映射...")
    
    df = pd.read_csv(f'configs/{country_code}/disease_mapping.csv')
    
    async with get_db() as db:
        total = 0
        for _, row in df.iterrows():
            # 主名称
            await db.execute(text("""
                INSERT INTO disease_mappings (
                    disease_id, country_code, local_name, local_code,
                    is_primary, is_alias, category, source, created_by
                ) VALUES (
                    :disease_id, :country_code, :local_name, :local_code,
                    true, false, :category, 'migration', 'migration_script'
                )
                ON CONFLICT (country_code, local_name) DO NOTHING
            """), {
                "disease_id": row['disease_id'],
                "country_code": country_code,
                "local_name": row['local_name'],
                "local_code": row.get('local_code', ''),
                "category": row.get('category', '')
            })
            total += 1
            
            # 别名
            if pd.notna(row.get('aliases')) and row['aliases']:
                import json
                aliases = json.loads(row['aliases']) if isinstance(row['aliases'], str) else row['aliases']
                for alias in aliases:
                    await db.execute(text("""
                        INSERT INTO disease_mappings (
                            disease_id, country_code, local_name,
                            is_primary, is_alias, source, created_by
                        ) VALUES (
                            :disease_id, :country_code, :alias,
                            false, true, 'migration', 'migration_script'
                        )
                        ON CONFLICT (country_code, local_name) DO NOTHING
                    """), {
                        "disease_id": row['disease_id'],
                        "country_code": country_code,
                        "alias": alias
                    })
                    total += 1
        
        await db.commit()
    
    print(f"✅ 导入完成，共 {total} 条映射\n")

async def main():
    """主函数"""
    print("🚀 开始数据迁移...\n")
    
    # 1. 导入标准疾病库
    await migrate_standard_diseases()
    
    # 2. 导入国家映射
    await migrate_country_mappings('cn')
    # await migrate_country_mappings('us')
    
    print("🎉 所有数据迁移完成！")

if __name__ == '__main__':
    asyncio.run(main())
```

### 步骤2: 更新DiseaseMapper使用数据库

```python
# src/data/normalizers/disease_mapper_db.py

from typing import Optional, Dict, List
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.cache import cache_result


class DiseaseMapperDB:
    """
    数据库版疾病映射器
    
    从PostgreSQL读取 + Redis缓存
    """
    
    def __init__(self, country_code: str):
        self.country_code = country_code
        self._cache = {}  # 内存缓存
    
    @cache_result(ttl=3600)
    async def map_local_to_id(self, local_name: str) -> Optional[str]:
        """本地名称 → disease_id"""
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
                
                # 异步更新使用统计
                await db.execute(text("""
                    UPDATE disease_mappings
                    SET usage_count = usage_count + 1,
                        last_used_at = CURRENT_TIMESTAMP
                    WHERE country_code = :country 
                      AND local_name = :name
                """), {"country": self.country_code, "name": local_name})
                await db.commit()
                
                return disease_id
            else:
                # 记录未知疾病
                await self._record_unknown_disease(local_name)
                return None
    
    async def _record_unknown_disease(self, local_name: str):
        """记录未知疾病到学习建议表"""
        async with get_db() as db:
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
    
    @cache_result(ttl=3600)
    async def get_standard_info(self, disease_id: str) -> Optional[Dict]:
        """disease_id → 标准信息"""
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
                return {
                    "disease_id": row[0],
                    "standard_name_en": row[1],
                    "standard_name_zh": row[2],
                    "category": row[3],
                    "icd_10": row[4],
                    "icd_11": row[5],
                    "description": row[6]
                }
            return None
    
    async def add_disease(
        self,
        disease_id: str,
        standard_name_en: str,
        standard_name_zh: str,
        category: str,
        **kwargs
    ) -> int:
        """添加新疾病到标准库"""
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
                "created_by": kwargs.get('created_by', 'system'),
                "source": kwargs.get('source', 'manual')
            })
            await db.commit()
            
            return result.scalar_one()
    
    async def add_mapping(
        self,
        disease_id: str,
        local_name: str,
        **kwargs
    ):
        """添加国家映射"""
        async with get_db() as db:
            await db.execute(text("""
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
            """), {
                "disease_id": disease_id,
                "country": self.country_code,
                "local_name": local_name,
                "local_code": kwargs.get('local_code', ''),
                "is_primary": kwargs.get('is_primary', True),
                "is_alias": kwargs.get('is_alias', False),
                "category": kwargs.get('category', ''),
                "source": kwargs.get('source', 'manual'),
                "created_by": kwargs.get('created_by', 'system')
            })
            await db.commit()
```

## 🛠️ 管理工具

### 1. 命令行工具

```python
# src/cli/disease_commands.py

import click
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB

@click.group()
def disease():
    """疾病管理命令"""
    pass

@disease.command()
@click.option('--disease-id', required=True, help='疾病ID (如 D142)')
@click.option('--name-en', required=True, help='英文名称')
@click.option('--name-zh', required=True, help='中文名称')
@click.option('--category', required=True, help='分类 (Viral/Bacterial/Parasitic/Fungal)')
@click.option('--icd-10', help='ICD-10编码')
@click.option('--icd-11', help='ICD-11编码')
@click.option('--description', help='疾病描述')
async def add(disease_id, name_en, name_zh, category, icd_10, icd_11, description):
    """添加新疾病到标准库"""
    mapper = DiseaseMapperDB('cn')
    
    await mapper.add_disease(
        disease_id=disease_id,
        standard_name_en=name_en,
        standard_name_zh=name_zh,
        category=category,
        icd_10=icd_10,
        icd_11=icd_11,
        description=description,
        created_by='cli'
    )
    
    click.echo(f"✅ 疾病添加成功: {disease_id} - {name_en}")

@disease.command()
@click.option('--country', default='cn', help='国家代码')
async def suggestions(country):
    """查看待审核的疾病建议"""
    async with get_db() as db:
        result = await db.execute(text("""
            SELECT id, local_name, occurrence_count, 
                   suggested_standard_name, ai_confidence
            FROM disease_learning_suggestions
            WHERE country_code = :country AND status = 'pending'
            ORDER BY occurrence_count DESC, ai_confidence DESC
            LIMIT 20
        """), {"country": country})
        
        rows = result.fetchall()
        
        if not rows:
            click.echo("📭 没有待审核的疾病建议")
            return
        
        click.echo(f"\n📋 待审核疾病建议 ({len(rows)} 条):\n")
        for row in rows:
            click.echo(f"  [{row[0]}] {row[1]}")
            click.echo(f"      出现次数: {row[2]}")
            click.echo(f"      AI建议: {row[3]} (置信度: {row[4]:.2f})")
            click.echo()

@disease.command()
@click.argument('suggestion_id', type=int)
@click.option('--disease-id', required=True, help='关联的disease_id')
@click.option('--create-mapping/--no-mapping', default=True, help='是否创建映射')
async def approve(suggestion_id, disease_id, create_mapping):
    """批准疾病建议"""
    async with get_db() as db:
        # 获取建议详情
        result = await db.execute(text("""
            SELECT country_code, local_name
            FROM disease_learning_suggestions
            WHERE id = :id
        """), {"id": suggestion_id})
        row = result.fetchone()
        
        if not row:
            click.echo(f"❌ 未找到建议 #{suggestion_id}")
            return
        
        country_code, local_name = row
        
        # 创建映射
        if create_mapping:
            mapper = DiseaseMapperDB(country_code)
            await mapper.add_mapping(
                disease_id=disease_id,
                local_name=local_name,
                source='ai_learned',
                created_by='cli'
            )
        
        # 更新建议状态
        await db.execute(text("""
            UPDATE disease_learning_suggestions
            SET status = 'approved',
                final_disease_id = :disease_id,
                reviewed_by = 'cli',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {"id": suggestion_id, "disease_id": disease_id})
        await db.commit()
        
        click.echo(f"✅ 已批准: {local_name} → {disease_id}")

@disease.command()
async def stats():
    """统计信息"""
    async with get_db() as db:
        # 标准疾病数
        result = await db.execute(text("SELECT COUNT(*) FROM standard_diseases WHERE is_active = true"))
        total_diseases = result.scalar()
        
        # 各国映射数
        result = await db.execute(text("""
            SELECT country_code, COUNT(*) as cnt
            FROM disease_mappings
            WHERE is_active = true
            GROUP BY country_code
            ORDER BY cnt DESC
        """))
        mappings = result.fetchall()
        
        # 待审核建议数
        result = await db.execute(text("SELECT COUNT(*) FROM disease_learning_suggestions WHERE status = 'pending'"))
        pending = result.scalar()
        
        click.echo("\n📊 疾病数据统计:\n")
        click.echo(f"  标准疾病库: {total_diseases} 种")
        click.echo(f"\n  国家映射:")
        for country, cnt in mappings:
            click.echo(f"    {country.upper()}: {cnt} 条")
        click.echo(f"\n  待审核建议: {pending} 条\n")
```

### 2. 管理API

```python
# src/api/diseases.py

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB

router = APIRouter(prefix="/api/diseases", tags=["diseases"])

class DiseaseCreate(BaseModel):
    disease_id: str
    standard_name_en: str
    standard_name_zh: str
    category: str
    icd_10: Optional[str] = None
    icd_11: Optional[str] = None
    description: Optional[str] = None

@router.post("/add")
async def add_disease(disease: DiseaseCreate):
    """添加新疾病"""
    mapper = DiseaseMapperDB('cn')
    
    try:
        disease_id = await mapper.add_disease(
            disease_id=disease.disease_id,
            standard_name_en=disease.standard_name_en,
            standard_name_zh=disease.standard_name_zh,
            category=disease.category,
            icd_10=disease.icd_10,
            icd_11=disease.icd_11,
            description=disease.description,
            created_by='api',
            source='manual'
        )
        
        return {
            "success": True,
            "disease_id": disease.disease_id,
            "message": "疾病添加成功"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/suggestions")
async def get_suggestions(country: str = 'cn', limit: int = 20):
    """获取待审核的疾病建议"""
    async with get_db() as db:
        result = await db.execute(text("""
            SELECT id, local_name, occurrence_count,
                   suggested_disease_id, suggested_standard_name,
                   ai_confidence, first_seen_at, last_seen_at
            FROM disease_learning_suggestions
            WHERE country_code = :country AND status = 'pending'
            ORDER BY occurrence_count DESC, ai_confidence DESC
            LIMIT :limit
        """), {"country": country, "limit": limit})
        
        rows = result.fetchall()
        
        return {
            "total": len(rows),
            "suggestions": [
                {
                    "id": row[0],
                    "local_name": row[1],
                    "occurrence_count": row[2],
                    "suggested_disease_id": row[3],
                    "suggested_standard_name": row[4],
                    "ai_confidence": row[5],
                    "first_seen": row[6].isoformat(),
                    "last_seen": row[7].isoformat()
                }
                for row in rows
            ]
        }

@router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: int,
    disease_id: str,
    create_mapping: bool = True
):
    """批准疾病建议"""
    # 实现同CLI命令
    pass

@router.get("/search")
async def search_diseases(q: str, limit: int = 10):
    """搜索疾病（支持中英文）"""
    async with get_db() as db:
        result = await db.execute(text("""
            SELECT disease_id, standard_name_en, standard_name_zh, category
            FROM standard_diseases
            WHERE is_active = true
              AND (
                  standard_name_en ILIKE :query
                  OR standard_name_zh ILIKE :query
                  OR disease_id ILIKE :query
              )
            LIMIT :limit
        """), {"query": f"%{q}%", "limit": limit})
        
        rows = result.fetchall()
        
        return {
            "total": len(rows),
            "results": [
                {
                    "disease_id": row[0],
                    "name_en": row[1],
                    "name_zh": row[2],
                    "category": row[3]
                }
                for row in rows
            ]
        }
```

## 📈 应对动态增长的工作流

### 场景1: 新疾病爆发

```bash
# 方式1: 命令行快速添加
python main.py disease add \
  --disease-id D142 \
  --name-en "Mpox Variant 2026" \
  --name-zh "猴痘2026变种" \
  --category Viral \
  --icd-11 "1E71.1"

# 方式2: API调用
curl -X POST http://localhost:8000/api/diseases/add \
  -H "Content-Type: application/json" \
  -d '{
    "disease_id": "D142",
    "standard_name_en": "Mpox Variant 2026",
    "standard_name_zh": "猴痘2026变种",
    "category": "Viral",
    "icd_11": "1E71.1"
  }'

# ✅ 添加后立即生效，无需重启服务
```

### 场景2: 爬虫发现未知疾病

```python
# 爬虫流程中自动处理
class DataProcessor:
    async def process_data(self, df):
        """处理数据"""
        # 1. 尝试映射
        mapped_df = await self.disease_mapper.map_dataframe(df)
        
        # 2. 检查未映射的疾病
        unmapped = mapped_df[mapped_df['disease_id'].isna()]
        
        if not unmapped.empty:
            # 3. 自动记录到学习建议表
            for disease_name in unmapped['disease_name'].unique():
                await self.disease_mapper._record_unknown_disease(disease_name)
        
        # 4. 定期查看建议
        # python main.py disease suggestions
        
        # 5. 批准后自动创建映射
        # python main.py disease approve 123 --disease-id D143
```

### 场景3: AI辅助学习

```python
# 使用AI分析未知疾病
from openai import OpenAI

async def analyze_unknown_disease(local_name: str, context: str):
    """AI分析未知疾病"""
    client = OpenAI()
    
    prompt = f"""
    分析以下疾病名称，推荐对应的标准疾病：
    
    本地名称: {local_name}
    上下文: {context}
    
    现有标准疾病库: [从数据库查询]
    
    请判断：
    1. 这是否是已知疾病的别名？如果是，给出disease_id
    2. 是否是新疾病？如果是，建议英文标准名和分类
    3. 置信度评分 (0-1)
    """
    
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    
    # 解析AI响应，更新learning_suggestions表
    # ...
```

## 🔄 定期维护

### 1. 导出到CSV（备份）

```python
# scripts/export_diseases_to_csv.py

async def export_to_csv():
    """从数据库导出到CSV"""
    async with get_db() as db:
        # 导出标准库
        result = await db.execute(text("SELECT * FROM standard_diseases WHERE is_active = true"))
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
        df.to_csv('configs/standard_diseases.csv', index=False)
        
        # 导出各国映射
        for country in ['cn', 'us', 'uk']:
            result = await db.execute(text("""
                SELECT disease_id, local_name, local_code, category,
                       ARRAY_AGG(alias_name) FILTER (WHERE is_alias) as aliases
                FROM disease_mappings
                WHERE country_code = :country AND is_active = true
                GROUP BY disease_id, local_name, local_code, category
            """), {"country": country})
            
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
            df.to_csv(f'configs/{country}/disease_mapping.csv', index=False)
    
    print("✅ 导出完成，请提交到Git")
```

### 2. 定期审核

```bash
# 每周审核一次未知疾病
python main.py disease suggestions

# 查看使用频率低的映射（可能是错误映射）
python main.py disease audit --low-usage

# 查看AI建议
python main.py disease suggestions --sort-by confidence
```

## 📊 监控指标

### Grafana Dashboard

```yaml
疾病数据健康度:
  - 标准疾病库数量趋势
  - 各国映射覆盖率
  - 未知疾病发现率（trends）
  - 映射使用热度 Top 20
  - 待审核建议数量
  - 数据质量评分

告警规则:
  - 待审核建议 > 50 条
  - 未知疾病比例 > 5%
  - 映射覆盖率 < 90%
  - 某个疾病出现频率异常高（可能是爆发）
```

## 🎯 总结

### 优势

1. **动态扩展** ✅
   - 新疾病快速添加
   - 无需代码修改
   - 无需重启服务

2. **智能学习** ✅
   - 自动发现未知疾病
   - AI辅助分析
   - 人工审核确认

3. **数据可靠** ✅
   - 数据库+CSV双备份
   - 审计日志完整
   - 版本控制

4. **易于维护** ✅
   - Web管理界面
   - CLI命令工具
   - API集成

### 最佳实践

1. **初始导入**: 从CSV导入基础数据
2. **运行时**: 使用数据库+缓存
3. **发现新疾病**: 记录到建议表
4. **人工审核**: 批准/拒绝
5. **定期备份**: 导出到CSV，提交Git
6. **持续优化**: 根据使用频率调整优先级
