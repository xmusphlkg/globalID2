# 历史数据与兼容映射重建

`scripts/full_rebuild_database.py` 是现有历史恢复和兼容映射入口，CI 也使用其 mappings
模式。它仍被调用，不能作为废弃脚本删除。它不是日常数据库启动、Alembic 升级或 v3
来源语义修改的替代流程。

## 模式与输入

| 模式 | 用途 | 主要边界 |
| --- | --- | --- |
| `mappings` | 同步标准库、兼容映射和疾病记录 | 保留历史观测，不导入历史 CSV；仍会修改映射数据 |
| `history` | 从已准备的文件重导兼容历史 | 清理并重建相应历史记录，保留映射和抓取审计 |
| `full` | 映射、疾病及兼容历史的组合重建 | 包含删除与重导；执行前核对实际计划和国家范围 |
| `custom` | 交互选择清理和导入步骤 | 根据选定步骤产生计划，不能假定等同于某个固定模式 |

输入包括 `configs/standard_diseases.csv`、`configs/mapping/<scope>.csv`、英文兼容
映射，以及 `data/history/<country>/history_merged.csv`。国家参数默认 `cn`；
原始文件和历史 CSV 不随源码仓库提供。

计划及清理边界由 [database_rebuild_plan.py](../../src/services/database_rebuild_plan.py)
定义。先在交互模式确认实际步骤，不能根据文件名推断所有表都受国家范围保护。
来源系列与 Registry v3 的修改遵循[疾病本体文档](../architecture/DISEASE_ONTOLOGY_V3.md)。

## 使用方式

```bash
# 查看参数，不连接数据库执行业务。
venv/bin/python scripts/full_rebuild_database.py --help

# 交互式检查并确认 CN 兼容映射同步。
venv/bin/python scripts/full_rebuild_database.py --country cn --mode mappings

# 历史恢复：仅在输入文件、目标数据库和重建范围已经核实后执行。
venv/bin/python scripts/full_rebuild_database.py --country cn --mode history \
  --checkpoint-file logs/database-rebuild/cn-history-reviewed.json
```

`--yes` 跳过确认；不指定模式时配合该选项会采用完整重建。正常启动见
[快速开始](../../QUICKSTART.md)，不要把完整重建当作修复缺表、连接失败或数据未刷新的通用办法。

## 恢复与验证

持久运行报告默认写到 `logs/database-rebuild/<country>-<mode>-latest.json`；
需要保留独立记录时使用 `--checkpoint-file`。执行失败后按报告检查已完成阶段，
不要把重复运行视为事务级回滚。核对导入数量、来源范围、缺失值和实际覆盖日期，
不能把旧文档里的固定记录数当作当前验收标准。

```bash
venv/bin/python scripts/cn_data_quality_check.py
venv/bin/python scripts/check_legacy_disease_access.py
```

CN 检查针对旧兼容投影；其他来源还需各自的质量检查和 v3 来源系列验收。
美国 NNDSS 的居民口径与更广的 Total 口径必须保持分离，历史恢复工具会检查其输入。

## 入口整理

旧的单语言刷新、直接重导、临时任务建表和手工插入 CN 脚本已移除。
缺失模型表和基础 CN 元数据使用 `main.py init-database`；已有库升级按
[迁移说明](../../migrations/README.md)执行。

CHFL、country scopes、旧任务归属和知识流程迁移保留在
[scripts/maintenance](../../scripts/maintenance/README.md)。
历史 CSV 预处理在 [scripts/imports](../../scripts/imports/README.md)。
数据库 schema 全量删除重建工具仅作为显式恢复工具保留，不代替 Alembic。
