# 疾病映射系统 v2.0 - 国际化设计文档

> **兼容层说明**：本文描述的是旧版“一条来源标签映射到一个标准疾病 ID”模型，
> 目前仅用于 `disease_records` 等旧接口的兼容投影，不再作为监测数据的语义事实源。
> 新增或修改疾病、来源和监测口径时，请遵循
> [疾病本体与来源系列系统 v3](./DISEASE_ONTOLOGY_V3.md)。

## 📌 设计理念

全新的疾病映射系统采用**中心化标准库 + 多国家映射表**的架构，解决了国际化数据对齐问题。

### 核心优势

1. **唯一标准ID**：每种疾病有全局唯一的`disease_id`（如D004代表COVID-19）
2. **多国家支持**：不同国家的疾病名称都映射到同一个标准ID
3. **名称灵活性**：支持各种本地名称、别名、变体
4. **易于扩展**：新增国家只需添加新的映射文件

## 🗂️ 文件结构

```
configs/
├── standard_diseases.csv          # 标准疾病库（全局唯一）
├── cn/
│   └── disease_mapping.csv        # 中国疾病映射
├── us/
│   └── disease_mapping.csv        # 美国疾病映射
└── uk/
    └── disease_mapping.csv        # 英国疾病映射（待扩展）
```

## 📋 标准疾病库 (standard_diseases.csv)

全局唯一的疾病标准定义：

| 字段 | 说明 | 示例 |
|------|------|------|
| disease_id | 疾病唯一标识符 | D004 |
| standard_name_en | 标准英文名称 | COVID-19 |
| standard_name_zh | 标准中文名称 | 新冠肺炎 |
| category | 疾病分类 | Viral |
| icd_10 | ICD-10编码 | U07.1 |
| icd_11 | ICD-11编码 | RA01 |
| description | 疾病描述 | Coronavirus Disease 2019 |

**示例数据：**
```csv
disease_id,standard_name_en,standard_name_zh,category,icd_10,icd_11,description
D004,COVID-19,新冠肺炎,Viral,U07.1,RA01,Coronavirus Disease 2019
D025,Tuberculosis,肺结核,Bacterial,A15-A19,1B10,Mycobacterium tuberculosis infection
```

## 🌍 国家映射表 (configs/{country}/disease_mapping.csv)

每个国家的本地名称映射到标准disease_id：

| 字段 | 说明 | 示例 |
|------|------|------|
| disease_id | 关联到标准疾病库 | D004 |
| local_name | 本地官方名称 | 新型冠状病毒感染 |
| local_code | 本地代码 | COVID-19 |
| category | 本地分类 | 乙类 |
| aliases | 别名列表（\|分隔） | 新冠\|新型冠状病毒肺炎\|新冠肺炎 |
| data_source | 数据来源 | China CDC |

**中国示例：**
```csv
disease_id,local_name,local_code,category,aliases,data_source
D004,新型冠状病毒感染,COVID-19,乙类,新冠|新型冠状病毒肺炎|新冠肺炎,China CDC
D025,肺结核,Tuberculosis,乙类,结核病|TB,China CDC
```

**美国示例：**
```csv
disease_id,local_name,local_code,category,aliases,data_source
D004,COVID-19,COVID-19,Notifiable,Coronavirus Disease 2019|SARS-CoV-2,US CDC
D025,Tuberculosis,Tuberculosis,Notifiable,TB|Mycobacterium tuberculosis,US CDC
```

## 💻 使用示例

### 基础用法

```python
from src.data.normalizers.disease_mapper import DiseaseMapper

# 初始化中国映射器
mapper = DiseaseMapper(country_code="cn")

# 1. 本地名称 -> 疾病ID
disease_id = mapper.map_local_to_id("新冠肺炎")  # -> "D004"

# 2. 疾病ID -> 标准英文名
standard_name = mapper.get_standard_name(disease_id, lang="en")  # -> "COVID-19"

# 3. 疾病ID -> 中文标准名
standard_zh = mapper.get_standard_name(disease_id, lang="zh")  # -> "新冠肺炎"

# 4. 疾病ID -> 本地官方名称
local_name = mapper.map_id_to_local(disease_id)  # -> "新型冠状病毒感染"

# 5. 一步到位：本地名 -> 标准英文名
standard_name = mapper.map_local_to_standard("新冠肺炎", lang="en")  # -> "COVID-19"
```

