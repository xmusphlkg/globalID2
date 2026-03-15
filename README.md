# GlobalID V2 - 全球传染病智能监测系统

基于 AI 的新一代传染病监测和报告生成系统。

## 🌟 主要特性

- **智能数据爬取**：自动从多个数据源获取传染病数据
- **AI 驱动分析**：使用 GPT-4/Claude 进行数据分析和洞察
- **自动报告生成**：生成专业的 Markdown/HTML/PDF 报告
- **质量审核**：AI 审核确保报告质量
- **性能优化**：85% 成本降低，3倍速度提升

## 📁 项目结构

```
globalID2/
├── dashboard/          # 新 Dashboard（Next.js + API 目录）
│   ├── src/            # Next.js 前端源码
│   └── api/            # FastAPI 代码（已迁移）
├── src/
│   ├── core/           # 核心功能（配置、数据库、缓存）
│   ├── api/            # 兼容路径（软链接到 dashboard/api）
│   ├── domain/         # 领域模型（疾病、国家、报告）
│   ├── data/           # 数据层（爬虫、解析器）
│   ├── ai/             # AI模块（分析师、作家、审核）
│   └── generation/     # 报告生成（图表、格式化、邮件）
├── tests/              # 测试
├── logs/               # 日志
├── reports/            # 生成的报告
├── .env                # 环境配置
├── main.py             # 主入口
└── requirements.txt    # 依赖
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd globalID2
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `.env` 文件：

```env
# OpenAI API
OPENAI_API_KEY=your_openai_key
OPENAI_BASE_URL=https://api.openai.com/v1

# Anthropic API (可选)
ANTHROPIC_API_KEY=your_anthropic_key

# 数据库
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/globalid

# Redis缓存 (可选)
REDIS_URL=redis://localhost:6379/0

# 邮件配置 (可选)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_password
```

### 3. 初始化数据库

有两种方式初始化数据库：

#### 方式一：快速初始化（仅创建表结构）

```bash
python main.py init-database
```

这会创建所有必要的表结构，并添加少量示例数据（中国配置和3个常见疾病）。

#### 方式二：完整重建（推荐，包含历史数据）

```bash
# 完整重建数据库（包括标准疾病库、映射关系和历史数据）
python scripts/full_rebuild_database.py
```

这会执行以下操作：
- 清空现有疾病相关数据
- 从 `configs/standard_diseases.csv` 导入标准疾病库
- 从 `configs/cn/disease_mapping.csv` 导入疾病映射关系
- 同步 diseases 表
- 从 `data/processed/history_merged.csv` 导入历史数据（包含完整字段：data_source, incidence_rate, mortality_rate, region, 详细metadata等）
- 验证数据完整性

**注意**：历史数据导入包含以下完整信息：
- 基础数据：病例数、死亡数
- 数据来源：真实的 data_source（来自 CSV 的 Source 列）
- 元数据：DOI、URL、source_file、adcode 等引用信息
- 扩展字段：incidence_rate、mortality_rate、region（如有）

#### 检查数据库是否正确初始化：

```bash
python scripts/cn_data_quality_check.py
```

#### 查看数据质量仪表盘：

```bash
cd dashboard
npm run dev
```

访问 http://localhost:3000 查看 Web 仪表盘。

#### 使用 Docker 一键启动完整 Dashboard 栈（推荐）

```bash
docker compose -f docker/dashboard-full-stack.yml up -d
```

如遇端口占用，可覆盖宿主机端口再启动：

```bash
API_PORT=18000 DASHBOARD_PORT=13000 POSTGRES_PORT=15432 REDIS_PORT=16379 \
QDRANT_HTTP_PORT=16333 QDRANT_GRPC_PORT=16334 \
docker compose -f docker/dashboard-full-stack.yml up -d
```

首次启动（或依赖升级）建议：

```bash
docker compose -f docker/dashboard-full-stack.yml pull
docker compose -f docker/dashboard-full-stack.yml up -d --force-recreate
```

启动后服务：
- Dashboard: http://localhost:3000
- API: http://localhost:8000/api/v1
- PostgreSQL: localhost:5432
- Redis: localhost:6379
- Qdrant: localhost:6333

若使用了端口覆盖，请将以上端口替换为你设置的值（例如 API_PORT=18000）。

说明：API 代码路径已切换为 `dashboard/api`，统一启动命令为
`uvicorn dashboard.api.main:app`。

常用 Docker 命令：

```bash
# 查看服务状态
docker compose -f docker/dashboard-full-stack.yml ps

# 查看日志（全部）
docker compose -f docker/dashboard-full-stack.yml logs -f

