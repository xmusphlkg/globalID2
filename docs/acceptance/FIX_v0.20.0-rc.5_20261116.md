# v0.20.0-rc.5 修复复验：自动化与通知事务

2026-11-16。原审查 1 项 P1、2 项 P2 均已修复。

1. API 健康状态枚举接受 `stale_source`；同步更新 OpenAPI 和生成客户端类型。回归通过实际 snapshot 的 AutomationConfigOut 与路由输出合同，避免只测试枚举本身。
2. 自动触发在入队前读取持久化日程，已暂停或改到未来的任务返回 `not_due`。手动运行原先暂停的任务后仍保持暂停；结果保存保留现有 compare-and-swap，防止覆盖并发控制面编辑。
3. 处理通知 opt-out 时不再中途 commit 释放保留事件的行锁；同一事务先将保留事件设为 sending。全批退订时正常持久化 skipped。回归在每次 commit 检查状态，覆盖混合批次与全退订，不发送真实邮件。

相关 Python 回归 **179 passed，0 failed**（rc5-python.log）。后台全套 **40 文件、170 passed**，`tsc --noEmit` 和 `openapi:check` 均通过；OpenAPI JSON 与生成 TypeScript 均为 ok。证据保存在 evidence/20261116_fixes/。另一 subagent 已交叉检查上述修复，未发现新阻断项。

结果是离线/替身测试与合同验收，不声称真实邮件已送达；正式运行时需由安全 drain/restart 载入代码并检查 readiness。
