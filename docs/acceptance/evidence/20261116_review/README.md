# 2026-11-16 分批验收证据

总表：[REVIEW_BATCHES_20261116.md](../../REVIEW_BATCHES_20261116.md)。此目录保留本轮实际命令输出、离线反例脚本及输入指纹，不包含原始业务数据库、部署产物或官网下载原档。

## 日志和快照

- `rc1-*`：多国来源首轮、工作区变化后的复验、额外反例和来源文件指纹。
- `rc2-*`：省级数据常规回归、日/月语义与附件反例、三批本地历史修复证据核对。
- `rc3-*`：地图/人口回归、本地分片对账和模型性能采样。
- `rc4-*`：态势、公共前端、Worker 类型/单元/本地 runtime，以及宿主物种分组反例。
- `rc5-*`：共享后端首轮及复验、后台全测试/类型/OpenAPI，以及调度和通知事务边界反例。
- `rc6-*`：跨模块回归、编译、Astro/图表检查、候选文件门禁以及主 agent 独立复现的日志。
- `workspace-before.json`：开始验收时 536 个变更/未跟踪文件的 SHA-256 和基线提交。
- `workspace-drift.json`：验收中发现的外部并行改动；不包含本轮后来新增的审查文档。
- `workspace-final.json`：最终记录的原始候选内容指纹和与开始时的差异，不冻结后续工作区。
- `evidence-manifest.json`：此目录每个证据文件的字节数和 SHA-256，清单本身除外。

各命令的完整参数及运行目录见对应 rc 分报告。不同批次/首轮/复验存在重复用例，不能把通过数简单相加为独立测试数量。空日志配合报告记录的 exit 0 表示静默检查通过；没有把 skipped 计为 passed。

## 重跑反例

从仓库根目录执行，使用当前已安装依赖及保留的测试/地图输入：

```bash
PYTHONPATH=. LOG_DIR=/tmp/globalid-review-logs \
  venv/bin/python docs/acceptance/evidence/20261116_review/rc1-adversarial.py
PYTHONPATH=. LOG_DIR=/tmp/globalid-review-logs \
  venv/bin/python docs/acceptance/evidence/20261116_review/rc2-adversarial.py
PYTHONPATH=. LOG_DIR=/tmp/globalid-review-logs \
  venv/bin/python docs/acceptance/evidence/20261116_review/rc4-host-place-repro.py
PYTHONPATH=. LOG_DIR=/tmp/globalid-review-logs \
  venv/bin/python docs/acceptance/evidence/20261116_review/rc5-adversarial.py
astro-site/node_modules/.bin/node --experimental-strip-types \
  docs/acceptance/evidence/20261116_review/rc3-model-performance.mjs
PYTHONPATH=. LOG_DIR=/tmp/globalid-review-logs \
  venv/bin/python docs/acceptance/evidence/20261116_review/rc6-config-audit.py
```

反例脚本会输出当前缺陷，部分断言特意确认缺陷存在；脚本退出 0 不表示业务验收通过。它们使用纯逻辑、现有本地输入或 mock，不执行生产写入、在线抓取或真实发送。部分脚本保留本机绝对路径；地图/分片采样依赖忽略目录中的现有生成输入，不是仅凭 Git checkout 即可重建的 hermetic 测试。重新运行前应核对输入指纹。

`rc6-config-audit.py` 读取同目录的初始候选清单，调用正式 ontology loader 和仓库对象大小规则；输出违规列表而不修改 Git index。修复/删除文件后输出自然会变化。

Worker 的 `test:runtime` 仅做 `wrangler deploy --dry-run` 本地打包及 Miniflare 本地运行；没有执行 Cloudflare 部署。评审额外读取当前官方 Workers 指引与类型 `5.20261116.1`，项目原安装类型版本为 `5.20261107.1`。

本目录对已有修复快照或生成分片的核验，不等于重新核验当前生产数据库、实时来源完整性或线上已发布代次。未执行实际邮件送达测试。