# 仅看 API 或 Dashboard 日志
docker compose -f docker/dashboard-full-stack.yml logs -f api
docker compose -f docker/dashboard-full-stack.yml logs -f dashboard

# 停止并清理本栈容器
docker compose -f docker/dashboard-full-stack.yml down
```

常见问题排查：

```bash
# 1) Docker 权限不足（permission denied /var/run/docker.sock）
sudo docker compose -f docker/dashboard-full-stack.yml up -d

# 2) 端口占用（3000/8000/5432/6379/6333）
sudo lsof -i :3000 -i :8000 -i :5432 -i :6379 -i :6333

# 3) Compose 配置自检
docker compose -f docker/dashboard-full-stack.yml config -q
```

如果你已经通过根目录 `docker-compose.yml` 启动了基础组件（PostgreSQL/Redis/Qdrant），
请先执行 `docker compose down`，再启动 `docker/dashboard-full-stack.yml`，避免端口冲突。

### 7. 运行测试

```bash
python main.py test
```

### 8. 爬取数据

```bash
# 智能爬取中国疾病数据（只爬取新数据）
python main.py crawl --country CN

# 指定数据源
python main.py crawl --country CN --source cdc_weekly  # 只爬取CDC Weekly
python main.py crawl --country CN --source nhc         # 只爬取国家疾控局
python main.py crawl --country CN --source pubmed      # 只爬取PubMed

# 强制爬取所有数据（忽略数据库检查）
python main.py crawl --country CN --force
```

**工作流程**：
1. **阶段1（轻量级）**: 获取数据列表，提取标题和年月信息
2. **阶段2（智能判断）**: 与数据库对比，识别哪些是新数据
3. **阶段3（重量级）**: 只爬取和处理新数据的详细内容

**优势**：
- ⚡ 避免重复爬取已有数据
- 💰 降低网络请求和存储成本
- 🎯 精准定位需要更新的数据

**原始内容存档**：
- 原始网页转为纯文本保存到 `data/raw/<country>/<run_id>/...`
- 数据库保存路径和哈希：`crawl_runs`, `crawl_raw_pages`

### 9. 生成报告

```bash
# 生成周报
python main.py generate-report --country CN --report-type weekly --days 7

# 生成并发送邮件
python main.py generate-report --country CN --report-type weekly --send-email
```

### 10. 完整流程

```bash
# 运行完整的爬取+生成流程
python main.py run --full
```

## 📦 数据库管理

### 完整重建数据库

推荐使用一体化脚本完成所有数据库初始化和数据导入：

```bash
python scripts/full_rebuild_database.py
```

执行步骤：
1. 清空现有数据（disease_records, diseases, disease_mappings, standard_diseases）
2. 导入标准疾病库（从 `configs/standard_diseases.csv`）
3. 导入疾病映射关系（从 `configs/cn/disease_mapping.csv`）
4. 同步 diseases 表（根据标准疾病库创建 diseases 记录）
5. 导入历史数据（从 `data/processed/history_merged.csv`，约 8,785 条记录）
   - 包含完整字段：cases, deaths, data_source, incidence_rate, mortality_rate, region
   - 包含详细metadata：DOI, URL, source_file, adcode 等
   - 使用 ON CONFLICT 处理重复数据
6. 验证数据完整性

**特点**：
- 一次运行，全部完成
- 批量插入优化性能
- 归一化匹配提高容错性
- 详细日志和进度显示

### 单独操作

如果需要单独执行某些操作：

```bash
# 仅刷新疾病映射关系
python scripts/refresh_disease_mappings.py --yes

# 数据质量检查
python scripts/data_quality_check_cn.py

# 生成数据库 schema
python scripts/generate_schema.py
```

**注意**：历史数据导入功能已整合到 `full_rebuild_database.py` 中，无需单独运行。

### 查看数据统计

```bash
# 使用 Web 仪表盘
cd dashboard && npm run dev

# 访问 http://localhost:3000
# - 主页：数据概览和趋势分析
# - 疾病对比：多疾病对比分析  
# - 数据质量：数据完整性检查
# - SQL 查询：自定义查询
```

## 📦 数据迁移（已废弃）

**注意**：以下命令已不再可用，请使用上述"数据库管理"部分的新方法。

<details>
<summary>旧的迁移方法（仅供参考）</summary>

从旧系统（ID_CN）迁移历史数据：

```bash
# 方法1：使用CLI命令
python main.py migrate-data

# 方法2：指定数据路径
python main.py migrate-data --data-path /path/to/old/data

