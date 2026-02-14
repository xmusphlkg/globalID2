# GlobalID 2.0 实施计划

## 🎯 重构策略：渐进式迁移

**关键原则**: 不是一次性推翻，而是**逐步替换**

```
旧系统 (ID_CN)
    ↓ 继续运行
    ↓ 逐步迁移功能
    ↓
新系统 (globalid-v2)
    ↑ 并行开发
    ↑ 验证稳定后切换
    ↑
最终完全迁移
```

---

## 📅 详细实施计划

### Week 1: 基础设施搭建

#### Day 1-2: 环境搭建

**任务清单**:
- [ ] 创建新项目目录 `globalid-v2`
- [ ] 设置 Docker 环境（PostgreSQL + Redis + TimescaleDB）
- [ ] 配置开发环境（Poetry + pre-commit hooks）
- [ ] 初始化 Git 仓库
- [ ] 设置 CI/CD pipeline (GitHub Actions)

**交付物**:
- ✅ `docker-compose.yml` 可以一键启动全部服务
- ✅ `pyproject.toml` 依赖管理配置
- ✅ 基础项目结构
- ✅ README 和开发文档

**验收标准**:
```bash
docker-compose up -d
# 能够访问：
# - PostgreSQL: localhost:5432
# - Redis: localhost:6379
# - pgAdmin: localhost:5050

poetry install
poetry run python -c "import src; print('OK')"
```

#### Day 3-4: 核心服务实现

**任务清单**:
- [ ] 实现配置管理 (`src/core/config.py`)
- [ ] 实现日志系统 (`src/core/logging.py`)
- [ ] 实现缓存服务 (`src/core/cache.py`)
- [ ] 实现数据库连接 (`src/core/database.py`)
- [ ] 编写单元测试

**交付物**:
- ✅ 可用的核心服务
- ✅ 100%测试覆盖率
- ✅ 完整的类型提示

**验收标准**:
```python
from src.core.config import get_config
from src.core.database import get_db
from src.core.cache import get_cache

config = get_config()  # 成功加载配置
db = get_db()         # 成功连接数据库
cache = get_cache()   # 成功连接Redis
```

#### Day 5: 数据模型定义

**任务清单**:
- [ ] 定义Pydantic模型 (`src/domain/models.py`)
- [ ] 创建数据库表 (执行SQL schema)
- [ ] 实现ORM映射 (SQLAlchemy models)
- [ ] 数据验证测试

**交付物**:
- ✅ 完整的数据模型
- ✅ 数据库schema创建成功
- ✅ 模型验证通过

---

### Week 2: 数据层实现

#### Day 1-2: 爬虫框架

**任务清单**:
- [ ] 实现 `BaseCrawler` 抽象类
- [ ] 创建爬虫调度器
- [ ] 实现错误处理和重试逻辑
- [ ] 迁移现有CN爬虫

**迁移步骤**:
```python
# 1. 将现有dataget.py中的函数封装为类
# 旧代码:
def get_cdc_results(url, label, origin):
    ...
    
# 新代码:
class CNCDCCrawler(BaseCrawler):
    async def fetch(self):
        ...  # 复用旧逻辑
```

**交付物**:
- ✅ CN数据爬取正常工作
- ✅ 新旧数据对比一致性100%

#### Day 3-4: 智能疾病注册表

**任务清单**:
- [ ] 实现疾病精确匹配
- [ ] 实现模糊匹配（编辑距离）
- [ ] 集成向量搜索（语义匹配）
- [ ] 实现LLM辅助识别
- [ ] 自动注册新疾病

**测试场景**:
```python
# 测试1: 精确匹配
assert registry.match("COVID-19") == Disease("COVID-19")

# 测试2: 模糊匹配
assert registry.match("新冠肺炎") == Disease("COVID-19")

# 测试3: 英文变体
assert registry.match("Coronavirus Disease 2019") == Disease("COVID-19")

# 测试4: 新疾病
result = registry.match("未知疾病X")
assert result == None  # 自动加入审查队列
```

**交付物**:
- ✅ 90%+ 匹配准确率
- ✅ 新疾病自动识别
- ✅ 人工审查队列

#### Day 5: 数据标准化

**任务清单**:
- [ ] 实现数据清洗
- [ ] 实现地理位置标准化
- [ ] 实现语言检测和翻译
- [ ] 集成测试

---

### Week 3: AI层重构

