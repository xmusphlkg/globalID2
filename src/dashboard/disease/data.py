"""Disease data queries and utilities."""
import streamlit as st
from src.dashboard.common.data import run_query


def get_disease_list(country_id: int):
    """Return disease display names and a mapping for a country.

    Returns:
        tuple: (display_list, name_to_code) where `display_list` is a list of names
               suitable for `selectbox` and `name_to_code` maps the display name
               back to the internal disease code.
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
