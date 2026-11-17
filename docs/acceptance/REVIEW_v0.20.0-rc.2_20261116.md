# v0.20.0-rc.2：中国省级修复、历史回补与图片投票独立验收

日期：2026-11-16。基线：`b86708d`，检查对象为当前未提交工作区。此编号只用于分批验收，不表示已经创建 Git tag、提交或正式发布版本。本轮未改业务代码，未执行部署、发布、爬取、修复应用或生产数据库写入。

结论：**有条件不通过**。现有离线回归及三批历史证据核对通过，但发现 3 个可独立复现的 P2 边界问题；建议修复后重验再接受本批自动采集能力。

## 范围与验证

检查了省级 crawler/adapters/表格及正文语义、独立死亡系列、离线响应重放、历史缺月选择、取消流程、受保护修订候选、图片原件封装、固定分母专家投票、健康诊断，以及 collect/plan/apply/verify/configure 脚本。参考现有省级修复、历史回补、图片投票文档，但验证结论来自本次运行。

运行命令：

```bash
venv/bin/python -m pytest tests/unit/test_cn_province*.py tests/unit/test_cn_inner_mongolia_deaths.py tests/unit/test_document_images.py -q
```

结果：**362 passed，0 failed，0 skipped，36.83s**。日志：[rc2-pytest-unique.log](evidence/20261116_review/rc2-pytest-unique.log)。

另用无网络的内存 HTML/DataFrame 反例直接调用正式解析器；结果见 [rc2-adversarial.log](evidence/20261116_review/rc2-adversarial.log)，可重跑脚本为 [rc2-adversarial.py](evidence/20261116_review/rc2-adversarial.py)：

```bash
venv/bin/python docs/acceptance/evidence/20261116_review/rc2-adversarial.py
```

已有测试没有覆盖下述反例，故测试全绿不能替代这些门禁检查。

对本地三批 `before.json.gz` / `after.json.gz` / `plan.json` / `apply_receipt.json` 重新核对：基线 SHA、回执计划 SHA、计划值、非计划旧记录逐字段、意外新增记录及全国/数据中心保护范围汇总均一致。

| 本地证据批次 | 前后观测数 | 计划观测数 | 计划值不符 / 非计划旧值变化 / 意外新增 |
| --- | --- | --- | --- |
| cn_province_repair_20261116 | 23,619 → 42,493 | 19,127 | 0 / 0 / 0 |
| cn_province_history_20261116 | 42,493 → 45,292 | 2,799 | 0 / 0 / 0 |
| cn_province_history_20261116_js2023 | 45,292 → 48,113 | 2,829 | 0 / 0 / 0 |

核对日志：[rc2-local-evidence.log](evidence/20261116_review/rc2-local-evidence.log)。这是对已保存快照的独立离线核对，不是重新读取实时数据库；保护范围只比较快照汇总，没有逐格验证全国/数据中心所有事实，也没有重新查询修订历史表。

## 发现

### P2：数字日期和日区间仍可被当成整月病例

位置：[normalization.py:172](/home/likangguo/globalID/globalID2/src/data/crawlers/cn_province_adapters/parsing/normalization.py:172)、[normalization.py:195](/home/likangguo/globalID/globalID2/src/data/crawlers/cn_province_adapters/parsing/normalization.py:195)。

新增非月度正文门禁识别“今日”“本周”和数字周号，但不识别明确的 `10月15日`、`10月1日至10月7日`。月份核对只比较年月，随后将这些日/短区间计数赋到月初。

复现：以 `MonthlyReportLink('CN-BJ', date(2026,10,1), ..., ...)` 调用 `_normalize_narrative_report`：

```text
2026年10月15日报告病例数为流行性感冒（100例）。
2026年10月1日至10月7日报告病例数为流行性感冒（100例）。
```

两者均产生 `Date=2026-10-01, SourceDiseaseCode=influenza, Cases=100`。若月报页面包含具体某日/某段的说明，便会污染整月计数。这属于本次加强月度语义门禁后的残留缺口，未证明旧版本曾正确处理该输入。应明确拒绝日日期、日区间等非整月证据，再保留合法整月重置语义。

### P2：病例缺失会吞掉同一行明确的死亡数

位置：[normalization.py:161](/home/likangguo/globalID/globalID2/src/data/crawlers/cn_province_adapters/parsing/normalization.py:161)、[cn_provinces.py:973](/home/likangguo/globalID/globalID2/src/data/crawlers/cn_provinces.py:973)。

死亡列是本次新增能力，但解析仍在 `cases is not None` 条件内才读取死亡值。正式输入 `病名=甲型肝炎，发病数='-'，死亡数=2` 时，`_normalize_report_table(..., include_deaths=True)` 返回空列表，`_normalize_tables` 抛出 `No registered disease rows parsed`。因此明确的死亡证据无法进入独立死亡系列；混合表中该行也会静默丢失。

应按指标保留已知死亡值，病例保持缺失，不能为了保留死亡推断病例为零。还需覆盖仅有死亡列的表格与图片缺失病例的适用边界；本次只实证了表格场景。

### P2：补充附件下载失败导致已解析成功的 HTML 整表被丢弃

位置：[cn_provinces.py:873](/home/likangguo/globalID/globalID2/src/data/crawlers/cn_provinces.py:873)。

本次将“首个 HTML 表成功立即返回”改为遍历所有载体，但附件 `_get` 仍在异常处理之外。内存页面含合法 HTML 表（甲型肝炎 3 例、死亡 0）及一个 `broken.xlsx` 链接时，令后者抛 `HTTPError('404 ...')`，整个 `fetch` 失败，`report_diagnostics` 仍为空。正式 collect 随后把整份报告记为失败，不保留已取得的有效 HTML 行。这是多载体遍历引入的回归。

应逐附件保留下载失败诊断，并依既有部分成功/待复核策略处理已取得的明确事实；不能让一个补充附件的临时失败擦掉整份 HTML 结果。

## 已确认的行为与未覆盖范围

现有回归确认：三个月窗口按不同月份选择并保留同月修订；冲突隔离对应月份；死亡与病例分开注册；受保护历史值生成候选；固定 3/5/7 席及多数门槛不随失败/重复模型缩小；模型读取失败、缺页、脚注差异和文本降级不被当成有效票；取消会排空采集线程后结束；整理在已归档响应上重放。

未调用真实模型或外部官网，未重新核验每份历史原图和人工转录。现有图片投票文档中的青海/甘肃/陕西真实调用均为 `needs_review`，不能由离线单测推导为已获真实多数通过。未执行迁移、启用作业、实时数据库验收或站点浏览器验收；本批不能作为部署授权。
