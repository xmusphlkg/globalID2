import asyncio
from sqlalchemy import select, func
from database import engine
from models import DiseaseRecord

async def test():
    async with engine.connect() as conn:
        q = select(
            (func.extract('year', func.age(func.max(DiseaseRecord.time), func.min(DiseaseRecord.time))) * 12 + getattr(func.extract('month', func.age(func.max(DiseaseRecord.time), func.min(DiseaseRecord.time))), 'label')('months')).label('total_months')
        )
        print(q)

