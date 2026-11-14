"""Read-only source snapshot and metrics for isolated literature benchmarks."""
from __future__ import annotations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.config import get_config
from src.domain import LiteratureArticle
from src.domain.ai_model_center import AIProviderConfig, AIModelConfig
from src.services.ai_content_governance_service import AIContentGovernanceService

def percentile(values, percent):
    ordered = sorted(values)
    return (
        round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percent))], 3)
        if ordered
        else None
    )

async def source_snapshot(limit):
    source = create_async_engine(
        get_config().database.url,
        hide_parameters=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    policy = AIContentGovernanceService()
    try:
        async with async_sessionmaker(source, expire_on_commit=False)() as db:
            providers = (
                (await db.execute(select(AIProviderConfig.__table__))).mappings().all()
            )
            models = (
                (await db.execute(select(AIModelConfig.__table__))).mappings().all()
            )
            articles, cursor = [], 0
            while len(articles) < limit:
                rows = (
                    await db.scalars(
                        policy._article_review_query()
                        .order_by(None)
                        .where(LiteratureArticle.id > cursor)
                        .order_by(LiteratureArticle.id)
                        .limit(min(512, limit - len(articles)))
                    )
                ).all()
                if not rows:
                    break
                cursor = rows[-1].id
                articles.extend(
                    row
                    for row in rows
                    if not policy._held(row.metadata_, policy._article_fingerprint(row))
                )
            records = [
                {
                    column.name: getattr(
                        row, "metadata_" if column.name == "metadata" else column.name
                    )
                    for column in LiteratureArticle.__table__.columns
                }
                for row in articles
            ]
            occupancy = dict(
                (
                    await db.execute(
                        text(
                            "SELECT route_key, count(*) FROM ai_work_items WHERE status='leased' "
                            "AND capability='text' AND lease_expires_at > now() GROUP BY route_key"
                        )
                    )
                ).all()
            )
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
            return (
                list(map(dict, providers)),
                list(map(dict, models)),
                records,
                occupancy,
                revision,
            )
    finally:
        await source.dispose()
