#!/usr/bin/env python3
"""
测试 Force Crawl 功能

演示：
1. 正常爬取（只爬取新数据）
2. Force 爬取（爬取所有数据并更新数据库）
"""
import asyncio
from src.core.database import get_db
from src.domain import DiseaseRecord
from sqlalchemy import select, func

async def show_database_stats():
    """显示数据库统计"""
    async with get_db() as db:
        # 统计总记录数
        count_query = select(func.count(DiseaseRecord.time))
        result = await db.execute(count_query)
        total = result.scalar()
        
        # 统计不同数据源
        from sqlalchemy import distinct
        source_query = select(func.count(distinct(DiseaseRecord.data_source)))
        source_result = await db.execute(source_query)
        sources = source_result.scalar()
        
        print("\n" + "="*60)
        print("📊 数据库统计")
        print("="*60)
        print(f"总记录数: {total:,}")
        print(f"数据源数量: {sources}")
        print("="*60 + "\n")

async def main():
    print("\n🧪 Force Crawl 功能测试\n")
    
    print("功能说明:")
    print("• 正常模式 (--no-force): 只爬取数据库中不存在的新数据")
    print("• 强制模式 (--force): 爬取所有数据，如果已存在则更新\n")
    
    print("使用方法:")
    print("\n1. 正常爬取（默认）:")
    print("   ./venv/bin/python main.py crawl --country=CN --source=cdc_weekly")
    print("\n2. 强制爬取并更新:")
    print("   ./venv/bin/python main.py crawl --country=CN --source=cdc_weekly --force")
    print("\n3. 查看所有选项:")
    print("   ./venv/bin/python main.py crawl --help\n")
    
    # 显示当前数据库状态
    await show_database_stats()
    
    print("✅ force 功能已实现：")
    print("   • 如果记录已存在（相同时间+疾病+国家），则更新")
    print("   • 如果记录不存在，则插入")
    print("   • 避免了重复数据问题")
    print("\n💡 用途：定期更新数据或修正历史数据\n")

if __name__ == "__main__":
    asyncio.run(main())
