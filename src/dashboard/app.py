import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import text
import os
import sys
from dotenv import load_dotenv
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
import importlib.util

# Add workspace root to sys.path for proper module imports
_workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

load_dotenv()

# Page config
if "lang" not in st.session_state:
    st.session_state["lang"] = "en"

st.set_page_config(page_title="GlobalID Data Dashboard", page_icon="🌍", layout="wide")

# If language is provided in query params, respect it
try:
    if "lang" in st.query_params:
        st.session_state["lang"] = st.query_params["lang"]
except Exception:
    pass

# Load i18n module from local file for stable imports (works when running via Streamlit)
_i18n_path = os.path.join(os.path.dirname(__file__), "i18n.py")
spec = importlib.util.spec_from_file_location("dashboard_i18n", _i18n_path)
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)
t = i18n.t


def _load_css():
    css_path = os.path.join(os.path.dirname(__file__), "styles.css")
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except Exception:
        pass


_load_css()



from src.dashboard.data import run_query, get_disease_list
from src.dashboard.plots import plot_top_diseases, plot_trend_chart
from src.dashboard.ui import render_sidebar

# Fetch country list (used in sidebar filter) and build redesigned sidebar
try:
    c_df = run_query("SELECT id, name FROM countries ORDER BY name")
    country_list = list(c_df["name"]) if not c_df.empty else []
    country_error = None
except Exception as e:
    c_df = pd.DataFrame()
    country_list = []
    country_error = str(e)

# Render the sidebar using the UI helper; this returns the selected
# page/navigation labels and the selected country (name + id).
page, nav_labels, sel_country, sel_country_id = render_sidebar(t, country_list, c_df, country_error)

