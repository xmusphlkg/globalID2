import os
import sys
import importlib.util

from dotenv import load_dotenv
import streamlit as st


_workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

load_dotenv()

st.set_page_config(page_title="GlobalID Task Monitor", page_icon="🛰️", layout="wide")

if "lang" not in st.session_state:
    st.session_state["lang"] = "en"

try:
    if "lang" in st.query_params:
        st.session_state["lang"] = st.query_params["lang"]
except Exception:
    pass

_i18n_path = os.path.join(os.path.dirname(__file__), "i18n.py")
spec = importlib.util.spec_from_file_location("dashboard_i18n", _i18n_path)
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)
t = i18n.t


def _load_css() -> None:
    css_path = os.path.join(os.path.dirname(__file__), "styles.css")
    try:
        with open(css_path, "r", encoding="utf-8") as handle:
            st.markdown(f"<style>{handle.read()}</style>", unsafe_allow_html=True)
    except Exception:
        pass


_load_css()

from src.dashboard.task import render_task_monitor


render_task_monitor(t, standalone=True)