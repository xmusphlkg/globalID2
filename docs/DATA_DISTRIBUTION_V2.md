# GlobalID 数据分发协议 v2

## 1. 目标

现有下载目录把同一批监测事实分别按国家和疾病保存，导致记录、来源信息和名称元数据重复。当前目录约 625 MiB，最大单文件已经接近 GitHub 普通 Git 的 100 MiB 上限。继续压缩单个 JSON 只能延后故障，不能解决仓库历史持续增长、可变分支 URL 缓存错配和发布不可回滚的问题。

v2 的目标是：

1. 一条监测事实只保存一次；国家和疾病下载共同引用它。
2. 任意数据增长都不会形成超大单文件。
3. 生成、校验、上传和生产切换相互分离。
4. 已发布版本不可变，可以校验、回滚和长期归档。
5. 下载链路只保留 v2，不再生成或回退到旧 `json_path`、`csv_path`。

## 2. 当前问题

- country 下载和 disease 下载的 `record_count` 汇总均为 199,545，同一批事实被物理存储两次。
- 当前只有约 611 个有效的“国家 × 疾病”组合，天然适合作为共享分区。
- 根 manifest 约 956 KiB，其中大量空间来自各条目重复的 `source_info`。
- JSON 每行重复国家名、疾病名、生成时间、来源 URL 等常量。
- 下载 URL 指向可变的 Git 分支；网站部署失败时，旧网站仍可能读到已经更新的新数据。
- 旧 manifest 没有协议版本、发布 ID、文件大小和 SHA-256，无法做端到端完整性校验。

## 3. 架构决定：GitHub 零固定成本方案

```text
PostgreSQL
    │
    ▼
本地纯导出 ──► 本地全量校验
    │              │
    │              └── 失败：保留上一份完整候选包
    ▼
GitHub 生成数据仓库的 snapshot-v2 分支
    ├── latest.json
    ├── 最近 3 个完整 release（处理并发读取与回滚）
    └── 按 SHA-256 命名的 gzip NDJSON 分片
              │
              ├──► raw.githubusercontent.com 公开下载
              └──► 每月 GitHub Release 压缩归档
```

- 继续使用现有 GitHub 生成数据仓库，不增加 R2、数据库托管或 Worker 固定费用。
- `snapshot-v2` 是机器生成分支，只保存最近 3 个已验证 release；历史归档使用 GitHub Releases。
- 新事实只写一次，country/disease index 共同引用同一分片。
- 不采用 Git LFS，避免存储和下载流量额度成为新的故障点。
- 网站与发布服务直接使用 `snapshot-v2`，旧 JSON/CSV 生成器和发布器已退出执行链路。
- 生成分支最终采用无父提交快照并以 `--force-with-lease` 更新，防止可达 Git 历史无限增长；长期审计版本由 Release 资产保存。

## 4. 规范化事实

v2 从 country 视图生成唯一事实流，并移除可以从目录或发布日期推导的重复字段。

保留的核心字段包括：

- `country_code`
- `disease_id`
- `date`
- `cases`、`weekly_equiv_cases`、`deaths`
- `incidence_rate_per_100k`、`incidence_rate_source`、`mortality_rate`
- `data_layer`、`projection_policy`、`series_codes`
- `loss_risk`、`coverage_status`
- `legacy_gap_fill_count`、`coverage_ratio_against_legacy`
- `primary_source_ref`、`source_refs`

下列字段不再逐行保存：

- `dataset_kind`、`dataset_id`、`dataset_slug`、`dataset_name`
- 国家和疾病的展示名称、疾病分类
- `year_month`、`coverage_start`、`coverage_end`
- `generated_at`
- 展开的来源名称、URL 和类型

展示名称进入 country/disease index，来源详情进入 source catalog，生成时间进入 release metadata。这样单纯更新发布时间不会重写所有事实分片。

## 5. 本地包格式

```text
site-downloads-v2/
├── .sharded-data-package-v2
├── manifest.json
├── indexes/
│   ├── countries/jp.json
│   └── diseases/D162.json
└── objects/
    └── sha256/
        └── ab/
            └── ab…64位哈希….ndjson.gz
```

分片规则：

1. 默认按 `(country_code, disease_id)` 生成一个共享分片。
2. 只有该组合超过 `max_uncompressed_bytes` 时，才继续按年份和 part 拆分，避免小文件及 raw 请求数爆炸。
3. NDJSON 使用稳定 key 顺序、UTF-8 和确定性 gzip（`mtime=0`）。
4. 压缩对象按 SHA-256 寻址；逻辑 shard 在 manifest 中保存国家、疾病、可选溢出年份、part、记录数、日期范围和压缩前后字节数。
5. country index 和 disease index 只保存相同 shard 的路径引用，不复制事实。

