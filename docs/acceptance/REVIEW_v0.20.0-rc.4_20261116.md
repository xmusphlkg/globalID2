# v0.20.0-rc.4 Situation、RSS 与公共试点独立验收

日期：2026-11-16（Asia/Shanghai）。基线：`b86708d`，范围为当前工作区。编号仅标识验收批次，不修改应用版本、不创建 tag。本轮没有部署、发布、生产数据库写入、重抽取或发信。

## 结论

现有离线回归与 Worker 本地运行验证通过；独立反例确认 **1 项 P2**：宿主物种的分组规范化在提取/聚类与地图导出之间不一致，使同一地点重复导出、首页与地图数量不一致。此问题修正并复验后再批准该批次。未发现确认 P1。

## 范围

- 宿主字段迁移、AI 证据约束、宿主/物种身份、重抽取 ID 保留、聚类、公共摘要及图表隔离。
- 前端宿主筛选、深链接、历史 unknown 标签、动物与人类计数单位。
- RSS 冻结导出、独立 Situation current、实体分片、Worker gzip/摘要/长度限制/XML/缓存/只读方法。
- 6 个病原体资源映射及三语说明；月度算法信号试点的来源隔离、日期与质量门禁。

## 问题

### P2：物种大小写变化使同一动物疫情地点导出两次

定位：`/home/likangguo/globalID/globalID2/src/services/situation_notices/export.py:159`（`latest_per_place` 的新增 `host_species` 分组键）。

触发：同一国家、疾病、行政区的两日通报分别从原句提取 `Wild birds` 与 `wild birds`。物种是来源原文，因此句首大写与句中小写是正常输入；清洗器保留来源原文。`pipeline.py:726` 聚类使用 `normalise(host_species)`，正确产生一个 outbreak；`latest_per_place` 却使用原字符串分组，产生两个 map point。前端 `model.ts` 的 `situationPlaceKey` 又做大小写规范化，因此地图列表最终显示一个地点，首页 JSON 的 `situation_home_summary` 却仍计为两个。

独立反例实际输出：

```text
clusters 1
map_points 2
[('new', 'wild birds', 1), ('old', 'Wild birds', 1)]
homepage_recent_places 2
```

影响：导出的最新地点不再满足“一个 outbreak × 地区 × 宿主”的唯一性；该地点的 `place_alert_count` 被拆成 1+1，首页新近地点与前端去重列表数量不一致，旧通报也保留为单独 marker。此反例证明代码路径可触发，未声称生产已出现该种原文组合。

建议：分组键使用与 `alert_id` / `cluster_alerts` / 前端一致的物种规范化；显示字段继续保留原文。增加两条通报仅物种大小写/空格不同的回归，断言一个地点、`place_alert_count=2`、最新更新时间及首页计数一致，同时保留不同物种、人类与动物的隔离。

复现脚本及日志位于 `docs/acceptance/evidence/20261116_review/rc4-host-place-repro.py`、`rc4-host-place-repro.log`；脚本只调用纯逻辑并输出结果：

```sh
PYTHONPATH=. LOG_DIR=docs/acceptance/evidence/20261116_review/logs \
  venv/bin/python docs/acceptance/evidence/20261116_review/rc4-host-place-repro.py
```

## 验证证据

以下回归由主 agent 执行，本 subagent 已核对日志并独立阅读相关实现与测试；没有重复计算为额外测试数：

```sh
# 仓库根目录
venv/bin/python -m pytest -q \
  tests/unit/test_situation_hosts.py \
  tests/unit/test_situation_feed_export.py \
  tests/unit/test_situation_release_validation.py \
  tests/unit/test_situation_json_publication.py \
  tests/unit/test_situation_pipeline_helpers.py \
  tests/unit/test_situation_pipeline_db.py \
  tests/unit/test_situation_export.py \
  tests/unit/test_situation_documents.py \
  tests/unit/test_notice_subscription_release.py \
  tests/unit/test_surveillance_signal_pilot.py

# astro-site 目录
npm run test:public

# cloudflare/surveillance-data 目录
npm test
npm run typecheck
npm run test:runtime
```

结果：Python **162 passed**；公共前端 **55 passed**；Worker 单元 **32 passed**；typecheck exit 0；本地 runtime **5 passed、1 skipped、0 failed**。runtime 命令中的 `wrangler deploy --dry-run` 仅本地打包，随后在 Miniflare 验证；**没有部署**。跳过项为既有旧 v2 路径用例，不能计作通过。原始日志位于 `docs/acceptance/evidence/20261116_review/rc4-{python,public,worker-tests,worker-types,worker-runtime}.log`。

Worker 评审读取了 `cloudflare`、`workers-best-practices` skills，获取当前官方 best-practices 文档与最新 `@cloudflare/workers-types@5.20261116.1`，核对 `R2ObjectBody` / `DecompressionStream` 合同；未据版本差异本身提出缺陷。代码直接使用 R2 binding、单次冻结指针、对象摘要和双向大小限制；没有引入部署或权限要求。

## 通过项与限制

- 旧记录仍默认 unknown；未通过疾病名称判定宿主。明确不同宿主/物种保持独立 ID 和聚类；人类监测图表对 animal/unknown 事件隐藏。阻止传播的来源不导出 host_evidence。
- RSS 使用同一冻结代次，实体筛选在上限前进行；非法日期、摘要不符、损坏/超限 gzip 返回失败，不把损坏输入渲染成正常空 feed。XML 转义与不可用字符处理、HEAD/条件读取均有本地测试。
- 信号试点只用此前同月基线，保留人群/来源/定义身份；缺报、未来月份、暂定值及不完整历史显式跳过；当前快照回放明确不代表真实历史预警表现。
- 宿主英文/中文词汇与混合句判断是保守约束，不能证明任意语言的语义角色和数字归属；此限制已写入 `SITUATION_ONE_HEALTH_PHASE1.md`，未误报为已完成通用宿主推理。
- 本轮未在线核验 NCBI/WHO/GISAID 链接内容、生产 Worker/网页发布、迁移实际状态或邮件送达。已有迁移/浏览器文档仅作背景，不能替代此次独立执行证据。