### DataFrame批量映射

```python
import pandas as pd

# 原始数据（中文疾病名）
df = pd.DataFrame({
    'DiseasesCN': ['新型冠状病毒感染', '肺结核', '艾滋病'],
    'Cases': [17916, 52889, 2805]
})

# 批量映射
df = mapper.map_dataframe(
    df,
    source_col='DiseasesCN',      # 源列
    target_col='Diseases',        # 目标列（标准英文名）
    add_id_col=True,              # 添加disease_id列
    add_standard_col=True         # 添加标准英文名列
)

# 结果：
#        DiseasesCN disease_id      Diseases
# 0  新型冠状病毒感染       D004      COVID-19
# 1          肺结核       D025  Tuberculosis
# 2          艾滋病       D005      HIV/AIDS
```

### 处理未知疾病

```python
# 获取未识别的疾病
unknown = mapper.get_unknown_diseases()
print(unknown)  # {'某个新发现的疾病'}

# 导出到文件供人工审核
mapper.export_unknown_diseases(Path("exports/unknown_diseases.csv"))

# 临时添加映射（仅内存中）
mapper.add_temporary_mapping(
    local_name="某个新疾病",
    disease_id="D058",
    aliases=["新疾病别名"]
)
```

### 获取统计信息

```python
stats = mapper.get_statistics()
print(stats)
# {
#     'country_code': 'cn',
#     'standard_diseases_count': 58,
#     'local_mappings_count': 58,
#     'total_recognizable_names': 106,
#     'unknown_diseases_count': 0
# }
```

## 🔄 工作流程

```
爬虫数据（原始疾病名）
    ↓
Parser（保留原始名称）
    ↓
DiseaseMapper
    ├─ 本地名称 → disease_id
    ├─ disease_id → 标准英文名
    └─ disease_id → 标准中文名
    ↓
标准化数据
    ├─ disease_id: D004
    ├─ Diseases: COVID-19
    └─ DiseasesCN: 新冠肺炎
    ↓
数据库存储
```

## 📊 数据库schema更新

建议在`disease_records`表中添加`disease_id`字段：

```sql
ALTER TABLE disease_records ADD COLUMN disease_id VARCHAR(10);
ALTER TABLE disease_records ADD FOREIGN KEY (disease_id) REFERENCES standard_diseases(disease_id);
```

## 🆕 新增国家支持

1. 在`configs/`下创建国家目录：`configs/jp/`
2. 创建映射文件`disease_mapping.csv`
3. 按格式填充本地名称和别名
4. 初始化：`DiseaseMapper(country_code="jp")`

## ⚠️ 注意事项

1. **标准疾病库是权威来源**：所有修改应在此文件进行
2. **disease_id不可变**：一旦分配，不应修改
3. **本地映射可灵活调整**：添加新别名、修正本地名称等
4. **别名要全面**：包含常见变体、缩写、俗称等
5. **定期审核未识别疾病**：及时补充到映射文件

## 🔍 迁移指南

从旧系统迁移：

```python
# 旧代码
mapper = DiseaseMapper(mapping_file="configs/disease_mapping.csv")
en_name = mapper.map_to_english("新冠肺炎")
zh_name = mapper.map_to_chinese("COVID-19")

# 新代码
mapper = DiseaseMapper(country_code="cn")
en_name = mapper.map_local_to_standard("新冠肺炎", lang="en")
disease_id = mapper.map_local_to_id("新冠肺炎")
zh_name = mapper.get_standard_name(disease_id, lang="zh")
```

## 📈 未来扩展

- [ ] 支持更多国家（日本、韩国、印度等）
- [ ] 疾病分类层级结构
- [ ] 疾病关系图谱（并发症、前驱症状等）
- [ ] 自动从WHO获取最新ICD编码
- [ ] ML辅助的模糊匹配
- [ ] 数据库同步工具

## 🤝 贡献指南

添加新疾病到标准库：
1. 分配新的disease_id（递增）
2. 在`standard_diseases.csv`中添加标准信息
3. 在各国映射表中添加本地名称
4. 提交PR并注明来源
