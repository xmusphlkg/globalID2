# GlobalID V2 Examples

示例代码，展示系统的各种使用方式。

## 运行示例

```bash
cd globalID2
python examples/quick_start.py
```

## 可用示例

1. **数据分析**: 使用 AnalystAgent 分析疾病数据
2. **内容撰写**: 使用 WriterAgent 撰写报告内容
3. **完整工作流程**: 数据分析 → 内容撰写 → 质量审核
4. **生成报告**: 完整的报告生成流程

## 示例说明

### 1. 数据分析

```python
from src.ai.agents import AnalystAgent
import pandas as pd

analyst = AnalystAgent()

result = await analyst.process(
    data=data_frame,
    disease_name="COVID-19",
    period_start=start_date,
    period_end=end_date,
)

print(result['statistics'])
print(result['trends'])
print(result['insights'])
```

### 2. 内容撰写

```python
from src.ai.agents import WriterAgent

writer = WriterAgent()

result = await writer.process(
    section_type="summary",
    analysis_data=analysis_result,
    style="formal",
    language="zh",
)

print(result['content'])
```

### 3. 质量审核

```python
from src.ai.agents import ReviewerAgent

reviewer = ReviewerAgent()

result = await reviewer.process(
    content=written_content,
    content_type="summary",
    original_data=analysis_data,
)

print(f"Approved: {result['approved']}")
print(f"Score: {result['quality_score']['overall']}")
```

### 4. 生成报告

```python
from src.generation import ReportGenerator
from src.domain import ReportType

generator = ReportGenerator()

report = await generator.generate(
    country_id=1,
    report_type=ReportType.WEEKLY,
    period_start=start_date,
    period_end=end_date,
)

print(f"Report: {report.html_path}")
```
