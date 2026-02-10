# GlobalID 2.0

**智能全球疾病监测系统** - 下一代传染病监控与报告平台

## 🎯 核心特性

- ✅ **智能疾病识别** - 使用向量语义搜索 + LLM 自动识别新疾病
- ✅ **多国家支持** - 插件化架构，2小时接入新国家
- ✅ **AI 多专家协作** - Analyst → Writer → Reviewer → Fact Checker 工作流
- ✅ **智能缓存** - Redis 缓存降低 80%+ API 成本
- ✅ **时序数据库** - TimescaleDB 高效存储疾病数据
- ✅ **向量搜索** - Qdrant 语义相似度匹配

## 🚀 快速开始

### 前置要求

- Docker & Docker Compose
- Python 3.11+
- Poetry

### 安装步骤

```bash
# 1. 复制环境变量配置
cp .env.example .env
# 编辑 .env 填入你的 API keys

# 2. 启动 Docker 服务
docker-compose up -d

# 3. 安装 Python 依赖
poetry install

# 4. 初始化数据库
poetry run alembic upgrade head

# 5. 运行测试
poetry run pytest

# 6. 启动 CLI
poetry run globalid --help
```

## 📁 项目结构

```
globalID2/
├── src/
│   ├── core/              # 核心服务（配置、日志、数据库、缓存）
│   ├── domain/            # 领域模型（Disease, Country, Report）
│   ├── data/              # 数据层
│   │   ├── crawlers/      # 数据爬虫（CN, US, ...）
│   │   ├── processors/    # 数据处理
│   │   └── storage/       # 数据存储
│   ├── ai/                # AI 层
│   │   ├── agents/        # Agent（Analyst, Writer, Reviewer）
│   │   ├── validators/    # 验证器
│   │   └── prompts/       # Prompt 模板
│   ├── analysis/          # 数据分析
│   ├── generation/        # 报告生成
│   ├── services/          # 业务服务
│   ├── api/               # REST API
│   └── cli/               # 命令行接口
├── tasks/                 # Celery 异步任务
├── configs/               # 配置文件
├── tests/                 # 测试
├── docker/                # Docker 配置
├── scripts/               # 工具脚本
└── docs/                  # 文档
```

## 💻 开发命令

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down

# 运行测试
poetry run pytest

# 代码格式化
poetry run black src tests

# 类型检查
poetry run mypy src

# 代码检查
poetry run ruff check src
```

## 🔧 使用示例

```bash
# 生成月度报告
poetry run globalid generate --country CN --month 2026-02

# 添加新疾病
poetry run globalid disease add "COVID-19" --category respiratory

# 运行健康检查
poetry run globalid health-check

# 查看统计信息
poetry run globalid stats
```

## 🗄️ 数据库访问

访问 pgAdmin 管理数据库：

- URL: http://localhost:5050
- Email: admin@globalid.com
- Password: admin123

## 📊 性能对比

| 指标 | V1 | V2 | 改进 |
|------|----|----|------|
| API成本 | $1.31/次 | $0.20/次 | ↓85% |
| 生成速度 | 6.5分钟 | 2分钟 | ↑3倍 |
| 失败率 | 15% | 3% | ↓80% |
| 新疾病识别 | 手动 | 自动 | 100% |
| 新国家接入 | 2-3天 | 2小时 | ↑10倍 |

## 📖 文档

详细文档请查看 [docs/](docs/) 目录。

## 📝 License

MIT License
