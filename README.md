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
├── src/
│   ├── core/           # 核心功能（配置、数据库、缓存）
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

```bash
python main.py init-database
```

### 4. 迁移疾病数据到数据库

```bash
# 将CSV配置的疾病数据导入数据库
python scripts/migrate_diseases_to_db.py

# 验证迁移结果
python main.py disease stats
```

💡 **为什么要用数据库？**
- 疾病种类会不断增加（新疾病、变种、本地名称）
- 支持动态添加，无需修改代码和重启服务
- 多实例部署共享数据
- 自动学习未知疾病
- 详见：[docs/DISEASE_MANAGEMENT_STRATEGY.md](docs/DISEASE_MANAGEMENT_STRATEGY.md)

### 5. 迁移历史数据（可选）

```bash
# 从旧系统导入历史数据
python main.py migrate-data --data-path /path/to/old/data

# 或直接运行迁移脚本
python scripts/migrate_data.py /path/to/old/data
```

### 6. 运行测试

```bash
python main.py test
```

### 7. 爬取数据

```bash
# 爬取中国疾病数据
python main.py crawl --country CN --max-results 100
```

### 8. 生成报告

```bash
# 生成周报
python main.py generate-report --country CN --report-type weekly --days 7

# 生成并发送邮件
python main.py generate-report --country CN --report-type weekly --send-email
```

### 7. 完整流程

```bash
# 运行完整的爬取+生成流程
python main.py run --full
```
## 📦 数据迁移

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
