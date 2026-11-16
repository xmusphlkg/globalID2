# v0.20.0-rc.4 修复复验：态势宿主与公共输出

2026-11-16。对应原审查中的宿主物种分组不一致问题。

导出地点身份使用 NFKC、连续空白合并及小写，与前端一致；不使用会移除物种名内部连字符的 AI 引用匹配规则。首页去重身份同时包含宿主类型和物种，防止同日同病同地的人类、家禽、野鸟及未知宿主互相覆盖。

新增回归验证 `Wild  birds` / `wild birds` 合并、不同宿主分开、`Black-headed gulls` / `Blackheaded gulls` 保持不同，并核对首页和地图计数一致。经另一 subagent 交叉检查。

最终针对性命令包含 situation export/hosts/feed/json publication/release validation/signal pilot 及 homepage：**89 passed in 12.19s，0 failed/0 skipped**。此前更大相关范围 165 passed，两个范围重叠，不相加。日志为 evidence/20261116_fixes/rc4-final.log；前端及 Worker 最终结果另见整体发布验收。

此轮没有发送订阅邮件或调用真实外部采集；RSS/宿主功能随原有本日更新一起进入最终版本，真实公开输出由发布后检查验证。
