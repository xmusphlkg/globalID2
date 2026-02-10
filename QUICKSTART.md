# 快速开始指南

## 当前进度

✅ 所有功能代码已完成
🔄 正在安装依赖...
⏳ 待运行：数据迁移

## 手动运行步骤

如果自动安装较慢，可以手动执行：

```bash
cd /home/likangguo/globalID/globalID2

# 1. 激活虚拟环境
source venv/bin/activate

# 2. 安装依赖
pip install pgvector sqlalchemy asyncpg pandas typer rich python-dotenv pyyaml openpyxl

# 3. 初始化数据库
python main.py init-database

# 4. 迁移历史数据（约2-5分钟）
python main.py migrate-data

# 5. 验证数据
python -c "from src.core import *; from src.domain import *; import asyncio; async def c(): await init_app(); db=get_database(); from sqlalchemy import select, func; cnt=await db.scalar(select(func.count(DiseaseRecord.id))); print(f'Total records: {cnt}'); asyncio.run(c())"
```

## 新增功能：数据文件导出

每次更新时，除了生成报告，还可以导出清洗整理好的数据文件。

### 导出命令

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
