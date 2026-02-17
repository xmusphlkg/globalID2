"""Common data utilities for dashboard."""
import os
import pandas as pd
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def get_db_url():
    """Return the database URL used by the dashboard."""
    default = "postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid"
    return os.getenv("DATABASE_URL", default)


async def _fetch(query: str):
    """Asynchronously execute a SQL query and return a pandas DataFrame."""
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
    """Run the async query in a synchronous context and cache results."""
    return asyncio.run(_fetch(query))


def run_query(query: str) -> pd.DataFrame:
    """Public helper to execute SQL and return a DataFrame."""
    try:
        return _cached_run(query)
    except Exception as e:
        st.error(f"Database Error: {e}")
        return pd.DataFrame()
