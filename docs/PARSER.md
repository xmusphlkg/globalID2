# Parser 模块使用文档

GlobalID V2 的 Parser 模块负责解析爬取的数据，提取结构化信息，并进行标准化处理。

## 📦 模块结构

```
src/data/
├── parsers/            # 解析器模块
│   ├── base.py         # 基础解析器类
│   └── html_parser.py  # HTML表格解析器
│
├── normalizers/        # 标准化模块
│   └── disease_mapper.py  # 疾病名称映射器
│
└── processors/         # 数据处理器
    └── data_processor.py  # 整合解析和标准化的完整流程
```

## 🚀 快速开始

### 1. 基础HTML表格解析

```python
from src.data.parsers.html_parser import HTMLTableParser

# 初始化解析器
parser = HTMLTableParser()

# 从URL解析
result = parser.parse_from_url(
    url="https://weekly.chinacdc.cn/en/article/doi/10.46234/ccdcw2024.001",
    title="National Notifiable Infectious Diseases",
    date="2024-01-01",
    year_month="2024 January",
    source="China CDC Weekly",
    language="en",  # 'en' 或 'zh'
)

if result.success and result.has_data:
    df = result.data
    print(f"解析成功，共 {len(df)} 行数据")
    print(df.head())
else:
    print(f"解析失败: {result.error_message}")
```

### 2. 疾病名称映射

```python
from src.data.normalizers.disease_mapper import DiseaseMapper

# 初始化映射器
mapper = DiseaseMapper()

# 中文 -> 英文
en_name = mapper.map_to_english("新型冠状病毒肺炎")
print(en_name)  # "COVID-19"

# 英文 -> 中文
zh_name = mapper.map_to_chinese("Tuberculosis")
print(zh_name)  # "肺结核"

# 添加新映射
mapper.add_mapping(
    english_name="Novel Disease",
    chinese_name="新疾病",
    aliases=["ND", "New Disease"],
)
```

### 3. 完整数据处理流程

```python
from pathlib import Path
from src.data.processors import DataProcessor

# 初始化处理器
processor = DataProcessor(
    output_dir=Path("data/processed"),
)

# 处理单个URL
df = processor.process_single_url(
    url="https://weekly.chinacdc.cn/en/article/doi/10.46234/ccdcw2024.001",
    metadata={
        "title": "National Notifiable Infectious Diseases",
        "date": "2024-01-01",
        "year_month": "2024 January",
        "source": "China CDC Weekly",
        "language": "en",
        "doi": "10.46234/ccdcw2024.001",
    }
)

if df is not None:
    print(f"处理成功，共 {len(df)} 行数据")
```

### 4. 批量处理爬虫结果

```python
from src.data.crawlers.cn_cdc import ChinaCDCCrawler
from src.data.processors import DataProcessor

# 爬取数据
crawler = ChinaCDCCrawler()
results = await crawler.crawl(source="cdc_weekly")

# 批量处理
processor = DataProcessor()
processed_data = processor.process_crawler_results(
    results,
    save_to_file=True,  # 自动保存到文件
)

print(f"成功处理 {len(processed_data)} 条数据")
```

## 📊 数据格式

### 输入格式

Parser 接受两种来源的数据：
1. **URL**: 直接从网页URL解析
2. **HTML内容**: 传入HTML字符串

### 输出格式

解析后的数据包含以下列：

| 列名 | 说明 | 示例 |
|------|------|------|
| `Date` | 报告日期 | `2024-01-01` |
| `YearMonthDay` | 年月日 | `2024/01/01` |
| `YearMonth` | 年月 | `2024 January` |
| `Diseases` | 疾病英文名称 | `COVID-19` |
| `DiseasesCN` | 疾病中文名称 | `新型冠状病毒肺炎` |
| `Cases` | 病例数 | `12345` |
| `Deaths` | 死亡数 | `123` |
| `Incidence` | 发病率 | `10.5` |
| `Mortality` | 死亡率 | `0.5` |
| `Province` | 省份（英文） | `China` |
| `ProvinceCN` | 省份（中文） | `全国` |
| `ADCode` | 行政区划代码 | `100000` |
| `DOI` | 文献DOI | `10.46234/ccdcw2024.001` |
| `URL` | 数据来源URL | `https://...` |
| `Source` | 数据源名称 | `China CDC Weekly` |

