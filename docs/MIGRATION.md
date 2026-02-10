# 数据迁移指南

## 概述

历史数据还在旧系统 `ID_CN` 中，需要迁移到新的 GlobalID V2 系统。

## 数据源

旧系统数据位置：
```
/home/likangguo/globalID/ID_CN/Data/AllData/CN/
├── 2024 May.csv
├── 2024 June.csv
├── 2024 July.csv
├── ...
├── 2025 June.csv
└── latest.csv
```

数据格式示例：
```csv
Date,YearMonthDay,YearMonth,Diseases,DiseasesCN,Cases,Deaths,Incidence,Mortality,Province,Source
2025-06-01,2025/06/01,2025 June,Hepatitis B,乙型肝炎,105033,38,-10,-10,China,GOV Data
```

## 快速开始

### 方法 1：使用 CLI 命令（推荐）

```bash
cd /home/likangguo/globalID/globalID2

# 1. 初始化数据库（如果还没有）
python main.py init-database

# 2. 迁移数据
python main.py migrate-data
```

### 方法 2：指定自定义路径

```bash
python main.py migrate-data --data-path /path/to/your/data
```

### 方法 3：直接运行脚本

```bash
python scripts/migrate_data.py /home/likangguo/globalID/ID_CN/Data/AllData/CN
```

## 迁移过程

1. **读取 CSV 文件**: 自动扫描目录中的所有 CSV 文件
2. **解析数据**: 
   - 日期格式转换
   - 疾病名称标准化
   - 数值清洗（处理 -10 缺失值）
3. **创建疾病记录**: 
   - 自动创建缺失的疾病
   - 分类疾病（respiratory, hepatitis, etc.）
4. **导入数据库**:
   - 批量插入（1000条/批次）
   - 跳过重复记录
5. **显示统计**: 总记录数、疾病数、时间范围

## 特性

### ✅ 智能映射

疾病名称自动映射到标准名称：
- `Acquired immune deficiency syndrome` → `HIV/AIDS`
- `Human infection with H5N1 virus` → `Avian Influenza H5N1`
- `Epidemic hemorrhagic fever` → `Hemorrhagic Fever`

### ✅ 数据清洗

- 处理缺失值（-10 → NULL）
- 计算病死率：`死亡数 / 病例数 * 100`
- 标记数据质量：`high`（官方数据）
- 设置可信度：`0.9`（政府数据源）

### ✅ 去重处理

基于以下组合检查重复：
- 时间（Date）
- 疾病（Disease）
- 国家（Country）

### ✅ 自动分类

根据疾病名称自动分类：
- `hepatitis`: 肝炎相关
- `respiratory`: 呼吸道疾病
- `immunodeficiency`: 免疫缺陷
- `bacterial`: 细菌性疾病
- `viral_exanthematous`: 病毒性出疹性疾病
- `hemorrhagic`: 出血热
- `neurological`: 神经系统疾病
- `gastrointestinal`: 消化道疾病

## 预期结果

完成迁移后，您将看到：

```
Migration completed!
  Imported: 85,432
  Skipped: 1,234
  Errors: 0

Migration Statistics:
  Total records: 85,432
  Total diseases: 45
  Date range: 2024-05-01 to 2025-06-01

Top 10 diseases by records:
  1. Hepatitis B: 12,345 records
  2. HIV/AIDS: 8,765 records
  3. Tuberculosis: 7,654 records
  ...
```

## 验证迁移

迁移完成后，验证数据：

```bash
# 运行测试
python main.py test

# 或查询数据库
python -c "
from src.core import init_app, get_database
from src.domain import DiseaseRecord
from sqlalchemy import select, func
import asyncio

async def check():
    await init_app()
    db = get_database()
    count = await db.scalar(select(func.count(DiseaseRecord.id)))
    print(f'Total records: {count}')

asyncio.run(check())
"
```

## 常见问题

### Q: 迁移需要多长时间？
A: 约 2-5 分钟，取决于数据量（~85,000 条记录）

### Q: 可以多次运行吗？
A: 可以！默认跳过已存在的记录（`--skip-existing=True`）

### Q: 如何重新导入？
A: 1. 清空数据库表，或 2. 使用 `--skip-existing=False`（会报错重复）

### Q: 数据源文件会被修改吗？
A: 不会！脚本只读取 CSV 文件，不会修改源文件

### Q: 支持哪些数据格式？
A: 目前支持 ID_CN 的 CSV 格式。如需其他格式，请修改 `scripts/migrate_data.py`

## 排错

### 问题 1: 数据库连接失败

```bash
# 检查数据库是否运行
sudo systemctl status postgresql

# 检查 .env 配置
cat .env | grep DATABASE_URL
```

### 问题 2: 找不到 CSV 文件

```bash
# 检查路径
ls /home/likangguo/globalID/ID_CN/Data/AllData/CN/

# 使用绝对路径
python main.py migrate-data --data-path /absolute/path/to/data
```

### 问题 3: 内存不足

```bash
# 编辑脚本，减小批量大小
# 将 1000 改为 500
vim scripts/migrate_data.py
# 找到: if imported % 1000 == 0:
# 改为: if imported % 500 == 0:
```

## 下一步

迁移完成后：

1. ✅ 生成第一份报告
   ```bash
   python main.py generate-report --country CN --report-type monthly
   ```

2. ✅ 运行完整测试
   ```bash
   python main.py test
   ```

3. ✅ 设置定时任务
   ```bash
   # 每周自动生成报告
   crontab -e
   # 添加: 0 9 * * 1 cd /path/to/globalID2 && python main.py run --full
   ```

## 联系支持

遇到问题？查看日志：
```bash
tail -f logs/globalid.log
```
