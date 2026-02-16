# 疾病数据库集成 - 快速开始

## 🎯 为什么需要数据库？

你的项目有两个关键特点：

1. **疾病种类会不断增加** ⚠️
   - 新疾病爆发（如COVID变种）
   - 各国发现本地名称
   - 疾病信息更新（ICD编码、描述）

2. **全球多国家部署** 🌍
   - 需要共享标准疾病库
   - 多实例需要数据一致性
   - 分布式爬虫协同工作

**CSV方案的局限**：
- ❌ 添加新疾病需要修改代码、提交、重启
- ❌ 多实例需要同步配置文件
- ❌ 无法动态学习和适应

**数据库方案的优势**：
- ✅ 通过API/CLI快速添加新疾病，无需重启
- ✅ 多实例自动共享最新数据
- ✅ 爬虫发现未知疾病，自动记录学习
- ✅ 完整审计日志
- ✅ 支持语义搜索和AI增强

## 🚀 快速开始（5分钟）

### 步骤1: 运行迁移脚本

```bash
# 将CSV数据导入PostgreSQL
python scripts/migrate_diseases_to_db.py
```

**执行内容**：
- 创建4张表（standard_diseases, disease_mappings, learning_suggestions, audit_log）
- 导入127种标准疾病
- 导入中国映射（58主名称 + 48别名 = 106条）
- 验证数据完整性

**预期输出**：
```
🚀 GlobalID V2 - 疾病数据迁移
============================================================

📐 创建数据库表...
✅ 数据库表创建完成

📑 创建索引...
✅ 索引创建完成

📥 导入标准疾病库: configs/standard_diseases.csv
   读取 127 条记录
✅ 标准疾病库导入完成: 成功 127, 跳过 0, 总计 127

📥 导入 CN 映射: configs/cn/disease_mapping.csv
   读取 58 条主映射
✅ CN 映射导入完成: 共 106 条

🔍 验证迁移结果...
============================================================
📊 迁移结果统计:
   标准疾病库: 127 种
   国家映射:
     - CN: 106 条
============================================================

🧪 测试关键疾病映射:
   ✅ 新冠肺炎 → D004 (COVID-19)
   ✅ 肺结核 → D025 (Tuberculosis)
   ✅ 艾滋病 → D005 (HIV/AIDS)

✅ 所有测试通过！

============================================================
🎉 迁移完成！
============================================================
```

### 步骤2: 运行测试

```bash
# 测试数据库版映射器
python tests/test_disease_mapper_db.py
```

**测试内容**：
- 基本映射功能
- 别名映射
- DataFrame批量处理
- 统计信息
- 未知疾病学习
- 动态添加疾病

**预期通过率**: 100%

### 步骤3: 使用命令行工具

```bash
# 查看统计信息
python scripts/disease_cli.py stats

# 搜索疾病
python scripts/disease_cli.py search "新冠"

# 查看待审核建议
python scripts/disease_cli.py suggestions
```

## 📘 使用场景

### 场景1: 新疾病爆发

```bash
# 2026年发现猴痘新变种，快速添加
python scripts/disease_cli.py add-disease D142 \
  "Mpox Variant 2026" "猴痘2026变种" Viral \
  --icd-11 "1E71.1" \
  --description "2026年新发猴痘变种"

# 添加中国本地名称
python scripts/disease_cli.py add-mapping D142 "猴痘新变种"

# ✅ 立即生效，无需重启服务
```

### 场景2: 爬虫发现未知疾病

```python
# 爬虫自动运行时会记录未知疾病
# 查看建议
python scripts/disease_cli.py suggestions

# 输出:
# 📋 待审核疾病建议 (CN):
# 
# 1. [123] 未知疾病X
#    出现次数: 15
#    AI建议: Disease X (置信度: 0.85)

# 批准建议（自动创建映射）
python scripts/disease_cli.py approve 123 D143
```

### 场景3: 批量导入新数据

