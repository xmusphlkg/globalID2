# 快速开始指南

## 系统要求

- Python 3.11+
- PostgreSQL 14+
- Redis (可选，用于缓存)

## 快速部署（5分钟）

### 1. 克隆并安装依赖

```bash
cd /home/likangguo/globalID/globalID2
source venv/bin/activate  # 如果已有虚拟环境

# 或创建新的虚拟环境
python -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置数据库

确保 `.env` 文件包含正确的数据库连接：

```env
DATABASE_URL=postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid
```

### 3. 初始化数据库（二选一）

#### 方式 A：完整重建（推荐）

一次性完成所有初始化和数据导入（包含 8,785 条历史记录）：

```bash
python scripts/full_rebuild_database.py
```

预计耗时：1-2 分钟

#### 方式 B：快速初始化

仅创建表结构和基础配置：

```bash
python main.py init-database
```

### 4. 启动数据质量仪表盘

```bash
streamlit run src/dashboard/app.py
```

访问：http://localhost:8501

## 常用操作

### 数据管理

```bash
# 完整重建数据库（推荐，包含所有步骤）
python scripts/full_rebuild_database.py

# 刷新疾病映射（修改 CSV 配置后）
python scripts/refresh_disease_mappings.py --yes

# 数据质量检查
python scripts/data_quality_check_cn.py
```

**注意**：`full_rebuild_database.py` 已整合历史数据导入功能，包含完整字段和详细metadata。

### 数据查看

```bash
# 启动仪表盘
streamlit run src/dashboard/app.py

# 功能：
# - 数据概览和 KPI 指标
# - 疾病趋势分析
# - 疾病对比
# - 数据质量检查
# - 自定义 SQL 查询
```

### 数据导出

```bash
# 导出最新数据（CSV + Excel）
python main.py export-data --country CN --period latest

# 导出全部数据
python main.py export-data --country CN --period all --output-format all

# 导出指定月份
python main.py export-data --country CN --period 2025-06

# 创建数据包（ZIP）
python main.py export-data --country CN --package
```

## 当前数据统计

完成完整重建后，系统包含：

- **总记录数**：8,785 条疾病记录
- **时间范围**：2010-01-01 至 2025-12-01
- **疾病数量**：49 种（排除汇总项）
- **数据来源**：
  - China CDC: Notifiable Infectious Diseases Reports (5,408 条)
  - China CDC Weekly: Notifiable Infectious Diseases Reports (2,059 条)
  - GOV Data (1,318 条)

## 故障排除
  - GOV Data (1,318 条)

## 故障排除

### 支持的格式

- **CSV**: 通用格式，Excel/Python/R都能读取
- **Excel** (.xlsx): 带格式化的Excel文件
- **JSON**: API友好格式
- **Parquet**: 高效压缩格式（大数据）

### 自动导出

生成报告时自动导出数据：

```bash
python main.py generate-report --country CN --report-type weekly
# 会自动生成：
# - reports/CN_data_20260210_*.csv
# - reports/CN_latest.csv
# - reports/CN_latest.xlsx
```

### 数据包内容

使用 `--package` 创建的 ZIP 包含：

- 所有历史数据（CSV + Excel + JSON）
- 最新数据（CSV + Excel）
- README.txt（数据字典说明）

## 数据字段说明

导出的数据包含以下字段：

| 字段 | 说明 |
|------|------|
| Date | 记录日期 (YYYY-MM-DD) |
| YearMonth | 年月 (YYYY Month) |
| Disease | 疾病名称 |
| DiseaseCategory | 疾病分类 |
| Cases | 病例数 |
| Deaths | 死亡数 |
| Recoveries | 康复数 |
| IncidenceRate | 发病率 |
| MortalityRate | 死亡率 |
| FatalityRate | 病死率 (%) |
| Country | 国家 |
| DataQuality | 数据质量 (high/medium/low) |
| ConfidenceScore | 可信度评分 (0-1) |
| Source | 数据来源 |
| SourceURL | 来源URL |

## 下一步

依赖安装完成后：

1. 运行数据迁移（约2-5分钟）
2. 生成第一份报告
3. 导出数据文件
4. 设置定时任务

## 问题排查

### 如果数据库连接失败

检查 .env 文件中的 DATABASE_URL 配置

### 如果依赖安装很慢

可以使用国内镜像：
```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pgvector
```

### 如果内存不足

编辑 `scripts/migrate_data.py`，将批量大小从 1000 改为 500
