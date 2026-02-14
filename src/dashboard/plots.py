"""Plotting helpers for the dashboard.

This module contains small wrapper functions that build Plotly figures
and render them into Streamlit. Keeping plotting code here reduces the
size of `app.py` and makes the visuals easier to test and maintain.
"""
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd


def plot_top_diseases(top_df: pd.DataFrame, t):
    """Render the top diseases horizontal bar and a small data table.

    The function creates a two-column layout: the chart on the left and
    the table on the right (matching the previous app layout).
    """
    col_chart, col_table = st.columns([2, 1])
    with col_chart:
        fig = px.bar(top_df, x='total_cases', y='name', orientation='h',
                     labels={'total_cases': t('cases'), 'name': t('disease_label')},
                     template='plotly_white')
        fig.update_layout(height=400, yaxis={'categoryorder': 'total ascending'})
        st.plotly_chart(fig, width='stretch')
    with col_table:
        st.dataframe(top_df, height=400, width='stretch')


def plot_trend_chart(trend_df: pd.DataFrame, t, trend_df_display: pd.DataFrame = None):
    """Render the trend chart with dual Y axes (cases and deaths).

    `trend_df` is expected to contain columns `time_period`, `cases`,
    and `deaths`. `trend_df_display` is an optional formatted copy used
    for the raw-data expander and CSV download.
    """
    fig = go.Figure()

    # Cases as bar chart (left Y-axis)
    fig.add_trace(go.Bar(
        x=trend_df['time_period'],
        y=trend_df['cases'],
        name=t('cases'),
        marker_color='#1f77b4',
        yaxis='y'
    ))

    # Deaths as line chart (right Y-axis) to avoid overlap
    fig.add_trace(go.Scatter(
        x=trend_df['time_period'],
        y=trend_df['deaths'],
        name=t('deaths'),
        line=dict(color='#d62728', width=3),
        mode='lines+markers',
        marker=dict(size=6),
        yaxis='y2'
    ))

    fig.update_layout(
        template='plotly_white',
        height=400,
        xaxis_title=t('time'),
        yaxis=dict(
            title=dict(text=t('cases'), font=dict(color='#1f77b4')),
            tickfont=dict(color='#1f77b4')
        ),
        yaxis2=dict(
            title=dict(text=t('deaths'), font=dict(color='#d62728')),
            tickfont=dict(color='#d62728'),
            overlaying='y',
            side='right'
        ),
        barmode='group'
    )

    st.plotly_chart(fig, width='stretch')

    if trend_df_display is not None:
        with st.expander(t("raw_data"), expanded=False):
            st.dataframe(trend_df_display, width='stretch')
            csv = trend_df_display.to_csv(index=False)
            st.download_button(t("download_csv"), data=csv, file_name='trend.csv')