# Pages
if page == nav_labels[0]:
    # Overview
    st.title(t("overview_title"))
    st.write(f"**{t('current_country')}:** {sel_country or '—'}")

    col1, col2, col3, col4 = st.columns(4)
    if sel_country_id:
        try:
            total_diseases = int(run_query(f"SELECT COUNT(DISTINCT disease_id) FROM disease_records WHERE country_id = {sel_country_id}").iloc[0,0] or 0)
            total_records = int(run_query(f"SELECT COUNT(*) FROM disease_records WHERE country_id = {sel_country_id}").iloc[0,0] or 0)
            latest = run_query(f"SELECT MAX(time) FROM disease_records WHERE country_id = {sel_country_id}")
            latest_date = latest.iloc[0,0] if not latest.empty else None
            # 修复：使用所有疾病的总和，而不是查询不存在的'Total'疾病
            recent_cases = int(run_query(f"SELECT COALESCE(SUM(cases),0) FROM disease_records WHERE time > NOW()-INTERVAL '30 days' AND country_id={sel_country_id}").iloc[0,0] or 0)
        except Exception as e:
            st.error(t("connection_failed") + f": {e}")
            total_diseases = total_records = recent_cases = 0
            latest_date = None
    else:
        total_diseases = total_records = recent_cases = 0
        latest_date = None

    col1.metric(t("kpi_monitored"), f"{total_diseases}")
    col2.metric(t("kpi_total"), f"{total_records:,}")
    # 只显示日期，不显示时间
    latest_date_str = pd.to_datetime(latest_date).strftime('%Y-%m-%d') if latest_date is not None else "N/A"
    col3.metric(t("kpi_last"), latest_date_str)
    col4.metric(t("kpi_recent"), f"{recent_cases:,}")

    # Top疾病排名
    st.subheader(f"{t('top_diseases_title')} ({t('interval_1y')})")
    if sel_country_id:
        lang = st.session_state.get("lang", "en")
        if lang == "zh":
            top_sql = f"""
                SELECT COALESCE(sd.standard_name_zh, d.name_en, d.name) as name, 
                       SUM(r.cases) as total_cases, 
                       SUM(r.deaths) as total_deaths
                FROM disease_records r
                JOIN diseases d ON r.disease_id = d.id
                LEFT JOIN standard_diseases sd ON d.name = sd.disease_id
                WHERE r.country_id = {sel_country_id}
                AND r.time > NOW() - INTERVAL '365 days'
                GROUP BY sd.standard_name_zh, d.name_en, d.name
                ORDER BY total_cases DESC
                LIMIT 10
            """
        else:
            top_sql = f"""
                SELECT d.name_en as name, 
                       SUM(r.cases) as total_cases, 
                       SUM(r.deaths) as total_deaths
                FROM disease_records r
                JOIN diseases d ON r.disease_id = d.id
                WHERE r.country_id = {sel_country_id}
                AND r.time > NOW() - INTERVAL '365 days'
                GROUP BY d.name_en
                ORDER BY total_cases DESC
                LIMIT 10
            """
        top_df = run_query(top_sql)
        if not top_df.empty:
            # Delegate rendering to plotting helpers to keep app.py small.
            plot_top_diseases(top_df, t)

    st.subheader(t("trend_title"))
    if sel_country_id:
        # 选择时间范围和疾病
        col_interval, col_disease = st.columns([1, 2])
        with col_interval:
            interval_options = [
                (t("interval_30d"), "30"),
                (t("interval_90d"), "90"),
                (t("interval_1y"), "365"),
                (t("interval_all"), "all"),
                (t("custom_range"), "custom"),
            ]
            labels = [opt[0] for opt in interval_options]
            choice = st.selectbox(t("interval_label"), labels, index=3)
            sel_interval = dict(interval_options)[choice]
            
            custom_dates = None
            if sel_interval == "custom":
                custom_dates = st.date_input(t("date_range"), [])
        
        with col_disease:
            # 获取疾病列表供用户选择
            disease_names, disease_map = get_disease_list(sel_country_id)
            if disease_names:
                disease_options = [t("all_diseases")] + disease_names
                selected_disease_display = st.selectbox(t("disease_filter"), disease_options)
                # 转换显示名称为代码
                if selected_disease_display == t("all_diseases"):
                    selected_disease = None
                else:
                    selected_disease = disease_map.get(selected_disease_display)
            else:
                selected_disease_display = t("all_diseases")
                selected_disease = None

        if sel_interval == "all":
            time_filter = ""
        elif sel_interval == "custom":
            if custom_dates and len(custom_dates) == 2:
                time_filter = f"AND r.time >= '{custom_dates[0]}' AND r.time <= '{custom_dates[1]}'"
            else:
                # Default fallback if date not fully picked yet
                time_filter = "AND r.time > NOW() - INTERVAL '30 days'"
        else:
            time_filter = f"AND r.time > NOW() - INTERVAL '{sel_interval} days'"
        
        # 根据选择的疾病构建查询
        if selected_disease is None:
            # 使用Total(D999)疾病数据，而不是求和所有疾病
            disease_filter = "AND d.name = 'D999'"
            group_by = "date_trunc('month', r.time)"
        else:
            disease_filter = f"AND d.name = '{selected_disease}'"
            group_by = "date_trunc('month', r.time)"

        trend_sql = f"""
            SELECT {group_by} AS time_period, 
                   SUM(r.cases) AS cases,
                   SUM(r.deaths) AS deaths
            FROM disease_records r
            JOIN diseases d ON r.disease_id = d.id
            WHERE r.country_id = {sel_country_id} {time_filter} {disease_filter}
            GROUP BY time_period
            ORDER BY time_period
        """
        trend_df = run_query(trend_sql)
        if not trend_df.empty:
            # Prepare a display copy (formatted date) and delegate rendering.
            trend_df_display = trend_df.copy()
            trend_df_display['time_period'] = pd.to_datetime(trend_df['time_period']).dt.strftime('%Y-%m-%d')
            plot_trend_chart(trend_df, t, trend_df_display)
        else:
            st.info(t('query_no_data'))
    else:
        st.info(t("select_country_prompt"))