#### Day 1-2: 验证系统

**任务清单**:
- [ ] 实现规则验证器（Rule-based）
- [ ] 实现统计验证器
- [ ] 实现集成验证器（Ensemble）
- [ ] 性能测试

**对比测试**:
```python
# 旧方案
old_time = measure_time(old_validate_with_ai)  # 2秒
old_cost = 0.01  # $0.01

# 新方案
new_time = measure_time(new_ensemble_validate)  # 0.1秒
new_cost = 0  # $0（规则验证）

assert new_time < old_time * 0.1  # 快10倍
assert new_cost < old_cost * 0.1  # 便宜10倍
```

**交付物**:
- ✅ 验证速度提升90%
- ✅ 验证成本降低90%
- ✅ 准确率提升至95%+

#### Day 3-5: AI Agent系统

**任务清单**:
- [ ] 实现 `BaseAgent` 基类
- [ ] 实现 `AnalystAgent`（数据分析）
- [ ] 实现 `WriterAgent`（报告撰写）
- [ ] 实现 `ReviewerAgent`（质量审查）
- [ ] 实现 `FactCheckerAgent`（事实核查）
- [ ] 实现Agent协作流程

**里程碑测试**:
```python
# 生成一个section
section, metadata = await generator.generate_section(
    section_type="introduction",
    data=test_data,
    disease="COVID-19"
)

assert len(section) > 100
assert metadata['iterations'] <= 3  # 最多3次迭代
assert metadata['final_score'] >= 0.8  # 质量分数
assert metadata['fact_check'] == True  # 事实验证通过
```

**验收对比**:
| 指标 | 旧系统 | 新系统 | 改进 |
|------|--------|--------|------|
| 生成时间 | 30s | 15s | ↓50% |
| API成本 | $0.05 | $0.02 | ↓60% |
| 质量分数 | 0.75 | 0.85 | ↑13% |
| 失败率 | 15% | 3% | ↓80% |

---

### Week 4: 生成和发布

#### Day 1-2: 报告生成器

**任务清单**:
- [ ] 重构报告生成流程
- [ ] 实现模板系统
- [ ] 集成可视化（Plotly）
- [ ] 实现并行生成（加速）

**性能优化**:
```python
# 旧方案：串行生成
for disease in diseases:
    section = generate(disease)  # 15秒/个
# 总时间: 26 × 15秒 = 6.5分钟

# 新方案：并行生成
sections = await asyncio.gather(*[
    generate(disease) for disease in diseases
])  # 并行执行
# 总时间: ~2分钟（3倍加速）
```

#### Day 3-4: 网站构建

**任务清单**:
- [ ] 评估静态站点生成器（Astro vs Hugo）
- [ ] 实现网站构建器
- [ ] 实现自动部署
- [ ] SEO优化

**交付物**:
- ✅ 网站构建时间 < 30秒
- ✅ Lighthouse分数 > 95
- ✅ 自动部署到GitHub Pages

#### Day 5: 邮件服务

**任务清单**:
- [ ] 重构邮件服务
- [ ] 实现订阅者管理
- [ ] 邮件模板优化
- [ ] A/B测试准备

---

### Week 5-6: 集成测试和上线

#### Week 5: 端到端测试

**任务清单**:
- [ ] 编写集成测试
- [ ] 性能压力测试
- [ ] 数据迁移测试
- [ ] 并行运行新旧系统对比

**对比验证**:
```bash
# 运行旧系统
cd ID_CN/Script/CN
python main.py  # 生成2月报告

# 运行新系统
cd globalid-v2
poetry run globalid generate --month 2025-02

# 对比输出
diff old_output/ new_output/
# 核心数据一致性检查
# 质量对比
# 成本对比
```

#### Week 6: 部署和切换

**任务清单**:
- [ ] 生产环境部署
- [ ] 监控告警配置
- [ ] 文档完善
- [ ] 团队培训
- [ ] 灰度切换

**切换策略**:
```
Week 6.1-6.3: 
- 旧系统继续运行
- 新系统shadow模式（生成但不发布）
- 对比验证

Week 6.4-6.5:
- 新系统发布10%流量
- 监控错误率
- 收集反馈

Week 6.6-6.7:
- 逐步增加到100%
- 旧系统作为备份
- 一周后下线旧系统
```

---

## 🚀 快速启动指南

### 前置要求

```bash
# 安装依赖
- Docker & Docker Compose
- Python 3.11+
- Poetry
- Git
```

