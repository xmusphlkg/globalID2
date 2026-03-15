#!/usr/bin/env python3
"""清理无效的疾病建议"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.core.database import get_session_maker
from src.core.db_schema import ensure_disease_learning_suggestions_schema

async def main():
    SessionMaker = get_session_maker()
    async with SessionMaker() as db:
        await ensure_disease_learning_suggestions_schema(db)

        # 1. 删除空白建议
        result = await db.execute(text(
            "DELETE FROM disease_learning_suggestions "
            "WHERE country_code = 'CN' AND COALESCE(local_name, '') = ''"
        ))
        blank_count = result.rowcount
        
        # 2. 删除已有CN_EN映射的英文建议（清理所有country_code）
        result = await db.execute(text('''
            DELETE FROM disease_learning_suggestions 
            WHERE id IN (
                SELECT dls.id 
                FROM disease_learning_suggestions dls
                JOIN disease_mappings dm ON dls.local_name = dm.local_name
                WHERE dm.country_code = 'CN_EN'
                  AND dls.status = 'pending'
            )
        '''))
        en_count = result.rowcount
        
        await db.commit()
        
        print(f'✓ 删除空白建议: {blank_count} 条')
        print(f'✓ 删除已映射英文建议: {en_count} 条')
        print(f'✓ 总计删除: {blank_count + en_count} 条')
        
        # 查看剩余
        result = await db.execute(text(
            "SELECT COUNT(*) FROM disease_learning_suggestions "
            "WHERE country_code = 'CN' AND status = 'pending'"
        ))
        remaining = result.scalar()
        print(f'\n📊 剩余待审核: {remaining} 条')

if __name__ == '__main__':
    asyncio.run(main())
