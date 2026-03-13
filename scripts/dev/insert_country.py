import asyncio
import os
import sys

from sqlalchemy import text

# Allow running as a script from anywhere.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from src.core.database import get_db


async def main():
    async with get_db() as db:
        await db.execute(
            text(
                """
                INSERT INTO countries (
                    code, name, name_en, language, timezone,
                    crawler_config, parser_config, disease_mapping_rules, report_config,
                    is_active, metadata, created_at, updated_at
                ) VALUES (
                    'CN', '中国', 'China', 'zh', 'Asia/Shanghai',
                    '{}', '{}', '{}', '{}',
                    true, '{}', NOW(), NOW()
                ) ON CONFLICT(code) DO NOTHING
                """
            )
        )
        await db.commit()
        print("Inserted/Verified Country CN")


if __name__ == "__main__":
    asyncio.run(main())