默认分片上限采用 8 MiB 未压缩数据。压缩对象通常远小于此值，也远低于 GitHub 50 MiB 警告和 100 MiB 阻止阈值。对象按哈希前两位分目录，避免单个目录堆积过多文件。

## 6. Manifest v2 最小契约

```json
{
  "manifest_version": 2,
  "package_mode": "canonical_facts",
  "release": {
    "release_id": "20260801T000000Z-<内容哈希前12位>",
    "generated_at": "2026-08-01T00:00:00Z",
    "content_sha256": "<内容根哈希>"
  },
  "dataset": {
    "id": "globalid-surveillance-facts",
    "date_field": "date",
    "country_field": "country_code",
    "disease_field": "disease_id"
  },
  "format": {
    "media_type": "application/x-ndjson",
    "compression": "gzip",
    "partitioning": ["country_code", "disease_id", "overflow_year", "part"],
    "max_uncompressed_bytes": 8388608
  },
  "indexes": {
    "countries": [],
    "diseases": []
  },
  "shards": [],
  "totals": {
    "record_count": 199545,
    "shard_count": 0,
    "compressed_bytes": 0,
    "uncompressed_bytes": 0
  }
}
```

Manifest 与 index 中的所有路径都必须是包根目录下的 POSIX 相对路径；禁止绝对路径、`..`、反斜杠和符号链接。

## 7. GitHub snapshot 分支布局

远端生成分支只保存一个很小的可变指针和最近 3 个不可变 release：

```text
latest.json
releases/<release_id>/manifest.json
releases/<release_id>/indexes/countries/...
releases/<release_id>/indexes/diseases/...
releases/<release_id>/objects/sha256/ab/<hash>.ndjson.gz
releases/<previous_release_id>/...
```

- `latest.json` 只包含 `release_id`、`manifest_path`、生成时间和 manifest SHA-256。
- release 目录一旦生成不得原地修改；同一 release 的 manifest、index 和 object 必须在同一个 Git tree 中切换。
- 保留最近 3 个 release，避免客户端读取旧 `latest.json` 后恰逢分支切换而找不到旧分片。
- 网站构建应把具体 `release_id` 固定进页面数据；外部“最新版”客户端才读取 `latest.json`。
- raw 下载完成后仍按 manifest 中的 SHA-256 校验，不把 HTTP 缓存结果当作完整性证明。
- Git 仓库中的相同内容天然按 blob 哈希去重；内容寻址对象也让三个 release 之间的重复数据不会重复占用 Git object storage。

### 为什么不能只做普通提交

即使工作区从 625 MiB 降到几 MiB，普通提交历史仍会永久保留每一版被替换的 gzip blob。生成分支因此采用“有限快照历史”：

1. 发布器读取远端分支当前 SHA。
2. 本地组装含最近 3 个 release 的完整 tree。
3. 创建无父提交的 snapshot commit。
4. 使用 `git push --force-with-lease=<branch>:<expected_sha>` 更新分支。
5. lease 不匹配则退出并重新生成，绝不覆盖并发发布。

这只适用于专用的机器生成分支，不允许用于代码分支。GitHub 可能暂时保留不可达对象，因此仍设置仓库健康阈值；超过阈值时做一次 epoch rollover，而不是等到硬限制才处理。

## 8. 本地与 GitHub 发布状态机

1. 生成本地 staging 包。
2. 完整读取所有 shard，校验 schema、路径、hash、大小、顺序、日期范围和汇总计数。
3. 同文件系统原子替换本地候选目录。
4. 组装 snapshot tree，并复制最近 2 个旧 release。
5. 再次验证 snapshot 中的全部 3 个 release 和 GitHub 文件大小门槛。
6. 创建无父 snapshot commit，但尚不更新远端。
7. 用 `--force-with-lease` 一次性切换生成分支。
8. 从 GitHub raw 回读 `latest.json`、manifest 和抽样 shard，核对 release ID、大小及 SHA-256。
9. Pages 构建固定到该 release；失败时生成分支仍可回退到上一 snapshot commit。
10. 每月把完整 package 压成一个 Release asset，并在小型 catalog 分支记录归档信息。

当前已实现步骤 1–7 的代码：发布器默认 dry-run，只有显式 `--push` 才会访问远端。本轮只做本地校验，不执行 push、Pages 部署或 Release 上传。

## 9. v2-only 切换结果