## 🔧 高级功能

### 自定义解析规则

```python
from src.data.parsers.base import BaseParser, ParseResult

class CustomParser(BaseParser):
    def parse(self, content: str, **kwargs) -> ParseResult:
        # 自定义解析逻辑
        ...
        
    def validate(self, data: pd.DataFrame) -> bool:
        # 自定义验证逻辑
        ...
```

### 扩展疾病映射

```python
# 从CSV文件加载映射
mapper = DiseaseMapper(mapping_file=Path("custom_mapping.csv"))

# 批量添加映射
mappings = {
    "Disease A": "疾病A",
    "Disease B": "疾病B",
}

for en, zh in mappings.items():
    mapper.add_mapping(en, zh)
```

### 导出未识别的疾病

```python
# 获取未识别的疾病
unknown = mapper.get_unknown_diseases()
print(f"发现 {len(unknown)} 个未识别的疾病")

# 导出到文件供人工审核
mapper.export_unknown_diseases(Path("data/unknown_diseases.csv"))
```

## 🧪 测试

运行测试：

```bash
# 测试解析器
python tests/test_parser.py

# 运行示例
python examples/parser_examples.py
```

## 📝 配置文件

### 疾病映射文件格式

文件: `configs/disease_mapping.csv`

```csv
EnglishName,ChineseName,Aliases
COVID-19,新型冠状病毒肺炎,新冠肺炎|Novel Coronavirus Pneumonia
Tuberculosis,肺结核,TB|结核病
AIDS,艾滋病,HIV
...
```

## 🔍 最佳实践

### 1. 错误处理

```python
try:
    result = parser.parse_from_url(url)
    if not result.success:
        logger.error(f"解析失败: {result.error_message}")
        # 降级处理
except Exception as e:
    logger.error(f"发生异常: {e}")
    # 错误恢复
```

### 2. 数据验证

```python
# 解析后验证数据
if processor._validate_data(df):
    # 数据有效，继续处理
    ...
else:
    # 数据无效，记录日志
    logger.warning("数据验证失败")
```

### 3. 批量处理优化

```python
# 使用进度条
from tqdm import tqdm

for result in tqdm(crawler_results, desc="处理数据"):
    df = processor.process_single_url(result.url, result.metadata)
    if df is not None:
        # 保存或进一步处理
        ...
```

## 🐛 常见问题

### Q1: 解析表格失败

**原因**: HTML结构不符合预期

**解决**: 
- 检查表格结构是否正确
- 使用浏览器开发者工具查看HTML
- 自定义解析规则

### Q2: 疾病名称映射失败

**原因**: 映射表中没有该疾病

**解决**:
- 添加新的映射关系
- 检查"未识别疾病"列表
- 使用模糊匹配

### Q3: 数据验证失败

**原因**: 数据格式不符合要求

**解决**:
- 检查必需的列是否存在
- 验证数值列是否可转换
- 查看详细的错误日志

## 📚 相关文档

- [Architecture V2](../docs/ARCHITECTURE_V2.md) - 整体架构设计
- [Crawler 文档](./CRAWLER.md) - 爬虫模块使用
- [API 文档](../docs/API.md) - API接口文档

## 🤝 贡献

欢迎贡献代码！主要需求：
- [ ] 添加更多解析器（PDF、JSON等）
- [ ] 完善疾病映射表
- [ ] 优化解析性能
- [ ] 增加单元测试

## 📄 许可证

MIT License
