"""
测试 Parser 和 Processor 模块

演示如何使用爬虫 + 解析器 + 处理器的完整流程
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import pytest

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.logging import setup_logging
from src.data.crawlers.cn import ChinaCDCCrawler
from src.data.processors import DataProcessor


@pytest.mark.asyncio
async def test_parser_basic():
    """测试基础解析功能"""
    print("=" * 60)
    print("测试1: 基础解析功能")
    print("=" * 60)
    
    # 初始化处理器
    processor = DataProcessor(
        output_dir=Path("data/processed"),
    )
    
    # 测试单个URL
    test_url = "https://weekly.chinacdc.cn/en/article/doi/10.46234/ccdcw2022.157"
    
    print(f"\n解析URL: {test_url}")
    df = processor.process_single_url(
        url=test_url,
        metadata={
            "title": "Notifiable Infectious Diseases Reports: Reported Cases and Deaths of National Notifiable Infectious Diseases — China, June 2022",
            "date": datetime(2022, 6, 1),
            "year_month": "2022 June",
            "source": "China CDC Weekly",
            "language": "en",
            "doi": "10.46234/ccdcw2022.157",
        }
    )
    
    if df is not None:
        print(f"\n✅ 解析成功！")
        print(f"数据行数: {len(df)}")
        print(f"数据列数: {len(df.columns)}")
        print(f"\n前5行数据:")
        print(df.head())
    else:
        print("\n❌ 解析失败")


@pytest.mark.asyncio
async def test_crawler_integration():
    """测试爬虫集成"""
    print("\n" + "=" * 60)
    print("测试2: 爬虫 + 解析器集成")
    print("=" * 60)
    
    # 初始化爬虫和处理器
    crawler = ChinaCDCCrawler()
    processor = DataProcessor(
        output_dir=Path("data/processed"),
    )
    
    # 爬取数据（只爬取CDC Weekly）
    print("\n开始爬取数据...")
    results = await crawler.crawl(source="cdc_weekly")
    
    print(f"\n爬取到 {len(results)} 条记录")
    
    if results:
        # 只处理前3条数据作为测试
        test_results = results[:3]
        print(f"\n处理前 {len(test_results)} 条数据...")
        
        # 处理数据
        processed_data = processor.process_crawler_results(
            test_results,
            save_to_file=True,
        )
        
        print(f"\n✅ 成功处理 {len(processed_data)} 条数据")
        
        # 显示第一个数据框的信息
        if processed_data:
            df = processed_data[0]
            print(f"\n第一个数据框信息:")
            print(f"- 行数: {len(df)}")
            print(f"- 列数: {len(df.columns)}")
            print(f"- 疾病数量: {df['Diseases'].nunique()}")
            print(f"\n前3行数据:")
            print(df.head(3))
    else:
        print("\n❌ 没有爬取到数据")


@pytest.mark.asyncio
async def test_disease_mapper():
    """测试疾病映射器（数据库版）"""
    print("\n" + "=" * 60)
    print("测试3: 疾病名称映射（数据库版）")
    print("=" * 60)
    
    from src.data.normalizers.disease_mapper_db import DiseaseMapperDB
    
    mapper = DiseaseMapperDB(country_code="cn")
    
    # 显示统计信息
    stats = await mapper.get_statistics()
    print(f"\n📊 映射统计:")
    print(f"  国家: {stats['country_code'].upper()}")
    print(f"  标准疾病库: {stats['standard_diseases']} 条")
    print(f"  本地映射: {stats['total_mappings']} 条")
    print(f"    - 主名称: {stats['primary_mappings']} 条")
    print(f"    - 别名: {stats['alias_mappings']} 条")
    print(f"  待审核建议: {stats['pending_suggestions']} 条")
    
    # 测试中文 -> 英文（通过标准ID）
    test_diseases_zh = [
        "新型冠状病毒肺炎",
        "新冠肺炎",  # 别名
        "肺结核",
        "艾滋病",
        "手足口病",
        "流行性感冒",
    ]
    
    print("\n🇨🇳 中文本地名称 -> 标准疾病ID -> 标准英文名:")
    for disease_zh in test_diseases_zh:
        disease_id = await mapper.map_local_to_id(disease_zh)
        if disease_id:
            info = await mapper.get_standard_info(disease_id)
            standard_en = info.standard_name_en if info else None
            print(f"  {disease_zh:15s} -> {disease_id:6s} -> {standard_en or '未找到'}")
        else:
            print(f"  {disease_zh:15s} -> 未找到 -> 未找到")
    
    # 测试DataFrame批量映射
    print("\n📊 DataFrame批量映射:")
    test_df = pd.DataFrame({
        'DiseasesCN': [
            '新冠肺炎',
            '肺结核',  
            '艾滋病',
            '手足口病',
            '未知疾病XYZ',
        ],
        'Cases': [100, 200, 50, 80, 10]
    })
    
    print("原始数据:")
    print(test_df.to_string(index=False))
    
    mapped_df = await mapper.map_dataframe(test_df.copy(), 'DiseasesCN')
    
    print("\n映射后数据:")
    if 'disease_id' in mapped_df.columns and 'standard_name_en' in mapped_df.columns:
        print(mapped_df[['DiseasesCN', 'disease_id', 'standard_name_en', 'Cases']].to_string(index=False))
    else:
        print(mapped_df.to_string(index=False))
    
    mapped_count = mapped_df['disease_id'].notna().sum() if 'disease_id' in mapped_df.columns else 0
    print(f"\n映射成功: {mapped_count}/{len(mapped_df)}")
    
    # 测试别名
    print("\n🔄 别名测试:")
    test_aliases = [
        ("新冠", "COVID-19"),
        ("非典", "SARS"),
        ("HIV", "HIV/AIDS"),
    ]
    
    for alias, expected in test_aliases:
        disease_id = await mapper.map_local_to_id(alias)
        if disease_id:
            info = await mapper.get_standard_info(disease_id)
            standard_en = info.standard_name_en if info else None
            status = "✓" if standard_en and expected in standard_en else "✗"
            print(f"  {status} {alias:15s} -> {disease_id:6s} -> {standard_en or '未找到'} (期望: {expected})")
        else:
            print(f"  ✗ {alias:15s} -> 未找到 (期望: {expected})")


async def main():
    """主函数"""
    # 设置日志
    setup_logging()
    
    print("\n" + "🚀 GlobalID Parser 测试" + "\n")
    
    try:
        # 测试1: 基础解析
        # await test_parser_basic()
        
        # 测试2: 爬虫集成
        # await test_crawler_integration()
        
        # 测试3: 疾病映射
        await test_disease_mapper()
        
        print("\n" + "=" * 60)
        print("✅ 所有测试完成")
        print("=" * 60 + "\n")
        print("\n💡 提示:")
        print("  - 取消注释 test_parser_basic() 可测试单个URL解析")
        print("  - 取消注释 test_crawler_integration() 可测试完整爬虫流程")
        print("  - 这些测试需要网络连接")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
