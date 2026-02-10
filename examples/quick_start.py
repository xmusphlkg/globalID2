"""
GlobalID V2 Quick Start Example

快速开始示例：演示系统的基本使用
"""
import asyncio
from datetime import datetime, timedelta

from src.core import init_app, get_logger
from src.domain import ReportType
from src.generation import ReportGenerator

logger = get_logger(__name__)


async def example_generate_report():
    """
    示例：生成周报
    """
    # 初始化应用
    await init_app()
    
    logger.info("Starting report generation example...")
    
    # 创建报告生成器
    generator = ReportGenerator()
    
    # 设置时间范围（最近7天）
    period_end = datetime.utcnow()
    period_start = period_end - timedelta(days=7)
    
    # 生成周报
    report = await generator.generate(
        country_id=1,  # 假设中国的ID是1
        report_type=ReportType.WEEKLY,
        period_start=period_start,
        period_end=period_end,
        title="COVID-19 周度监测报告",
        send_email=False,  # 设置为True以发送邮件
        enable_review=True,  # 启用AI审核
    )
    
    logger.info(f"""
报告生成完成！
    
报告ID: {report.id}
状态: {report.status}
标题: {report.title}
时间范围: {period_start.date()} 至 {period_end.date()}

文件位置:
- Markdown: {report.markdown_path}
- HTML: {report.html_path}
- PDF: {report.pdf_path or '未生成'}

章节数: {len(report.sections)}
生成时间: {report.generated_at}
完成时间: {report.completed_at}
""")
    
    return report