elif page == nav_labels[1]:
    st.title(t("nav")[1])
    if not sel_country_id:
        st.warning(t("select_country"))
    else:
        # 获取有数据的疾病列表
        disease_names, disease_map = get_disease_list(sel_country_id)
        if disease_names:
            # 显示疾病数量
            st.info(f"{t('available_diseases')}: {len(disease_names)}")
            
            # 疾病选择和对比
            col1, col2 = st.columns([3, 1])
            with col1:
                disease_display = st.selectbox(t("disease_label"), disease_names)
                disease = disease_map.get(disease_display)
            with col2:
                compare_mode = st.checkbox(t("compare_diseases"))
            
            if compare_mode:
                # 疾病对比模式
                st.subheader(t("disease_comparison"))
                diseases_to_compare_display = st.multiselect(t("select_diseases_to_compare"),
                                                    disease_names,
                                                    default=[disease_display])
                diseases_to_compare = [disease_map.get(d) for d in diseases_to_compare_display]
                if len(diseases_to_compare) >= 1:
                    comparison_dfs = []
                    for i, d_code in enumerate(diseases_to_compare):
                        d_display = diseases_to_compare_display[i]
                        df = run_query(f"""
                            SELECT date_trunc('month', time) as month, 
                                   SUM(cases) as cases, 
                                   SUM(deaths) as deaths,
                                   '{d_display}' as disease
                            FROM disease_records r
                            JOIN diseases d ON r.disease_id=d.id
                            WHERE d.name = '{d_code}' AND r.country_id = {sel_country_id}
                            GROUP BY month
                            ORDER BY month
                        """)
                        comparison_dfs.append(df)
                    
                    combined_df = pd.concat(comparison_dfs, ignore_index=True)
                    if not combined_df.empty:
                        fig = px.line(combined_df, x='month', y='cases', color='disease',
                                    labels={'month': t('time'), 'cases': t('cases')},
                                    template='plotly_white')
                        fig.update_layout(height=400)
                        st.plotly_chart(fig, width='stretch')
                        
                        # 对比统计
                        st.subheader(t("comparison_stats"))
                        stats_data = []
                        for d_name in diseases_to_compare:
                            d_df = combined_df[combined_df['disease'] == d_name]
                            total_cases = int(d_df['cases'].sum())
                            total_deaths = int(d_df['deaths'].sum())
                            cfr = (total_deaths/total_cases*100) if total_cases else 0
                            stats_data.append({
                                t('disease_label'): d_name,
                                t('total_cases'): f"{total_cases:,}",
                                t('total_deaths'): f"{total_deaths:,}",
                                'CFR': f"{cfr:.2f}%"
                            })
                        st.dataframe(pd.DataFrame(stats_data), width='stretch')
            else:
                # 单个疾病分析
                df = run_query(f"""
                    SELECT time, cases, deaths, incidence_rate, mortality_rate
                    FROM disease_records r
                    JOIN diseases d ON r.disease_id=d.id
                    WHERE d.name = '{disease}' AND r.country_id = {sel_country_id}
                    ORDER BY time
                """)
                if not df.empty:
                    total = int(df['cases'].sum())
                    deaths = int(df['deaths'].sum())
                    cfr = (deaths/total*100) if total else 0
                    avg_monthly = int(df['cases'].mean())
                    
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric(t("total_cases"), f"{total:,}")
                    m2.metric(t("total_deaths"), f"{deaths:,}")
                    m3.metric("CFR", f"{cfr:.2f}%")
                    m4.metric(t("avg_monthly"), f"{avg_monthly:,}")
                    
                    # 图表选项卡
                    tab1, tab2, tab3 = st.tabs([t("cases_trend"), t("deaths_trend"), t("rates")])
                    
                    with tab1:
                        fig = px.area(df, x='time', y='cases', template='plotly_white')
                        fig.update_layout(height=350)
                        st.plotly_chart(fig, width='stretch')
                    
                    with tab2:
                        fig = px.bar(df, x='time', y='deaths', template='plotly_white')
                        fig.update_layout(height=350)
                        st.plotly_chart(fig, width='stretch')
                    
                    with tab3:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=df['time'], y=df['incidence_rate'], 
                                               name=t('incidence_rate'), mode='lines'))
                        fig.add_trace(go.Scatter(x=df['time'], y=df['mortality_rate'], 
                                               name=t('mortality_rate'), mode='lines'))
                        fig.update_layout(template='plotly_white', height=350)
                        st.plotly_chart(fig, width='stretch')
                    
                    with st.expander(t("raw_data"), expanded=False):
                        st.dataframe(df, width='stretch')
                        csv = df.to_csv(index=False)
                        st.download_button(t("download_csv"), data=csv, 
                                         file_name=f'{disease}_data.csv')
                else:
                    st.info(t('query_no_data'))
        else:
            st.info(t('query_no_data'))

