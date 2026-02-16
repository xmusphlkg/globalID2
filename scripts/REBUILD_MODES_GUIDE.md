# 数据库重建模式使用指南

## 概述

`full_rebuild_database.py` 现在支持 **4 种重建模式**，可以灵活地管理数据库重建过程。

## 📋 可用模式

### 1. 完整重建 (Full Rebuild)

**用途：** 完全重建数据库，清空所有数据并重新导入。

**操作：**
- ✅ 清空 disease_records（历史数据）
- ✅ 清空 diseases（疾病表）
- ✅ 清空 disease_mappings（映射关系）
- ✅ 清空 standard_diseases（标准疾病库）
- ✅ 导入标准疾病库
- ✅ 导入疾病映射（中文 + 英文）
- ✅ 同步疾病表
- ✅ 导入历史数据

**命令：**
```bash
# 交互式（需要确认）
./venv/bin/python scripts/full_rebuild_database.py

# 自动确认
./venv/bin/python scripts/full_rebuild_database.py --yes

# 指定模式
./venv/bin/python scripts/full_rebuild_database.py --mode full --yes
```

**适用场景：**
- 数据库完全损坏需要重建
- 数据结构发生重大变更
- 需要从零开始初始化数据库

---

### 2. 仅更新映射 (Mappings Only)

**用途：** 更新疾病映射规则，保留历史数据不动。

**操作：**
- ❌ 不清空 disease_records（**历史数据保留**）
- ❌ 不清空 diseases（通过 UPSERT 更新）
- ✅ 清空 disease_mappings
- ✅ 清空 standard_diseases
- ✅ 导入标准疾病库
- ✅ 导入疾病映射（中文 + 英文）
- ✅ 同步疾病表
- ❌ 不导入历史数据

**命令：**
```bash
./venv/bin/python scripts/full_rebuild_database.py --mode mappings --yes
```

**适用场景：**
- 修改了 `configs/cn/disease_mapping.csv`
- 修改了 `configs/en/disease_mapping.csv`
- 更新了标准疾病库 `configs/standard_diseases.csv`
- 添加了新的疾病映射规则
- 修复了映射错误（如模糊匹配问题）

**保证：**
- ✅ 历史数据完全保留（8,785+ 条记录不受影响）
- ⚠️ diseases 表通过 UPSERT 更新，不会触发级联删除

---

### 3. 仅导入历史数据 (History Only)

**用途：** 重新导入历史数据，不修改映射表。

**操作：**
- ✅ 清空 disease_records
- ❌ 不修改 diseases
- ❌ 不修改 disease_mappings
- ❌ 不修改 standard_diseases
- ❌ 不导入映射
- ✅ 导入历史数据

**命令：**
```bash
./venv/bin/python scripts/full_rebuild_database.py --mode history --yes
```

**适用场景：**
- 修改了历史数据源文件 `data/processed/cn/history_merged.csv`
- 发现历史数据导入有误需要重新导入
- 数据质量问题修复后重新导入
- 历史数据去重或清理后重新导入

**保证：**
- ✅ 映射表完全不受影响
- ✅ 疾病库不受影响

---

### 4. 自定义模式 (Custom)

**用途：** 交互式选择要执行的步骤。

**操作：** 用户可以选择：
1. 是否清空现有数据？
2. 是否导入标准疾病库？
3. 是否导入疾病映射？
4. 是否同步疾病表？
5. 是否导入历史数据？

**命令：**
```bash
./venv/bin/python scripts/full_rebuild_database.py --mode custom
```

**适用场景：**
- 特殊的维护需求
- 测试和调试
- 部分数据更新

---

## 🔧 命令行参数

| 参数 | 短参数 | 说明 | 默认值 |
|------|--------|------|--------|
| `--mode` | `-m` | 重建模式：full, mappings, history, custom | 无（交互式） |
| `--yes` | `-y` | 自动确认，跳过提示 | False |
| `--country` | 无 | 国家代码 | cn |

---

## 📊 测试结果

### ✅ Mappings 模式测试
```
清空的表：
  • disease_mappings: deleted 255 records
  • standard_diseases: deleted 124 records

保留的表：
  • disease_records (历史数据): 8,785 records ✅
  • crawl_runs, crawl_raw_pages

最终结果：
  • Standard Diseases: 124 records
  • Disease Mappings (CN): 121 mappings
  • Diseases Table: 124 records
  • Historical Records: 8,785 records ✅ 保留成功
  • Time Range: 2010-01-01 to 2025-12-01
```

