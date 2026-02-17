"""Disease visualization plots."""
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go


def plot_top_diseases(df, t):
    """Render bar chart for top diseases by cases and deaths."""
    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(df, x='name', y='total_cases',
                     labels={'name': t('disease_label'), 'total_cases': t('cases')},
                     template='plotly_white')
        fig.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig, width='stretch')
    with col2:
        fig = px.bar(df, x='name', y='total_deaths',
                     labels={'name': t('disease_label'), 'total_deaths': t('deaths')},
                     template='plotly_white', color_discrete_sequence=['#EF553B'])
        fig.update_layout(height=350, xaxis_tickangle=-45)
        st.plotly_chart(fig, width='stretch')


def plot_trend_chart(df, t, df_display):
    """Render trend line chart for cases and deaths over time."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['time_period'], y=df['cases'],
                             mode='lines+markers', name=t('cases'),
                             line=dict(color='#636EFA', width=2)))
    fig.add_trace(go.Scatter(x=df['time_period'], y=df['deaths'],
                             mode='lines+markers', name=t('deaths'),
                             line=dict(color='#EF553B', width=2)))
    fig.update_layout(
        xaxis_title=t('time'),
        yaxis_title=t('count'),
        template='plotly_white',
        height=400,
        hovermode='x unified'
    )
    st.plotly_chart(fig, width='stretch')
    
    with st.expander(t('raw_data'), expanded=False):
        st.dataframe(df_display, width='stretch')
        csv = df.to_csv(index=False)
        st.download_button(t('download_csv'), data=csv, 
                          file_name='trend_data.csv', key='trend_download')
