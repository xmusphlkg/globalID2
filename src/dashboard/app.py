import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

# Page Config
st.set_page_config(
    page_title="GlobalID Dashboard",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Load Config
load_dotenv()

# Database Connection
@st.cache_resource
def get_connection():
    # Construct DB URL from env or use default (matching your project config)
    # Defaulting to the one used in migration: postgresql+asyncpg is for async,
    # Streamlit works better with sync drivers usually, or we adapt.
    # We will use psycopg2 or standard driver if available, or pandas read_sql
    # Since we installed asyncpg, we might need a sync driver or create engine with asyncpg and run sync
    # Let's try standard URL.
    default_url = "postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid"
    db_url = os.getenv("DATABASE_URL", default_url)
    return db_url

# Helper to run async query in sync streamlit
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

async def get_data_async(query):
    default_url = "postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid"
    db_url = os.getenv("DATABASE_URL", default_url)
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(query))
            df = pd.DataFrame(result.fetchall(), columns=result.keys())
    finally:
        await engine.dispose()
    return df

def run_query(query):
    return asyncio.run(get_data_async(query))

# --- SIDEBAR ---
st.sidebar.title("🌍 GlobalID Monitor")
st.sidebar.markdown("---")
page = st.sidebar.radio("Navigation", ["Overview", "Disease Analysis", "Data Explorer"])

st.sidebar.header("Global Selection")
# Country Selector
try:
    c_df = run_query("SELECT id, name FROM countries ORDER BY name")
    if not c_df.empty:
        c_map = dict(zip(c_df['name'], c_df['id']))
        def_idx = list(c_map.keys()).index('China') if 'China' in c_map else 0
        sel_country = st.sidebar.selectbox("Country", list(c_map.keys()), index=def_idx)
        sel_country_id = c_map[sel_country]
    else:
        sel_country = "Unknown" 
        sel_country_id = None
except Exception:
    sel_country = "System"
    sel_country_id = None
    st.sidebar.error("Country table check failed")

# --- PAGE: OVERVIEW ---
if page == "Overview":
    st.title(f"🛡️ Infectious Disease Monitor - {sel_country}")
    
    # KPIs
    col1, col2, col3, col4 = st.columns(4)
    
    # Fetch Basic Stats
    try:
        if sel_country_id:
            # Using .iloc[0, 0] to access the scalar value safely from DataFrame
            total_diseases = run_query(f"SELECT COUNT(DISTINCT disease_id) FROM disease_records WHERE country_id = {sel_country_id}").iloc[0, 0]
            total_records = run_query(f"SELECT COUNT(*) FROM disease_records WHERE country_id = {sel_country_id}").iloc[0, 0]
            latest_df = run_query(f"SELECT MAX(time) FROM disease_records WHERE country_id = {sel_country_id}")
            latest_date = latest_df.iloc[0, 0] if not latest_df.empty else None
            
            # New Cases Last Month (approx)
            if latest_date:
                latest_str = latest_date.strftime('%Y-%m-%d')
                prev_month = (latest_date - pd.DateOffset(months=1)).strftime('%Y-%m-%d')
                # Only use 'Total' row for aggregate counts
                cases_sql = f"""
                    SELECT SUM(r.cases) 
                    FROM disease_records r 
                    JOIN diseases d ON r.disease_id = d.id 
                    WHERE r.time > '{prev_month}' 
                    AND d.name = 'Total'
                    AND r.country_id = {sel_country_id}
                """
                recent_cases = run_query(cases_sql).iloc[0, 0] or 0
            else:
                recent_cases = 0
        else:
            total_diseases = 0
            total_records = 0
            latest_date = None
            recent_cases = 0

        col1.metric("Monitored Diseases", total_diseases)
        col2.metric("Total Records", f"{total_records:,}")
        col3.metric("Last Update", str(latest_date.date()) if latest_date else "N/A")
        col4.metric("Recent Cases (30d)", f"{int(recent_cases):,}")

    except Exception as e:
        st.error(f"Database Connection Error: {e}")
        st.stop()

    # Trend Chart (Total Only)
    st.subheader(f"📈 Monthly Case Trend ({sel_country} Total)")
    
    if sel_country_id:
        trend_sql = f"""
            SELECT d.name, r.time, r.cases 
            FROM disease_records r 
            JOIN diseases d ON r.disease_id = d.id 
            WHERE d.name = 'Total' 
            AND r.country_id = {sel_country_id}
            ORDER BY r.time ASC
        """
        trend_df = run_query(trend_sql)
    else:
        trend_df = pd.DataFrame()
    
    if not trend_df.empty:
        fig = px.line(trend_df, x='time', y='cases', title=f'Epidemic Curve ({sel_country} Total)', markers=True)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"No 'Total' data found for {sel_country}.")

# --- PAGE: DISEASE ANALYSIS ---
elif page == "Disease Analysis":
    st.title(f"🔬 Deep Dive Analysis - {sel_country}")

    if not sel_country_id:
        st.error("Please select a country first.")
        st.stop()
    
    # Disease Selector
    d_list = run_query("SELECT name FROM diseases ORDER BY name")
    
    if not d_list.empty:
        selected_disease = st.selectbox("Select Disease", d_list['name'])
        
        # Fetch Data
        hist_sql = f"""
            SELECT time, cases, deaths, incidence_rate, mortality_rate 
            FROM disease_records r 
            JOIN diseases d ON r.disease_id = d.id 
            WHERE d.name = '{selected_disease}' 
            AND r.country_id = {sel_country_id}
            ORDER BY time
        """
        df = run_query(hist_sql)
        
        if not df.empty:
            # Metrics
            total = df['cases'].sum()
            deaths = df['deaths'].sum()
            rate = (deaths/total*100) if total else 0
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Cumulative Cases", f"{int(total):,}")
            m2.metric("Cumulative Deaths", f"{int(deaths):,}")
            m3.metric("Case Fatality Rate (CFR)", f"{rate:.2f}%")
            
            # Dual Axis Plot
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df['time'], y=df['cases'], name='Cases', marker_color='#636EFA'))
            fig.add_trace(go.Scatter(x=df['time'], y=df['incidence_rate'], name='Incidence Rate', yaxis='y2', line=dict(color='#EF553B')))
            
            fig.update_layout(
                title=f"{selected_disease} Trends",
                yaxis=dict(title="Cases"),
                yaxis2=dict(title="Incidence Rate", overlaying='y', side='right'),
                xaxis=dict(title="Date"),
                hovermode="x unified"
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Data Table
            with st.expander("View Raw Data"):
                st.dataframe(df)
        else:
            st.warning("No records found for this disease.")

# --- PAGE: DATA EXPLORER ---
elif page == "Data Explorer":
    st.title("💾 Data Database Explorer")
    
    table = st.selectbox("Select Table", ["disease_records", "diseases", "countries"])
    limit = st.slider("Rows limit", 100, 5000, 1000)
    
    if st.button("Load Data"):
        query = f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT {limit}"
        df = run_query(query)
        st.write(f"Showing top {len(df)} rows from `{table}`")
        st.dataframe(df)

st.sidebar.markdown("---")
st.sidebar.info("GlobalID V2 Dashboard\nPowered by Streamlit & TimescaleDB")