### ✅ History 模式测试
```
清空的表：
  • disease_records: deleted 8,785 records

保留的表：
  • diseases, disease_mappings, standard_diseases
  • crawl_runs, crawl_raw_pages

导入结果：
  • Imported 8,833 historical records
  • Time Range: 2010-01-01 to 2025-12-01
```

### ✅ Full 模式测试
```
清空的表：
  • disease_records: deleted 8,785 records
  • diseases: deleted 124 records
  • disease_mappings: deleted 255 records
  • standard_diseases: deleted 124 records

导入结果：
  • Standard Diseases: 124 records
  • Disease Mappings: 265 relationships (CN + EN)
  • Diseases Table: 124 records
  • Historical Records: 8,785 records
```

---

## ⚠️ 注意事项

### 外键约束
- `disease_records` 表通过外键引用 `diseases.id`
- 外键设置为 `ON DELETE CASCADE`
- 删除 diseases 表记录会自动删除相关历史记录
- **Mappings 模式通过 UPSERT 避免触发级联删除**

### 数据安全
- Full 模式会删除所有历史数据，请谨慎使用
- Mappings 模式安全：历史数据完全保留
- History 模式安全：映射表完全保留
- 建议在生产环境操作前先备份数据库

### 性能
- Full 模式最慢（~2-3秒）
- Mappings 模式较快（~0.5秒）
- History 模式中等（~1-2秒）

---

## 📝 使用示例

### 场景 1：修复映射错误
问题：发现"肝炎"错误匹配到"甲型肝炎"
```bash
# 1. 修改 configs/cn/disease_mapping.csv
# 2. 运行 mappings 模式
./venv/bin/python scripts/full_rebuild_database.py --mode mappings --yes
```

### 场景 2：添加新映射
问题：需要添加新的疾病映射规则
```bash
# 1. 编辑 configs/cn/disease_mapping.csv
# 2. 运行生成英文映射
./venv/bin/python scripts/generate_english_mappings.py
# 3. 更新映射表
./venv/bin/python scripts/full_rebuild_database.py --mode mappings --yes
```

### 场景 3：历史数据清理
问题：发现历史数据有重复，需要清理后重新导入
```bash
# 1. 清理 data/processed/cn/history_merged.csv
# 2. 重新导入历史数据
./venv/bin/python scripts/full_rebuild_database.py --mode history --yes
```

### 场景 4：初始化新环境
问题：新服务器需要初始化数据库
```bash
./venv/bin/python scripts/full_rebuild_database.py --mode full --yes
```

---

## 🔄 迁移说明

### 从旧脚本迁移

旧脚本（已废弃）：`scripts/deprecated/refresh_disease_mappings.py`

迁移对照表：
| 旧命令 | 新命令 |
|--------|--------|
| `refresh_disease_mappings.py --yes` | `full_rebuild_database.py --mode mappings --yes` |
| `refresh_disease_mappings.py` | `full_rebuild_database.py --mode mappings` |

**为什么废弃：**
- 旧脚本只支持单一语言映射
- 不支持多语言架构（CN + CN_EN）
- 缺少灵活的模式选择
- 新脚本功能更强大且向后兼容

---

## 📚 相关文档

- [数据库快速入门](../docs/DATABASE_QUICKSTART.md)
- [疾病映射 V2 设计](../docs/DISEASE_MAPPING_V2.md)
- [疾病数据管理策略](../docs/DISEASE_MANAGEMENT_STRATEGY.md)
- [废弃脚本说明](./deprecated/README.md)

---

## 🐛 问题排查

### 问题：Mappings 模式后历史数据丢失
**原因：** 可能使用了旧版本脚本
**解决：** 使用最新版本，确保 clear_data 方法不删除 diseases 表

### 问题：外键约束错误
**原因：** 尝试删除被引用的疾病
**解决：** 使用 Mappings 模式（UPSERT）而不是删除重建

### 问题：历史数据导入失败
**原因：** 映射表不存在或为空
**解决：** 先运行 Mappings 模式或 Full 模式

---

**最后更新：** 2026-02-16
**版本：** 2.0
**作者：** GlobalID Team
