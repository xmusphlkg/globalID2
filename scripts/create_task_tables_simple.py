"""
创建任务管理表的脚本

直接运行此脚本来创建新的任务管理表
"""
import asyncio
from src.core import init_app
from src.core.database import get_engine
from src.domain import Base
from src.core.logging import get_logger

logger = get_logger(__name__)


async def create_task_tables():
    """创建任务管理表"""
    await init_app()
    
    logger.info("Creating task management tables...")
    
    engine = get_engine()
    
    # 创建所有表（包括新的任务管理表）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Task management tables created successfully!")
    
    # 验证表是否创建成功
    from sqlalchemy import text
    async with engine.begin() as conn:
        result = await conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('tasks', 'task_workbook', 'task_dependencies')
            ORDER BY table_name
        """))
        tables = [row[0] for row in result]
        logger.info(f"Created tables: {', '.join(tables)}")


if __name__ == "__main__":
    asyncio.run(create_task_tables())