### 第一步：创建项目

```bash
# 1. 在ID_CN同级目录创建新项目
cd /home/likangguo/globalID
mkdir globalid-v2
cd globalid-v2

# 2. 初始化项目
git init
poetry init  # 按提示操作

# 3. 复制启动文件
# (我会为你创建这些文件)
```

### 第二步：启动开发环境

```bash
# 启动所有服务
docker-compose up -d

# 检查服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 初始化数据库
poetry run alembic upgrade head

# 运行测试
poetry run pytest
```

### 第三步：数据迁移

```bash
# 迁移现有数据到新系统
poetry run python scripts/migrate_from_old.py

# 验证数据一致性
poetry run python scripts/verify_migration.py
```

---

## 📊 成功指标

### 技术指标

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| API成本下降 | >80% | 对比每月账单 |
| 生成速度 | >2倍 | 计时对比 |
| 代码覆盖率 | >80% | pytest-cov |
| 失败率 | <5% | 错误日志统计 |
| 新疾病识别 | 100%自动 | 无需手动代码 |

### 业务指标

| 指标 | 目标 | 测量方式 |
|------|------|----------|
| 报告质量 | 提升20% | 人工评分 |
| 新国家接入时间 | <2小时 | 实际操作 |
| 维护时间 | 减少80% | 工时统计 |
| 用户满意度 | >4.5/5 | 问卷调查 |

---

## 🎯 MVP范围（最小可行产品）

### Phase 1 MVP（2周）

**包含功能**:
- ✅ CN数据爬取（迁移现有）
- ✅ 疾病注册表（基础版）
- ✅ 规则验证系统
- ✅ 报告生成（单个疾病）
- ✅ PostgreSQL存储

**不包含**:
- ❌ 多国家支持
- ❌ AI Agent协作
- ❌ 网站自动发布
- ❌ 向量搜索

**验收标准**:
- 能够生成一个月的CN报告
- 质量不低于现有系统
- 成本降低50%+

### Phase 2（Week 3-4）

**新增功能**:
- ✅ AI Agent系统
- ✅ 多专家验证
- ✅ 智能降级

### Full Release（Week 5-6）

**新增功能**:
- ✅ 多国家支持框架
- ✅ 向量语义搜索
- ✅ 完整监控系统
- ✅ 自动化部署

---

## 💰 成本效益分析

### 开发投入

**人力成本**:
- 6周 × 40小时 = 240小时
- 假设时薪 $50
- **总成本: $12,000**

**基础设施**:
- PostgreSQL: $0（自托管）
- Redis: $0（自托管）
- OpenAI API: ~$100/月
- **月度成本: $100**

### 收益

**直接节省**（每月）:
- API成本: $26 → $4 = **$22/月**
- 服务器: 可扩展性提升，成本不变
- **年度节省: $264**

**间接收益**（每月）:
- 维护时间: 10小时 → 2小时 = **8小时/周**
- 按$50/小时 = $400/周 = **$1,600/月**
- **年度节省: $19,200**

**投资回报**:
- ROI = (年度节省 / 初始投入) = ($19,464 / $12,000) = **162%**
- **回本周期: <1个月**

---

## 🎊 准备开始了吗？

我建议采用**渐进式迁移**策略：

### 选项 A: 保守路线（推荐）
1. **Week 1**: 搭建基础设施，不影响现有系统
2. **Week 2**: 并行运行新旧系统，对比验证
3. **Week 3**: 灰度切换，逐步迁移
4. **Week 4+**: 完全迁移，优化性能

### 选项 B: 激进路线
1. **Week 1-2**: 快速搭建MVP
2. **Week 3**: 直接切换
3. **Week 4+**: 修复bug和优化

### 选项 C: 混合方式
1. 先快速修复现有系统（QUICK_FIX.md）
2. 并行开发新系统
3. 功能ready后逐个迁移

---

## 下一步

我可以立即为你：

1. **创建项目脚手架** - 生成完整的文件结构、Docker配置、基础代码
2. **实现核心服务** - config, logging, database等基础设施
3. **迁移第一个模块** - 比如先迁移爬虫部分
4. **设置CI/CD** - GitHub Actions自动测试和部署

**你想从哪里开始？** 🚀

我建议：**先创建项目脚手架**，这样你可以看到完整的架构，然后我们逐步实现功能。

准备好了吗？
