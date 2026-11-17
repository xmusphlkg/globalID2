# v0.20.0-rc.2 修复复验：中国省级自动采集边界

日期：2026-11-16。本报告对应 [rc.2 独立验收](REVIEW_v0.20.0-rc.2_20261116.md) 的 3 个 P2。编号为验收批次，正式版本、提交及部署由主 agent 统一处理。

结论：**3 项已修复，离线复验通过：394 passed，0 failed，0 skipped**。本批没有执行生产爬取、观测写入、修复应用、邮件发送或部署。

## 修复结果

1. 月度正文识别拒绝明确日日期、数字日期、中文日号及不足整月的日期范围。合法的年月/本月/当月继续支持；明确从月初至该月最后一天的中文日期范围按真实月长判断，包含闰年二月。校验使用规范化期间，Raw 证据仍保留原始日期表达。原来把 10 月 15 日或 10 月 1—7 日的 100 例赋给整月的反例，现在均返回空结果。
2. 表格按病例、死亡分别保留明确证据。有病例列但单元格缺失、死亡明确时，输出病例 `NULL / source_missing` 和独立的数值死亡系列；只有死亡列时仅生成死亡事实。明确死亡 0 仍为 0，缺失病例不会变成 0。指标扩展清除从病例继承的缺失标记，CLI 预检接受显式 source_missing，重放和多载体合并让已知病例优先于未知病例。未知病例不会推进“最近明确病例月份”。
3. 每个补充附件的下载失败分别记录 `attachment_fetch_failed`，保留已解析的 HTML 明确事实，继续处理其他载体。在线采集和归档离线重放都传播相同诊断并标记 partial；原件不完整时阻止图片专家准入并保持 needs_review。取消异常继续向外传播。新增回归也发现并修复了重放诊断未进入总审计的问题，避免“保住数据却错误标记 parsed”。

## 验证

新增 `tests/unit/test_cn_province_acceptance_regressions.py`，共 32 个展开后的回归案例，覆盖三种正文提取方式的日/短期间反例、合法整月及闰年、缺失病例与正/零死亡经正式 store 构建和 CLI 预检、仅死亡列、多载体未知/已知协调、附件 404、离线无成功附件原件、病例新鲜度、取消和不完整图片原件门禁。

完整省级相关测试命令：

```bash
venv/bin/python -m pytest -q tests/unit/test_cn_province*.py tests/unit/test_cn_inner_mongolia_deaths.py tests/unit/test_document_images.py
```

结果：**394 passed in 39.52s**。证据：[rc2-full.log](evidence/20261116_fixes/rc2-full.log)。修改文件的 `git diff --check` 通过。

另外重新运行原验收的无网络对抗脚本，确认全部 3 类原失败输入已经改变为预期行为：

```bash
venv/bin/python docs/acceptance/evidence/20261116_review/rc2-adversarial.py
```

输出：[rc2-original-counterexamples.log](evidence/20261116_fixes/rc2-original-counterexamples.log)。脚本仅输出对比结果；断言由正式新增回归执行。

## 精确文件范围与边界

本批修改的业务/脚本文件为以下 5 个，保留文件中此前已有工作区更新：

- `src/data/crawlers/cn_province_adapters/parsing/normalization.py`
- `src/data/crawlers/cn_provinces.py`
- `src/data/processors/cn_provinces.py`
- `src/services/ingestion/countries/cn_province.py`
- `scripts/update_cn_provinces.py`

另新增上述回归文件及本报告。未修改通用 source store、自动化日程、通知模块、其他国家模块或地图文件。

本轮验证使用本地输入、请求替身和归档重放，没有重新核验真实官网、数据库或真实专家模型。已有图片投票的多数门槛和不确定输出门禁继续生效；表格支持独立缺失指标，不代表图片识别可以凭空补出缺失单元格。真实来源可用性与公开发布状态仍由后续统一验收决定。
