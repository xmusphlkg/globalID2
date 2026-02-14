# 疾病标准库采集与增强系统

## 📚 概述

本系统从多个权威数据源采集疾病标准信息，并进行数据增强处理，为GlobalID项目提供完整的国际化疾病标准库。

## 🎯 数据来源

### 1. WHO ICD-10
- **来源**: 世界卫生组织国际疾病分类第10版
- **覆盖**: 78种常见传染病
- **编码范围**: A00-B99, U04-U07
- **权威性**: ⭐⭐⭐⭐⭐

### 2. CDC Notifiable Diseases
- **来源**: 美国疾病控制与预防中心法定报告疾病目录
- **覆盖**: 63种美国法定传染病（2024年版）
- **权威性**: ⭐⭐⭐⭐⭐

### 3. China Notifiable Diseases
- **来源**: 中国法定传染病目录
- **覆盖**: 58种中国法定传染病（甲乙丙类+监测）
- **权威性**: ⭐⭐⭐⭐⭐

## 📊 数据统计

```
总疾病数: 141种
├─ ICD-10编码覆盖率: 70.2%
├─ ICD-11编码覆盖率: 100.0%
└─ 中文名称覆盖率: 100.0%

按分类统计:
├─ 细菌性疾病 (Bacterial): 47种 (33.3%)
├─ 病毒性疾病 (Viral): 44种 (31.2%)
├─ 寄生虫病 (Parasitic): 21种 (14.9%)
└─ 真菌病 (Fungal): 1种 (0.7%)
```

## 🚀 使用方法

### 1. 采集疾病数据

从权威数据源采集疾病基础信息：

```bash
# 运行采集脚本
./venv/bin/python scripts/collect_disease_standards.py

# 输出文件
# - configs/sources/icd10_diseases.csv (WHO ICD-10数据)
# - configs/sources/cdc_diseases.csv (美国CDC数据)
# - configs/sources/diseases_full_20260211.csv (合并数据)
```

### 2. 增强疾病数据

添加ICD-11编码、补充中文翻译、丰富描述信息：

```bash
# 运行增强脚本
./venv/bin/python scripts/enhance_disease_data.py

# 输出文件
# - configs/standard_diseases_enhanced.csv (增强后的数据)
```

### 3. 应用到系统

审核数据后替换现有标准库：

```bash
# 备份现有文件
cp configs/standard_diseases.csv configs/standard_diseases.backup.csv

# 替换为增强版本
mv configs/standard_diseases_enhanced.csv configs/standard_diseases.csv

# 重新测试映射系统
./venv/bin/python tests/test_parser.py
```

## 📁 文件结构

```
configs/
├── standard_diseases.csv              # 原始标准库（58种）
├── standard_diseases_enhanced.csv     # 增强后标准库（141种）
├── sources/                           # 原始采集数据
│   ├── icd10_diseases.csv            # ICD-10数据（78种）
│   ├── cdc_diseases.csv              # CDC数据（63种）
│   └── diseases_full_20260211.csv    # 合并数据（141种）
├── cn/                                # 中国映射
│   └── disease_mapping.csv
└── us/                                # 美国映射
    └── disease_mapping.csv
```

## 🔧 脚本说明

### collect_disease_standards.py

**功能**: 从多个权威数据源采集疾病基础信息

**数据源**:
1. ICD-10: 手动整理的常见传染病列表（78种）
2. CDC: 2024年美国法定报告传染病（63种）
3. China CDC: 从现有配置加载（58种）

**输出**: 
- 各数据源单独的CSV文件
- 合并后的完整数据集

**特点**:
- 自动去重
- 保留数据源信息
- 生成唯一disease_id

### enhance_disease_data.py

**功能**: 对采集的数据进行增强处理

**增强内容**:
1. **ICD-11编码映射**: 70+条ICD-10到ICD-11的映射
2. **中文翻译**: 90+种疾病的中文名称
3. **分类推断**: 根据ICD编码自动推断疾病分类
4. **数据合并**: 保留现有数据中的优质字段

**输出**: 
- 完整的增强数据集
- 统计报告
- 数据示例

