"""Apply mapping counters and learning suggestions inside the report commit."""
from sqlalchemy import text


async def persist_mapping_effects(db, effects):
    for effect in effects:
        parameters = {"country": effect["country"], "name": effect["name"]}
        if effect["kind"] == "usage":
            await db.execute(text("""
                UPDATE disease_mappings
                SET usage_count = usage_count + 1, last_used_at = CURRENT_TIMESTAMP
                WHERE country_code = :country AND local_name = :name
            """), parameters)
        elif effect["kind"] == "unknown":
            await db.execute(text("""
                INSERT INTO disease_learning_suggestions
                    (country_code, local_name, occurrence_count, first_seen_at, last_seen_at)
                VALUES (:country, :name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (country_code, local_name) DO UPDATE SET
                    occurrence_count = disease_learning_suggestions.occurrence_count + 1,
                    last_seen_at = CURRENT_TIMESTAMP
            """), parameters)
        else:
            raise ValueError(f"Unknown mapping effect: {effect['kind']}")