elif page == nav_labels[2]:
    st.title(t('data_browser_title'))
    
    # 快捷查询模板
    st.subheader(t("quick_queries"))
    query_templates = {
        t("template_recent_data"): f"SELECT d.name, r.time, r.cases, r.deaths FROM disease_records r JOIN diseases d ON r.disease_id=d.id WHERE r.country_id={sel_country_id or 1} ORDER BY r.time DESC LIMIT 100",
        t("template_disease_summary"): f"SELECT d.name, COUNT(*) as records, SUM(r.cases) as total_cases, SUM(r.deaths) as total_deaths FROM disease_records r JOIN diseases d ON r.disease_id=d.id WHERE r.country_id={sel_country_id or 1} GROUP BY d.name ORDER BY total_cases DESC",
        t("template_disease_summary"): "time_completeness_check",  # 特殊标识符，表示这是疾病汇总统计
        t("template_monthly_stats"): f"SELECT date_trunc('month', time) as month, SUM(cases) as cases, SUM(deaths) as deaths FROM disease_records WHERE country_id={sel_country_id or 1} GROUP BY month ORDER BY month DESC LIMIT 24",
        t("template_data_quality"): f"SELECT COUNT(*) as total_records, COUNT(DISTINCT disease_id) as unique_diseases, MIN(time) as earliest, MAX(time) as latest, COUNT(CASE WHEN cases = 0 THEN 1 END) as zero_cases FROM disease_records WHERE country_id={sel_country_id or 1}"
    }
    
    selected_template = st.selectbox(t("select_template"), list(query_templates.keys()))
    
    if selected_template == t("template_disease_summary"):
        # 时间序列完整性检查 - 特殊处理
        st.subheader(t("time_completeness"))
        
        # 选择疾病
        disease_names, disease_map = get_disease_list(sel_country_id or 1)
        
        if disease_names:
            disease_options = [t("all_diseases")] + disease_names
            selected_disease_display = st.selectbox(t("disease_filter"), disease_options)
            if selected_disease_display == t("all_diseases"):
                selected_disease = None
            else:
                selected_disease = disease_map.get(selected_disease_display)
            
            col1, col2 = st.columns(2)
            with col1:
                start_date = st.date_input(t("start_date"), value=pd.to_datetime("2020-01-01").date())
            with col2:
                end_date = st.date_input(t("end_date"), value=pd.to_datetime("today").date())
            
            if st.button(t("run_template")):
                if start_date >= end_date:
                    st.error("开始日期必须早于结束日期")
                else:
                    # 根据选择的疾病构建查询
                    if selected_disease is None:
                        # 检查所有疾病的数据完整性
                        lang = st.session_state.get("lang", "en")
                        if lang == "zh":
                            completeness_query = f"""
                                SELECT 
                                    COALESCE(sd.standard_name_zh, d.name_en, d.name) as disease_name,
                                    COUNT(DISTINCT date_trunc('month', r.time)) as data_months,
                                    EXTRACT(EPOCH FROM (MAX(r.time) - MIN(r.time)))/2592000 as total_months_span,
                                    MIN(r.time) as earliest_date,
                                    MAX(r.time) as latest_date,
                                    COUNT(*) as total_records
                                FROM disease_records r
                                JOIN diseases d ON r.disease_id = d.id
                                LEFT JOIN standard_diseases sd ON d.name = sd.disease_id
                                WHERE r.country_id = {sel_country_id or 1}
                                AND r.time >= '{start_date}' AND r.time <= '{end_date}'
                                GROUP BY d.id, sd.standard_name_zh, d.name_en, d.name
                                ORDER BY disease_name
                            """
                        else:
                            completeness_query = f"""
                                SELECT 
                                    d.name_en as disease_name,
                                    COUNT(DISTINCT date_trunc('month', r.time)) as data_months,
                                    EXTRACT(EPOCH FROM (MAX(r.time) - MIN(r.time)))/2592000 as total_months_span,
                                    MIN(r.time) as earliest_date,
                                    MAX(r.time) as latest_date,
                                    COUNT(*) as total_records
                                FROM disease_records r
                                JOIN diseases d ON r.disease_id = d.id
                                WHERE r.country_id = {sel_country_id or 1}
                                AND r.time >= '{start_date}' AND r.time <= '{end_date}'
                                GROUP BY d.id, d.name_en
                                ORDER BY disease_name
                            """
                        completeness_data = run_query(completeness_query)
                        
                        if not completeness_data.empty:
                            # 计算每个疾病的完整性
                            results = []
                            for _, row in completeness_data.iterrows():
                                disease_name = row['disease_name']
                                data_months = int(row['data_months'])
                                total_months_span = float(row['total_months_span']) if row['total_months_span'] else 0
                                earliest_date = row['earliest_date']
                                latest_date = row['latest_date']
                                total_records = int(row['total_records'])
                                
                                # 计算预期月份数
                                expected_months = max(1, int(total_months_span) + 1) if total_months_span > 0 else 1
                                completeness_rate = (data_months / expected_months * 100) if expected_months > 0 else 100
                                
                                results.append({
                                    t('disease_label'): disease_name,
                                    t('total_periods'): expected_months,
                                    'Data Months': data_months,
                                    t('completeness_rate'): f"{completeness_rate:.1f}%",
                                    'Earliest Date': earliest_date.strftime('%Y-%m-%d') if earliest_date else 'N/A',
                                    'Latest Date': latest_date.strftime('%Y-%m-%d') if latest_date else 'N/A',
                                    t('total_records'): total_records
                                })
                            
                            results_df = pd.DataFrame(results)
                            st.dataframe(results_df, width='stretch')
                            
                            # 整体统计
                            avg_completeness = results_df[t('completeness_rate')].str.rstrip('%').astype(float).mean()
                            total_diseases = len(results_df)
                            complete_diseases = len(results_df[results_df[t('completeness_rate')].str.rstrip('%').astype(float) == 100.0])
                            
                            col1, col2, col3 = st.columns(3)
                            col1.metric("Total Diseases", f"{total_diseases}")
                            col2.metric("Complete Diseases", f"{complete_diseases}")
                            col3.metric("Avg Completeness", f"{avg_completeness:.1f}%")
                            
                            csv = results_df.to_csv(index=False)
                            st.download_button(t('download_csv'), data=csv, file_name='disease_completeness_analysis.csv')
                        else:
                            st.info("所选时间范围内没有数据")
                    
                    else:
                        # 检查单个疾病的时间序列完整性
                        # 获取该疾病的所有数据月份
                        months_query = f"""
                            SELECT DISTINCT date_trunc('month', r.time) as month
                            FROM disease_records r
                            JOIN diseases d ON r.disease_id = d.id
                            WHERE r.country_id = {sel_country_id or 1}
                            AND d.name = '{selected_disease}'
                            AND r.time >= '{start_date}' AND r.time <= '{end_date}'
                            ORDER BY month
                        """
                        months_data = run_query(months_query)
                        
                        if not months_data.empty:
                            existing_months = set(months_data['month'])
                            
                            # 生成连续的月份序列
                            min_month = months_data['month'].min()
                            max_month = months_data['month'].max()
                            
                            # 使用pandas生成连续月份
                            all_months = pd.date_range(start=min_month, end=max_month, freq='MS')
                            expected_months = set(all_months)
                            
                            # 找出缺失的月份
                            missing_months = expected_months - existing_months
                            
                            total_expected = len(expected_months)
                            total_missing = len(missing_months)
                            completeness_rate = ((total_expected - total_missing) / total_expected * 100) if total_expected > 0 else 100
                            
                            col1, col2, col3 = st.columns(3)
                            col1.metric(t("total_periods"), f"{total_expected}")
                            col2.metric(t("missing_periods"), f"{total_missing}")
                            col3.metric(t("completeness_rate"), f"{completeness_rate:.1f}%")
                            
                            if total_missing > 0:
                                st.warning(f"发现 {total_missing} 个缺失的月份")
                                st.subheader(t("missing_details"))
                                missing_df = pd.DataFrame({'Missing Month': list(missing_months)})
                                missing_df = missing_df.sort_values('Missing Month')
                                st.dataframe(missing_df, width='stretch')
                            else:
                                st.success("时间序列连续，没有缺失的月份")
                            
                            # 显示数据分布
                            st.subheader("数据分布详情")
                            monthly_data_query = f"""
                                SELECT 
                                    date_trunc('month', r.time) as month,
                                    COUNT(*) as records,
                                    SUM(r.cases) as total_cases,
                                    SUM(r.deaths) as total_deaths
                                FROM disease_records r
                                JOIN diseases d ON r.disease_id = d.id
                                WHERE r.country_id = {sel_country_id or 1}
                                AND d.name = '{selected_disease}'
                                AND r.time >= '{start_date}' AND r.time <= '{end_date}'
                                GROUP BY month
                                ORDER BY month
                            """
                            monthly_data = run_query(monthly_data_query)
                            if not monthly_data.empty:
                                # 格式化月份列，只显示日期
                                monthly_data = monthly_data.copy()
                                monthly_data['month'] = pd.to_datetime(monthly_data['month']).dt.strftime('%Y-%m-%d')
                                st.dataframe(monthly_data, width='stretch')
                                
                                # 可视化
                                fig = go.Figure()
                                fig.add_trace(go.Bar(
                                    x=monthly_data['month'], 
                                    y=monthly_data['records'],
                                    name='Records per Month',
                                    marker_color='lightblue'
                                ))
                                fig.update_layout(
                                    title=f"Data Distribution for {selected_disease}",
                                    xaxis_title=t("time"),
                                    yaxis_title="Records",
                                    height=300
                                )
                                st.plotly_chart(fig, width='stretch')
                                
                                csv = monthly_data.to_csv(index=False)
                                st.download_button(t('download_csv'), data=csv, file_name=f'{selected_disease}_monthly_data.csv')
                        else:
                            st.info(f"疾病 '{selected_disease}' 在所选时间范围内没有数据")
        else:
            st.info("没有找到有数据的疾病")
    
    elif st.button(t("run_template")):
        query = query_templates[selected_template]
        try:
            res = run_query(query)
            if not res.empty:
                st.success(f"{t('query_success')}: {len(res)} {t('rows')}")
                st.dataframe(res, width='stretch')
                csv = res.to_csv(index=False)
                st.download_button(t('download_csv'), data=csv, file_name='query_result.csv')
            else:
                st.info(t('query_no_data'))
        except Exception as e:
            st.error(f"{t('query_failed')}: {e}")
    
    st.markdown("---")
    
    # 表格浏览器
    st.subheader(t("table_browser"))
    col1, col2 = st.columns([2, 1])
    with col1:
        table = st.selectbox(t("table_label"), ["disease_records", "diseases", "countries", "standard_diseases", "disease_mappings"])
    with col2:
        limit = st.number_input(t("rows_label"), 10, 5000, 100, step=50)
    
    if st.button(t('load_table')):
        df = run_query(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT {limit}")
        st.write(f"📊 {len(df)} {t('rows')} from `{table}`")
        st.dataframe(df, width='stretch')

else:
    st.title(t('data_quality_title'))
    st.write(t('data_quality_desc'))
    
    if sel_country_id:
        # 数据质量指标
        col1, col2, col3 = st.columns(3)
        
        # 基本统计
        basic_stats = run_query(f"""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT disease_id) as unique_diseases,
                MIN(time) as earliest_date,
                MAX(time) as latest_date
            FROM disease_records
            WHERE country_id = {sel_country_id}
        """)
        
        if not basic_stats.empty:
            row = basic_stats.iloc[0]
            col1.metric(t("total_records"), f"{row['total_records']:,}")
            col2.metric(t("diseases_count"), f"{row['unique_diseases']}")
            # 只显示日期，不显示时间
            earliest = pd.to_datetime(row['earliest_date']).strftime('%Y-%m-%d')
            latest = pd.to_datetime(row['latest_date']).strftime('%Y-%m-%d')
            date_range = f"{earliest} to {latest}"
            col3.metric(t("date_range"), date_range)
        
        # 数据质量检查
        st.subheader(t("quality_checks"))
        
        # 1. 零值统计
        zero_stats = run_query(f"""
            SELECT 
                COUNT(CASE WHEN cases = 0 THEN 1 END) as zero_cases,
                COUNT(CASE WHEN deaths = 0 THEN 1 END) as zero_deaths,
                COUNT(*) as total
            FROM disease_records
            WHERE country_id = {sel_country_id}
        """)
        
        if not zero_stats.empty:
            row = zero_stats.iloc[0]
            zero_cases_pct = (row['zero_cases'] / row['total'] * 100) if row['total'] else 0
            zero_deaths_pct = (row['zero_deaths'] / row['total'] * 100) if row['total'] else 0
            
            c1, c2 = st.columns(2)
            c1.metric(t("zero_cases_records"), 
                     f"{row['zero_cases']:,} ({zero_cases_pct:.1f}%)")
            c2.metric(t("zero_deaths_records"), 
                     f"{row['zero_deaths']:,} ({zero_deaths_pct:.1f}%)")
        
        # 2. 时间序列完整性
        st.subheader(t("time_completeness"))
        time_gaps = run_query(f"""
            WITH months AS (
                SELECT DISTINCT date_trunc('month', time) as month
                FROM disease_records
                WHERE country_id = {sel_country_id}
                ORDER BY month
            )
            SELECT 
                month,
                LEAD(month) OVER (ORDER BY month) as next_month,
                EXTRACT(EPOCH FROM (LEAD(month) OVER (ORDER BY month) - month))/2592000 as gap_months
            FROM months
        """)
        
        if not time_gaps.empty:
            gaps = time_gaps[time_gaps['gap_months'] > 1]
            if len(gaps) > 0:
                st.warning(f"{t('found_gaps')}: {len(gaps)}")
                # 格式化日期列，只显示日期，安全处理空值
                gaps = gaps.copy()
                gaps['month'] = pd.to_datetime(gaps['month']).dt.strftime('%Y-%m-%d')
                # next_month 可能有 NaT 值，需要安全处理
                gaps['next_month'] = gaps['next_month'].apply(
                    lambda x: pd.to_datetime(x).strftime('%Y-%m-%d') if pd.notna(x) else 'N/A'
                )
                st.dataframe(gaps, width='stretch')
            else:
                st.success(t("no_gaps_found"))
        
        # 3. 数据来源分布
        st.subheader(t("data_sources"))
        source_dist = run_query(f"""
            SELECT 
                data_source,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as percentage
            FROM disease_records
            WHERE country_id = {sel_country_id}
            GROUP BY data_source
            ORDER BY count DESC
        """)
        
        if not source_dist.empty:
            fig = px.pie(source_dist, values='count', names='data_source', 
                        title=t('source_distribution'))
            st.plotly_chart(fig, width='stretch')
            st.dataframe(source_dist, width='stretch')
    else:
        st.info(t("select_country_prompt"))

# footer moved into redesigned sidebar
