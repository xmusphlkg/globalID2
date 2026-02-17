#!/usr/bin/env python3
"""Clear all task-related data for testing"""
import asyncio
import sys
from pathlib import Path

# Add project root to Python path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.core.database import get_session_maker

async def main():
    SessionMaker = get_session_maker()
    async with SessionMaker() as db:
        # Clear task-related tables
        print("🗑️  Clearing task data...")
        
        # 1. Delete workbook entries
        result = await db.execute(text("DELETE FROM task_workbook"))
        workbook_count = result.rowcount
        
        # 2. Delete tasks
        result = await db.execute(text("DELETE FROM tasks"))
        task_count = result.rowcount
        
        # 3. Delete crawl runs
        result = await db.execute(text("DELETE FROM crawl_runs"))
        crawl_count = result.rowcount
        
        await db.commit()
        
        print(f'✓ Deleted {workbook_count} workbook entries')
        print(f'✓ Deleted {task_count} tasks')
        print(f'✓ Deleted {crawl_count} crawl runs')
        print(f'\n✨ All task data cleared!')

if __name__ == '__main__':
    asyncio.run(main())