## 📋 数据字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| disease_id | 疾病唯一标识符 | D001 |
| standard_name_en | 标准英文名称 | COVID-19 |
| standard_name_zh | 标准中文名称 | 新冠肺炎 |
| category | 疾病分类 | Viral |
| icd_10 | ICD-10编码 | U07.1 |
| icd_11 | ICD-11编码 | RA01 |
| description | 疾病描述 | Coronavirus Disease 2019 |
| source | 数据来源 | ICD-10, CDC |

## 🆕 新增疾病示例

相比原有58种疾病，新增了83种，包括：

**重要新增**:
- 各型肝炎的细分（急性/慢性）
- 性传播疾病
- 寄生虫病扩展
- 虫媒病毒病
- 其他细菌和病毒感染

**示例新增疾病**:
```
D064 - West Nile virus (西尼罗病毒)
D065 - Zika virus (寨卡病毒)
D083 - Lyme disease (莱姆病)
D089 - Legionellosis (军团菌病)
D102 - Trichinellosis (旋毛虫病)
D114 - Coccidioidomycosis (球孢子菌病)
```

## 🔄 更新流程

### 定期更新

建议每6个月检查一次数据源更新：

1. **WHO ICD更新**: 访问 https://icd.who.int/
2. **CDC列表更新**: 访问 https://www.cdc.gov/nndss/
3. **China CDC更新**: 访问国家疾控局官网

### 添加新疾病

```python
# 1. 在ICD-10列表中添加
icd10_diseases.append({
    "code": "A99",
    "name": "New Disease",
    "name_zh": "新疾病"
})

# 2. 在中文名称字典中添加
CHINESE_NAMES["New Disease"] = "新疾病"

# 3. 如需要，添加ICD-11映射
ICD10_TO_ICD11["A99"] = "1X99"

# 4. 重新运行脚本
./venv/bin/python scripts/collect_disease_standards.py
./venv/bin/python scripts/enhance_disease_data.py
```

## 📊 数据质量

### 完整性

- ✅ 所有疾病都有英文名称
- ✅ 所有疾病都有中文名称
- ✅ 所有疾病都有ICD-11编码
- ⚠️ 70.2%的疾病有ICD-10编码
- ⚠️ 部分疾病缺少详细描述

### 准确性

- ✅ ICD编码来自WHO官方文档
- ✅ 中文翻译参考国家标准
- ✅ 疾病分类基于ICD体系
- ⚠️ 需要医学专业人员审核

### 一致性

- ✅ 命名规范统一
- ✅ 编码格式标准
- ✅ 分类体系一致

## ⚠️ 注意事项

1. **数据审核**: 增强后的数据需要医学专业人员审核
2. **编码更新**: ICD编码会定期更新，需要跟进
3. **翻译准确性**: 部分疾病的中文翻译可能需要调整
4. **版权问题**: ICD编码有版权，使用需遵守WHO许可
5. **数据备份**: 更新前务必备份现有数据

## 🔗 相关链接

- **WHO ICD-10**: https://icd.who.int/browse10/2019/en
- **WHO ICD-11**: https://icd.who.int/browse11/
- **CDC NNDSS**: https://www.cdc.gov/nndss/conditions/
- **China CDC**: https://www.ndcpa.gov.cn/
- **ICD-10 to ICD-11 Mapping**: https://icd.who.int/icd11map/

## 📝 更新日志

### 2026-02-11
- ✅ 创建疾病数据采集系统
- ✅ 从ICD-10采集78种疾病
- ✅ 从CDC采集63种疾病
- ✅ 数据增强：添加ICD-11编码
- ✅ 数据增强：补充中文翻译
- ✅ 生成141种疾病的完整标准库
- ✅ 覆盖率：ICD-11 100%, 中文名 100%

### 未来计划
- [ ] 接入WHO ICD API
- [ ] 自动化定期更新
- [ ] 添加疾病关系图谱
- [ ] 支持更多语言
- [ ] 接入MeSH数据库
- [ ] 添加疾病症状信息

## 🤝 贡献

欢迎贡献：
1. 补充遗漏的疾病
2. 改进中文翻译
3. 添加疾病描述
4. 验证ICD编码
5. 报告数据问题

## 📄 许可

- 标准库数据遵守WHO ICD许可协议
- CDC数据为公共领域
- 本项目代码遵循项目主许可协议