```python
# scripts/import_who_diseases.py
import pandas as pd
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB

async def import_who_data():
    """从WHO导入新疾病"""
    mapper = DiseaseMapperDB('cn') df = pd.read_csv('who_diseases_2026.csv')
    
    for _, row in df.iterrows():
        await mapper.add_disease(
            disease_id=row['disease_id'],
            standard_name_en=row['name_en'],
            standard_name_zh=row['name_zh'],
            category=row['category'],
            icd_11=row['icd_11'],
            source='WHO_2026'
        )
    
    print(f"✅ 导入 {len(df)} 种疾病")
```

### 场景4: API集成

```python
# 通过API添加疾病（支持Web管理界面）
import requests

response = requests.post('http://localhost:8000/api/diseases/add', json={
    "disease_id": "D142",
    "standard_name_en": "New Disease",
    "standard_name_zh": "新疾病",
    "category": "Viral"
})

print(response.json())
# {"success": true, "disease_id": "D142", "message": "疾病添加成功"}
```

## 🔄 日常维护

### 每周审核

```bash
# 查看待审核建议
python scripts/disease_cli.py suggestions

# 批准高置信度建议
python scripts/disease_cli.py approve <id> <disease_id>
```

### 每月备份

```bash
# 从数据库导出到CSV（用于版本控制）
python scripts/export_diseases_to_csv.py

# 提交到Git
git add configs/
git commit -m "Update disease data - $(date +%Y-%m-%d)"
git push
```

### 监控数据质量

```bash
# 统计信息
python scripts/disease_cli.py stats

# 查看使用热度
# TODO: 实现热度分析工具
```

## 📊 数据库表结构

### 1. standard_diseases (标准疾病库)
- 127种全球标准疾病
- ICD-10/11编码
- 支持语义搜索

### 2. disease_mappings (国家映射)
- 多国家本地名称映射
- 支持主名称和别名
- 记录使用频率

### 3. disease_learning_suggestions (学习建议)
- 爬虫发现的未知疾病
- AI分析和建议
- 等待人工审核

### 4. disease_audit_log (审计日志)

### 5. crawl_runs (爬取运行记录)
- 记录每次爬取的国家、数据源、状态、统计信息
- 对应原始文件目录 `data/raw/<country>/<run_id>/...`

### 6. crawl_raw_pages (原始页面索引)
- 保存 URL、纯文本文件路径、哈希、抓取时间
- 便于审计与追溯解析结果
- 所有变更记录
- 操作者追踪
- 合规支持

## 🆚 CSV vs 数据库对比

| 场景 | CSV方案 | 数据库方案 |
|------|---------|------------|
| 添加新疾病 | 修改文件 → 提交代码 → 重启服务 | CLI命令 → 立即生效 |
| 多实例部署 | 需要同步配置文件 | 自动共享数据 |
| 未知疾病 | 手动发现和处理 | 自动记录、AI建议 |
| 数据审计 | 依赖Git历史 | 完整审计日志 |
| 查询性能 | 快（内存） | 快（索引+缓存） |
| 实施成本 | 低 | 中 |

## ⚠️ 注意事项

1. **数据库连接**: 确保DATABASE_URL正确配置
2. **权限管理**: 生产环境需要控制添加/修改权限
3. **缓存策略**: 数据库版本内置内存缓存，性能接近CSV
4. **备份策略**: 定期导出到CSV，双重保障
5. **审核流程**: 未知疾病建议必须人工审核后才能使用

## 📚 相关文档

- [完整设计文档](DISEASE_MANAGEMENT_STRATEGY.md) - 详细架构和实现
- [数据库方案对比](DATABASE_INTEGRATION.md) - CSV vs 数据库深度分析
- [数据采集文档](DISEASE_DATA_COLLECTION.md) - 从权威数据源采集

## 🤝 下一步

1. ✅ 运行迁移脚本
2. ✅ 运行测试验证
3. ✅ 尝试命令行工具
4. 🔄 更新应用代码使用DiseaseMapperDB
5. 🔄 配置定期备份任务
6. 🔄 实现Web管理界面（可选）

---

**准备好了吗？** 运行 `python scripts/migrate_diseases_to_db.py` 开始吧！ 🚀