- `generate_site_data.py` 直接构造规范事实，不再先生成 v1 展示行，也没有 `legacy`、`dual` 或回退模式。
- 前端下载清单只含 `dataset_index_path`，不含 `json_path`、`csv_path` 或兼容回退。
- `DownloadPanel` 和图表下载链接只消费分片 index。
- 旧 `publish_download_repo.py`、旧发布参数和旧 JSON/CSV 单元测试已移除。
- 发布服务在启用 Git 推送时只调用 `publish_github_snapshot_v2.py`。
- 本地旧 `exports/site-downloads` 已在完成一次 198,229 条记录等价校验后删除。

`migrate_legacy_downloads_v2.py` 只作为一次性历史转换工具保留，不在正常生成或发布链路中调用。

## 10. 本地验收门槛

- 唯一事实总数必须等于生成时 country 视图与 disease 视图的汇总。
- country index 汇总与 disease index 汇总都等于唯一事实总数。
- 每条事实恰好属于一个物理 shard。
- 所有 shard 都低于配置的未压缩字节上限。
- 所有 SHA-256、压缩前后大小、记录数和日期范围匹配。
- 改变输入顺序不会改变 manifest 或 gzip 字节。
- 任意 shard 被篡改后，离线 validator 必须失败。
- staging 生成失败时，上一份完整本地包保持不变。
- snapshot tree 中任何单文件不得超过 16 MiB（事实分片仍按 8 MiB 未压缩上限切分），并应远低于 GitHub 的警告线。
- snapshot tree 总大小设置 250 MiB 软门槛，Git object pack 设置 750 MiB 告警门槛。
- 单个目录通过 SHA-256 前缀分桶，避免超过 3,000 个直接子项。
- 整个本地验证过程不执行 Git push、服务重启、Pages 部署或外部写入。

当前可直接运行：

```bash
# 正常生成：数据库 -> v2 package -> GitHub snapshot tree -> 小型前端清单
PYTHONPATH=. venv/bin/python scripts/generate_site_data.py

# 逐个解压并校验所有对象和两套索引
PYTHONPATH=. venv/bin/python scripts/verify_sharded_downloads.py \
  exports/site-downloads-v2 --expected-records 198229

# 校验待发布 snapshot；默认 dry-run，不接触网络
PYTHONPATH=. venv/bin/python scripts/publish_github_snapshot_v2.py

# 真正发布必须显式授权；本轮未执行
PYTHONPATH=. venv/bin/python scripts/publish_github_snapshot_v2.py \
  --repo-url git@github.com:xmusphlkg/globalID2_data_download.git --push
```

发布器固定目标为 `snapshot-v2`，完整验证先于任何 Git 命令，且每次生成孤儿提交并使用精确 `--force-with-lease`。省略 `--push` 时不会创建 Git 仓库或访问网络。

## 11. 保留策略

Git 分支和长期归档分开管理：

- `snapshot-v2` 分支只保留最近 3 个完整 release。
- 每月保留 1 个 GitHub Release 压缩归档；重大 schema/映射迁移前额外归档一次。
- Release 使用单个 `globalid-data-<release_id>.tar.gz` 或 `.zip` 加 `manifest.json`、`SHA256SUMS`，资产数量保持很小。
- 旧 v1 大文件不再生成、保留或发布。
- 当远端仓库 Git object pack 达到 750 MiB 时启动 epoch rollover：冻结旧生成分支或仓库，新建一个轻量 snapshot 分支/数据仓库，并更新站点入口。
- 不在自动任务中运行 `git filter-repo` 或删除远端历史；历史压缩属于一次性人工审核操作，先做 Release 归档再执行。

该方案没有新增固定云费用。主要代价是 GitHub 的合理使用边界和生成分支允许安全的 lease 强制更新，因此必须保留文件、tree 和 pack 三层健康检查。

## 12. 2026-08-04 本地实测

使用当前 v1 下载快照直接迁移，不连接数据库和网络：

| 指标 | v1 | GitHub-ready v2 |
|---|---:|---:|
| 规范事实 | country/disease 各保存 198,229 行 | 唯一保存 198,229 行 |
| 工作区大小 | 649,831,484 bytes | 2,930,635 bytes |
| 共享事实 shard | 不适用 | 612 |
| GitHub tree 文件数 | 471 | 852（含 235 个 index、manifest/说明文件） |
| 最大文件 | 约 92 MB | 283,038 bytes |
| 本地 `git gc` 后 pack | 未重新测量旧完整历史 | 2.47 MiB |

工作区下降约 99.54%（约 218 倍）。country index 和 disease index 的记录汇总都严格等于 198,229；225 个疾病 index 全部保留，其中 27 个当前无事实的疾病以空 index 明确表示，而不是静默消失。

## 13. 官方边界参考

- [GitHub 普通 Git 大文件限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
- [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [Git LFS 说明与限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)
