# 快速开始

本文用于本地开发。生产服务的部署与升级见
[部署和回滚](docs/control-center/deployment-and-rollback.md)，已有数据库的迁移见
[迁移说明](migrations/README.md)。下列命令默认在仓库根目录执行。

## 1. 安装依赖

需要 Python 3.11+、Node.js 22.23.4+、Docker Compose。
服务进程使用的系统 Node 也必须满足版本要求。

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
npm --prefix dashboard ci
npm --prefix astro-site ci
```

首次配置时从 `.env.example` 复制 `.env`，填写数据库和所需服务参数；已有 `.env`
应按示例补齐配置。前后端的鉴权和代理设置见
[配置指南](docs/operations/DEVELOPMENT.md#configuration)。

## 2. 启动基础设施并初始化开发数据库

```bash
docker compose up -d
venv/bin/python main.py init-database
```

`init-database` 创建缺失的模型表并初始化 CN 元数据，不导入历史观测。
它不能替代已有数据库的 Alembic 升级。采用迁移管理前，按
[迁移说明](migrations/README.md)运行 preflight；已有 Alembic 版本的数据库按正常迁移链升级，
不要重复 stamp 基线。

数据库、Redis 和 Qdrant 使用固定 Docker volume。`docker compose down` 保留数据；
`docker compose down -v` 会删除持久化数据。

## 3. 启动 API、worker 和调度器

分别在终端中运行：

```bash
venv/bin/uvicorn dashboard.api.main:app --host 127.0.0.1 --port 8000
```

```bash
TASK_WORKER_ROLE=general venv/bin/python -m src.services.task_worker
```

```bash
TASK_WORKER_ROLE=ai venv/bin/python -m src.services.task_worker
```

```bash
venv/bin/python -m src.control_plane.scheduler
```

普通任务和 AI 任务使用独立 worker。`crawl` 等 CLI 会入队并等待任务结果，需要对应
worker 正常运行。调度器读取数据库中的任务配置；在生产数据库上启动它会执行已启用的计划。

## 4. 启动控制台

```bash
npm --prefix dashboard run dev
```

访问 <http://localhost:3000>。浏览器通过同源 `/api/v1` 访问 API；后端地址不同
时配置服务端 `API_PROXY_TARGET`，并确保 Web 与 API 的 `DASHBOARD_API_KEY` 一致。
健康检查入口为 <http://localhost:8000/health>。

## 5. 采集、检查与本地导出

```bash
# 采集并处理 CN 官方来源；需要运行中的 general worker。
venv/bin/python main.py crawl --country CN --source all --process --save-raw

# 查看任务运行状态。
venv/bin/python scripts/check_task_runtime.py

# 检查 CN 旧兼容投影的数据质量，不代表所有 v3 来源验收通过。
venv/bin/python scripts/cn_data_quality_check.py

# 导出最新数据到本地；不发布外部站点。
venv/bin/python main.py export-data --country CN --period latest --output-format all
```

国家来源与接入条件见[来源目录](docs/data-sources/README.md)。历史导入和修复见
[脚本索引](scripts/README.md)。`full_rebuild_database.py` 的 full/history 模式会重建数据，
需要已有原始文件；正常启动不需要执行它。仅同步旧兼容映射时可按
[重建指南](docs/operations/DATABASE_REBUILD.md)使用交互式 `--mode mappings`。

## 6. 预览公开站点

在独立开发工作区生成数据后启动 Astro：

```bash
venv/bin/python scripts/generate_site_data.py
npm --prefix astro-site run dev
```

访问 <http://localhost:4321>。数据导出修改本地生成目录；不要在受管发布任务使用的
工作区并行手工运行。正式公开更新由发布任务处理，流程见
[发布架构](docs/architecture/STATIC_SITE_AND_DYNAMIC_DATA.md)。

## 检查与故障排查

```bash
./scripts/check_repository_boundaries.sh
venv/bin/python scripts/check_legacy_disease_access.py
```

- 数据库连接失败：核对 `.env` 与 `docker compose ps`；不要通过重建数据库修复连接问题。
- CLI 一直等待：检查 API、对应 worker 和任务状态。
- 控制台访问 API 失败：核对代理目标、服务端 API key 与 API 健康状态。
- 数据表已有但字段缺失：按迁移流程检查 Alembic 版本，不重复运行旧建表脚本。
- 系统服务安装、日志和重启：见[开发与运维](docs/operations/DEVELOPMENT.md#boot-autostart)。

更多入口：[文档索引](docs/README.md)、[控制台排障](docs/control-center/troubleshooting.md)。
