import asyncio
import sys
import os

# Add root directory to path
sys.path.append(os.getcwd())

from src.core.database import get_db
from sqlalchemy import text

async def main():
    try:
        async with get_db() as db:
            await db.execute(text("""
                INSERT INTO countries (
                    code, name, name_en, language, timezone, 
                    crawler_config, parser_config, disease_mapping_rules, report_config, 
                    is_active, metadata, created_at, updated_at
                ) VALUES (
                    'CN', '中国', 'China', 'zh', 'Asia/Shanghai', 
                    '{}', '{}', '{}', '{}', 
                    true, '{}', NOW(), NOW()
                ) ON CONFLICT(code) DO NOTHING
            """))
            await db.commit()
            print("Inserted/Verified Country CN")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
