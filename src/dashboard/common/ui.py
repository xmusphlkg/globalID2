"""Shared UI components for the dashboard."""
from typing import Tuple, Optional
import streamlit as st
import pandas as pd


def render_sidebar(t, country_list: list, c_df: pd.DataFrame, country_error: Optional[str]):
    """Render the application sidebar and return selection values.

    Parameters:
        t: translation function (i18n.t)
        country_list: list of country names for the selectbox
        c_df: DataFrame of countries with at least columns `id` and `name`
        country_error: optional error message if fetching countries failed

    Returns:
        tuple: (page, nav_labels, sel_country, sel_country_id)
    """
    with st.sidebar:
        st.markdown(
            f"<div class=\"brand\">🌍 <span class=\"title\">{t('app_title')}</span><div class=\"subtitle\">{t('platform_desc')}</div></div>",
            unsafe_allow_html=True,
        )
        st.markdown("---")

        nav_labels = t("nav")
        page = st.radio(t("nav_heading"), nav_labels, key="nav_radio")
        st.markdown("---")

        with st.expander(t("filter_heading"), expanded=True):
            if country_list:
                default_index = 0
                prev = st.session_state.get("sel_country")
                if prev and prev in country_list:
                    default_index = country_list.index(prev)
                sel = st.selectbox(t("select_country"), country_list, index=default_index, key="country_select")
                st.session_state["sel_country"] = sel
                sel_country_id = int(c_df.loc[c_df["name"] == sel, "id"].iloc[0])
                sel_country = sel
            else:
                sel_country = None
                sel_country_id = None
                if country_error:
                    st.warning(t("no_countries") + f": {country_error}")
                else:
                    st.warning(t("no_countries"))

        with st.expander(t("language"), expanded=False):
            lang_index = 1 if st.session_state.get("lang") == "zh" else 0
            choice = st.selectbox(t("language"), [t("english"), t("chinese")], index=lang_index, key="ui_lang_select")
            new_lang = "zh" if choice == t("chinese") else "en"
            if new_lang != st.session_state.get("lang"):
                st.session_state["lang"] = new_lang
                st.query_params["lang"] = new_lang
                st.rerun()

        if st.button("🔄 " + ("Refresh Data" if st.session_state.get("lang") == "en" else "刷新数据"), type="primary"):
            st.cache_data.clear()
            st.rerun()

        st.markdown("---")
        st.caption(f"{t('version_text')}: v2.0")

    return page, nav_labels, sel_country, sel_country_id
