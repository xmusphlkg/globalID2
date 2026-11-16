# Situation 宿主字段数据库迁移验收 — 2026-11-16

北京时间 2026-11-16 07:49，按用户“开始迁移数据库”的授权，对当前运行数据库
`globalid` 执行 Alembic 迁移。此次操作仅迁移数据库结构；后端代码加载、来源重抽取、
Situation JSON、Worker 与网页发布仍为后续发布工作。

## 执行与结果

| 项目 | 验证结果 |
| --- | --- |
| 迁移前版本 | `0031_ai_success_log_index` |
| 迁移后版本 | `0032_situation_host_metadata` |
| 操作 | `venv/bin/alembic upgrade 0032_situation_host_metadata` |
| 时间 | UTC 2026-11-15 23:49:08 开始；07:49 为对应北京时间。Alembic 进程耗时 2.910 秒。 |
| 超时设置 | PostgreSQL `lock_timeout=5s`、`statement_timeout=60s`；迁移为事务性 DDL。 |
| 历史行数 | 前后均为 3,901 行。 |
| 原有字段完整性 | 以原有全部字段按 ID 排序、UTC 时间序列化后计算 SHA-256，迁移前后完全一致。 |
| 新字段 | `host_type`、`host_species`、`count_unit`、`host_evidence` 均存在。 |
| 历史默认值 | 3,901 行均为 `host_type=unknown`、`count_unit=unknown`，物种与证据为 NULL。必填字段无 NULL。 |
| 新 ORM 读取 | 使用当前 `SituationAlert` 模型查询通过。 |
| 运行健康 | 迁移前后本机 API `/health` 均 HTTP 200、`status=ok`。 |
| 聚焦回归 | `tests/unit/test_situation_hosts.py` 共 12 项通过，包含迁移升级/回滚、旧记录默认值和宿主身份隔离。 |

原有字段的前后校验值均为：

```text
ae54cc1d3eff3ea81dba2e89ca8feb4a6c2924bc197a6fed66555977397e1c85
```

没有执行历史宿主分类或主动批量重抽取，也没有通过本次操作发布页面或发送邮件。

## 备份与收据

操作前备份了本次受影响的 `public.situation_alerts` 和 `public.alembic_version`，
不是全库备份。自定义格式归档为 882,275 字节，使用完整 `pg_restore --file=/dev/null`
解码校验并读取目录通过；本次没有进行实际恢复演练。

证据位于本地忽略目录：

```text
backups/situation-host-migration-20261115T234824Z/
  situation-before.dump
  before.json
  backup-toc.txt
  backup-summary.json
  alembic-upgrade.log
  migration-result.json
```

目录权限为 0700，文件权限为 0600，归档 SHA-256：

```text
56901aae84d99596b263d4c5618ff07e177f32b20d0cacdde18f99162acd9317
```

恢复或回滚需要结合当前写入状态评估。新代码查询这些列时不能直接移除列；
有新宿主事实写入后也不能无条件恢复旧表。后续代码加载与发布依赖见
[One Health 一期说明](../operations/SITUATION_ONE_HEALTH_PHASE1.md) 和
[功能一期验收](PUBLIC_FEATURE_PILOT_20261115.md)。
