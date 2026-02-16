# 已废弃的脚本

## refresh_disease_mappings.py

**废弃原因：** 不支持多语言映射架构

**替代方案：** 使用 `full_rebuild_database.py`

### 为什么废弃？

1. ❌ **只能单语言映射** - 每次只能导入 CN 或 EN 的映射，需要运行多次
2. ❌ **不适应多语言架构** - 不会自动检测和导入英文映射文件
3. ❌ **功能重复** - `full_rebuild_database.py` 已包含所有功能并更强大

### 新的使用方式

```bash
# 完整重建（推荐）
./venv/bin/python scripts/full_rebuild_database.py --yes

# 交互式选择模式
./venv/bin/python scripts/full_rebuild_database.py

# 仅更新映射（不重新导入历史数据）
./venv/bin/python scripts/full_rebuild_database.py --mode mappings --yes

# 仅导入历史数据（不修改映射）
./venv/bin/python scripts/full_rebuild_database.py --mode history --yes

# 自定义选择步骤
./venv/bin/python scripts/full_rebuild_database.py --mode custom
```

### 新功能优势

✅ **多语言支持** - 自动导入 CN + CN_EN 映射
✅ **灵活模式** - 4种重建模式可选
✅ **智能步骤** - 根据模式自动调整执行步骤
✅ **历史数据** - 同时支持映射和历史数据导入

---

**迁移日期：** 2026-02-16
**建议：** 请更新你的脚本和文档，使用新的命令
