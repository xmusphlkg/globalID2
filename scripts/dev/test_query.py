import asyncio
import os
import sys

from sqlalchemy import func, select

# Allow running as a script from anywhere.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from src.core import get_database, init_app
from src.domain import DiseaseRecord


async def main():
    await init_app()
    async with get_database() as db:
        latest_query = select(func.max(DiseaseRecord.time)).where(DiseaseRecord.country_id == 1)
        latest_result = await db.execute(latest_query)
        latest_date = latest_result.scalar()
        print(f"latest_date: {repr(latest_date)}")


if __name__ == "__main__":
    asyncio.run(main())