async def example_data_analysis():
    """
    示例：单独使用AI Agent进行数据分析
    """
    from src.ai.agents import AnalystAgent
    import pandas as pd
    
    logger.info("Starting data analysis example...")
    
    # 准备测试数据
    dates = pd.date_range(start='2024-01-01', end='2024-01-30', freq='D')
    data = pd.DataFrame({
        'time': dates,
        'cases': [100 + i * 5 + (i % 7) * 10 for i in range(len(dates))],
        'deaths': [2 + i // 5 for i in range(len(dates))],
        'recoveries': [95 + i * 4 for i in range(len(dates))],
    })
    
    # 创建分析师Agent
    analyst = AnalystAgent()
    
    # 分析数据
    result = await analyst.process(
        data=data,
        disease_name="COVID-19",
        period_start=datetime(2024, 1, 1),
        period_end=datetime(2024, 1, 30),
    )
    
    logger.info(f"""
分析完成！

统计数据:
- 总病例: {result['statistics'].get('total_cases', 'N/A')}
- 总死亡: {result['statistics'].get('total_deaths', 'N/A')}
- 平均病例: {result['statistics'].get('avg_cases', 'N/A'):.1f}
- 病死率: {result['statistics'].get('fatality_rate', 'N/A')}%

趋势分析:
- 变化率: {result['trends'].get('cases_change_rate', 'N/A')}%
- 趋势方向: {result['trends'].get('cases_trend', 'N/A')}

异常检测:
- 检测到: {len(result['anomalies'])} 个异常值

数据质量:
- 质量评分: {result['data_quality'].get('score', 0):.2f}
- 完整性: {result['data_quality'].get('completeness', 0):.2f}

AI洞察:
{result['insights']}
""")
    
    return result


async def example_content_writing():
    """
    示例：使用AI Agent撰写内容
    """
    from src.ai.agents import WriterAgent
    
    logger.info("Starting content writing example...")
    
    # 准备分析数据
    analysis_data = {
        "disease_name": "COVID-19",
        "period": {
            "start": "2024-01-01",
            "end": "2024-01-30",
        },
        "statistics": {
            "total_cases": 45000,
            "total_deaths": 850,
            "avg_cases": 1500,
            "fatality_rate": 1.89,
        },
        "trends": {
            "cases_change_rate": -12.5,
            "cases_trend": "decreasing",
        },
        "anomalies": [],
    }
    
    # 创建作家Agent
    writer = WriterAgent()
    
    # 撰写摘要
    result = await writer.process(
        section_type="summary",
        analysis_data=analysis_data,
        style="formal",  # 可选: formal/popular/technical
        language="zh",    # 可选: zh/en
    )
    
    logger.info(f"""
内容撰写完成！

章节类型: {result['section_type']}
写作风格: {result['style']}
语言: {result['language']}
字数: {result['word_count']}

内容预览:
{'-' * 60}
{result['content'][:500]}...
{'-' * 60}
""")
    
    return result


async def example_complete_workflow():
    """
    示例：完整的工作流程
    
    数据分析 → 内容撰写 → 质量审核
    """
    from src.ai.agents import AnalystAgent, WriterAgent, ReviewerAgent
    import pandas as pd
    
    logger.info("Starting complete workflow example...")
    
    # 1. 准备数据
    dates = pd.date_range(start='2024-01-01', end='2024-01-30', freq='D')
    data = pd.DataFrame({
        'time': dates,
        'cases': [100 + i * 5 for i in range(len(dates))],
        'deaths': [2 + i // 5 for i in range(len(dates))],
    })
    
    # 2. 数据分析
    logger.info("Step 1: Analyzing data...")
    analyst = AnalystAgent()
    analysis = await analyst.process(
        data=data,
        disease_name="COVID-19",
        period_start=datetime(2024, 1, 1),
        period_end=datetime(2024, 1, 30),
    )
    logger.info("✓ Analysis completed")
    
    # 3. 内容撰写
    logger.info("Step 2: Writing content...")
    writer = WriterAgent()
    content = await writer.process(
        section_type="summary",
        analysis_data=analysis,
        style="formal",
        language="zh",
    )
    logger.info("✓ Writing completed")
    
    # 4. 质量审核
    logger.info("Step 3: Reviewing quality...")
    reviewer = ReviewerAgent()
    review = await reviewer.process(
        content=content['content'],
        content_type="summary",
        original_data=analysis,
    )
    logger.info("✓ Review completed")
    
    # 5. 结果汇总
    logger.info(f"""
{'=' * 60}
完整工作流程完成！
{'=' * 60}

分析结果:
- 总病例: {analysis['statistics']['total_cases']}
- 趋势: {analysis['trends']['cases_trend']}

撰写结果:
- 字数: {content['word_count']}

审核结果:
- 是否通过: {'✓ 通过' if review['approved'] else '✗ 需修改'}
- 质量评分: {review['quality_score']['overall']:.2f}
- 建议数: {len(review['suggestions'])}

最终内容:
{'-' * 60}
{content['content']}
{'-' * 60}

审核意见:
{review['assessment']}
{'=' * 60}
""")
    
    return {
        'analysis': analysis,
        'content': content,
        'review': review,
    }


async def main():
    """主函数：运行所有示例"""
    logger.info("=" * 60)
    logger.info("GlobalID V2 Examples")
    logger.info("=" * 60)
    
    # 选择要运行的示例
    examples = {
        '1': ('数据分析', example_data_analysis),
        '2': ('内容撰写', example_content_writing),
        '3': ('完整工作流程', example_complete_workflow),
        '4': ('生成报告', example_generate_report),
    }
    
    print("\n可用示例:")
    for key, (name, _) in examples.items():
        print(f"  {key}. {name}")
    
    choice = input("\n请选择要运行的示例 (1-4, 或 'all' 运行全部): ").strip()
    
    if choice == 'all':
        for name, func in examples.values():
            print(f"\n{'=' * 60}")
            print(f"运行示例: {name}")
            print(f"{'=' * 60}")
            try:
                await func()
            except Exception as e:
                logger.error(f"示例 '{name}' 执行失败: {e}")
                logger.exception(e)
    elif choice in examples:
        name, func = examples[choice]
        print(f"\n{'=' * 60}")
        print(f"运行示例: {name}")
        print(f"{'=' * 60}")
        await func()
    else:
        print("无效的选择")
        return
    
    logger.info("\n✓ 所有示例运行完成！")


if __name__ == "__main__":
    asyncio.run(main())