# 方法3：直接运行迁移脚本
python scripts/migrate_data.py
```

迁移功能：
- ✅ 自动解析CSV格式
- ✅ 疾病名称映射和标准化
- ✅ 数据去重（跳过已存在记录）
- ✅ 批量导入（1000条/批次）
- ✅ 进度显示
- ✅ 统计报告

</details>

## 🔧 核心组件

### 1. Domain Models（领域模型）

- **Disease**: 疾病信息，支持 pgvector 语义搜索
- **Country**: 国家配置和数据源
- **DiseaseRecord**: 疾病时间序列数据（TimescaleDB）
- **Report**: 报告和章节

### 2. Data Crawlers（数据爬虫）

支持多个数据源：
- CDC Weekly (中国疾控中心周报)
- NHC (国家卫健委)
- PubMed RSS Feed

特性：
- 异步爬取
- 自动重试
- 速率限制
- 数据去重

### 3. AI Agents（AI 代理）

- **AnalystAgent**: 数据分析和趋势识别
  - 统计指标计算
  - 趋势分析
  - 异常检测
  - AI 洞察生成

- **WriterAgent**: 报告内容撰写
  - 多种写作风格（正式/通俗/技术）
  - 多语言支持
  - 结构化输出

- **ReviewerAgent**: 质量审核
  - 内容质量评分
  - 事实核查
  - 改进建议

### 4. Report Generation（报告生成）

- **ChartGenerator**: 使用 Plotly 生成图表
  - 时间序列图
  - 柱状图
  - 热力图
  - 地理地图

- **ReportFormatter**: 多格式输出
  - Markdown
  - HTML
  - PDF

- **EmailService**: 邮件发送
  - HTML 邮件
  - 附件支持
  - 批量发送

## 📊 数据流程

```
数据源 → 爬虫 → 解析 → 数据库
   ↓
数据库 → AI分析 → 内容撰写 → 质量审核
   ↓
报告生成 → Markdown/HTML/PDF → 邮件发送
```

## 🔍 示例：生成报告

```python
from src.generation import ReportGenerator
from src.domain import ReportType
from datetime import datetime, timedelta

generator = ReportGenerator()

report = await generator.generate(
    country_id=1,
    report_type=ReportType.WEEKLY,
    period_start=datetime.now() - timedelta(days=7),
    period_end=datetime.now(),
    title="COVID-19 周报",
    send_email=True,
)

print(f"Report generated: {report.html_path}")
```

## 🧪 测试

运行集成测试：

```bash
python main.py test
```

测试覆盖：
- ✅ 数据库连接
- ✅ 数据爬虫
- ✅ 领域模型
- ✅ AI Agents
- ✅ 报告生成
- ✅ 邮件服务

## 📈 性能指标

| 指标 | V1 | V2 | 提升 |
|------|----|----|------|
| 报告生成时间 | 15min | 5min | **3x** |
| API 成本 | $1.50 | $0.22 | **85%↓** |
| Token 使用 | 45K | 8K | **82%↓** |
| 并发处理 | 1 | 10+ | **10x** |

## 🛠️ 技术栈

- **语言**: Python 3.11+
- **框架**: FastAPI, SQLAlchemy 2.0
- **数据库**: PostgreSQL + TimescaleDB + pgvector
- **缓存**: Redis
- **AI**: OpenAI GPT-4, Anthropic Claude
- **可视化**: Plotly
- **CLI**: Typer, Rich

## 📝 配置说明

### AI 配置

```python
# src/core/config.py
ai:
  default_model: "gpt-4-turbo-preview"
  temperature: 0.7
  max_tokens: 2000
  enable_cache: true
  cache_ttl: 24  # 小时
  rate_limit: 60  # 每分钟请求数
```

### 数据库配置

```python
database:
  url: "postgresql+asyncpg://..."
  pool_size: 20
  max_overflow: 10
  echo: false
```

### 爬虫配置

```python
crawler:
  timeout: 30
  retries: 3
  delay: 1.0  # 请求间延迟（秒）
```

## 🐛 故障排除

### 数据库连接失败

```bash
# 检查 PostgreSQL 服务
sudo systemctl status postgresql

# 验证连接
psql -U user -d globalid -h localhost
```

### AI API 错误

```bash
# 检查 API Key
echo $OPENAI_API_KEY

# 测试 API 连接
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

### 报告生成失败

```bash
# 查看日志
tail -f logs/globalid.log

# 运行单元测试
python -m pytest tests/
```

## 📚 文档

- [架构设计](docs/architecture.md)
- [API 文档](docs/api.md)
- [数据模型](docs/models.md)
- [开发指南](docs/development.md)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 👥 作者

GlobalID Team

---

**注意**: 本系统处于活跃开发中，API 可能会有变化。生产环境使用前请充分测试。
