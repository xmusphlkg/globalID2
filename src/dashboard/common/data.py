"""Common data utilities for dashboard."""
import os
import pandas as pd
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def get_db_url():
    """Return the database URL used by the dashboard."""
    default = "postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid"
    return os.getenv("DATABASE_URL", default)


def _get_sync_db_url() -> str:
    """Convert the app database URL to a sync driver for Streamlit queries."""
    url = make_url(get_db_url())
    if url.drivername == "postgresql+asyncpg":
        url = url.set(drivername="postgresql+psycopg2")
    return url.render_as_string(hide_password=False)


@st.cache_resource(show_spinner=False)
def _get_engine():
    """Reuse one SQLAlchemy engine across Streamlit reruns."""
    return create_engine(
        _get_sync_db_url(),
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def _run_sync(query: str) -> pd.DataFrame:
    """Synchronously execute a SQL query and return a pandas DataFrame."""
    engine = _get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return pd.DataFrame(result.fetchall(), columns=result.keys())


@st.cache_data(ttl=60, show_spinner=False)
def _cached_run(query: str):
    """Run a query and cache the resulting DataFrame."""
    return _run_sync(query)


def run_query(query: str, use_cache: bool = True) -> pd.DataFrame:
    """Public helper to execute SQL and return a DataFrame."""
    try:
        if use_cache:
            return _cached_run(query)
        return _run_sync(query)
    except Exception as e:
        st.error(f"Database Error: {e}")
        return pd.DataFrame()
