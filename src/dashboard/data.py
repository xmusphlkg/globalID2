import os
import pandas as pd
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def get_db_url():
    """Return the database URL used by the dashboard.

    This looks up `DATABASE_URL` in the environment and falls back to a
    reasonable default for local development.
    """
    default = "postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid"
    return os.getenv("DATABASE_URL", default)


async def _fetch(query: str):
    """Asynchronously execute a SQL query and return a pandas DataFrame.

    This helper is used by the cached synchronous wrapper below.
    """
    engine = create_async_engine(get_db_url())
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(query))
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
    finally:
        await engine.dispose()
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _cached_run(query: str):
    """Run the async query in a synchronous context and cache results.

    The caching reduces load on the database for frequently used queries.
    """
    return asyncio.run(_fetch(query))


def run_query(query: str) -> pd.DataFrame:
    """Public helper to execute SQL and return a DataFrame.

    This wraps `_cached_run` and returns an empty DataFrame on error,
    writing an error message to Streamlit so callers can surface it.
    """
    try:
        return _cached_run(query)
    except Exception as e:
        st.error(f"Database Error: {e}")
        return pd.DataFrame()


def get_disease_list(country_id: int):
    """Return disease display names and a mapping for a country.

    Returns a tuple `(display_list, name_to_code)` where `display_list` is
    a list of names suitable for `selectbox` and `name_to_code` maps the
    display name back to the internal disease code.
    """
    df = run_query(f"""
        SELECT DISTINCT d.name as code, d.name_en, sd.standard_name_zh
        FROM diseases d
        JOIN disease_records r ON d.id = r.disease_id
        LEFT JOIN standard_diseases sd ON d.name = sd.disease_id
        WHERE r.country_id = {country_id}
          AND d.name NOT IN ('D999')
        ORDER BY d.name
    """)
    if df.empty:
        return [], {}

    lang = st.session_state.get("lang", "en")
    if lang == "zh":
        df['display_name'] = df['standard_name_zh'].fillna(df['name_en'])
    else:
        df['display_name'] = df['name_en']

    name_to_code = dict(zip(df['display_name'], df['code']))
    return df['display_name'].tolist(), name_to_code
