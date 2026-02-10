"""
GlobalID V2 Chart Generator

图表生成器：使用Plotly生成可视化图表
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from src.core import get_logger

logger = get_logger(__name__)


class ChartGenerator:
    """
    图表生成器
    
    支持的图表类型：
    - 时间序列折线图
    - 柱状图
    - 饼图
    - 地理热力图
    - 组合图表
    """
    
    def __init__(self, theme: str = "plotly_white"):
        """
        初始化图表生成器
        
        Args:
            theme: Plotly主题
        """
        self.theme = theme
        self.default_height = 500
        self.default_width = 900
        logger.info(f"ChartGenerator initialized with theme '{theme}'")
    
    def generate_time_series(
        self,
        data: pd.DataFrame,
        x_col: str,
        y_cols: List[str],
        title: str,
        x_label: Optional[str] = None,
        y_label: Optional[str] = None,
        **kwargs
    ) -> go.Figure:
        """
        生成时间序列折线图
        
        Args:
            data: 数据DataFrame
            x_col: X轴列名（时间）
            y_cols: Y轴列名列表
            title: 图表标题
            x_label: X轴标签
            y_label: Y轴标签
            
        Returns:
            Plotly图表对象
        """
        logger.debug(f"Generating time series chart: {title}")
        
        fig = go.Figure()
        
        for y_col in y_cols:
            fig.add_trace(go.Scatter(
                x=data[x_col],
                y=data[y_col],
                mode='lines+markers',
                name=y_col if y_col != 'value' else kwargs.get('legend_name', y_col),
                line=dict(width=2),
                marker=dict(size=6),
            ))
        
        fig.update_layout(
            title=title,
            xaxis_title=x_label or x_col,
            yaxis_title=y_label or "Value",
            template=self.theme,
            hovermode='x unified',
            height=kwargs.get('height', self.default_height),
            width=kwargs.get('width', self.default_width),
        )
        
        return fig
    
    def generate_bar_chart(
        self,
        data: pd.DataFrame,
        x_col: str,
        y_col: str,
        title: str,
        orientation: str = 'v',
        **kwargs
    ) -> go.Figure:
        """
        生成柱状图
        
        Args:
            data: 数据DataFrame
            x_col: X轴列名
            y_col: Y轴列名
            title: 图表标题
            orientation: 方向（'v'=垂直, 'h'=水平）
            
        Returns:
            Plotly图表对象
        """
        logger.debug(f"Generating bar chart: {title}")
        
        fig = go.Figure()
        
        if orientation == 'v':
            fig.add_trace(go.Bar(
                x=data[x_col],
                y=data[y_col],
                text=data[y_col],
                textposition='auto',
            ))
        else:
            fig.add_trace(go.Bar(
                x=data[y_col],
                y=data[x_col],
                text=data[y_col],
                textposition='auto',
                orientation='h',
            ))
        
        fig.update_layout(
            title=title,
            template=self.theme,
            height=kwargs.get('height', self.default_height),
            width=kwargs.get('width', self.default_width),
        )
        
        return fig
    
    def generate_pie_chart(
        self,
        data: pd.DataFrame,
        labels_col: str,
        values_col: str,
        title: str,
        **kwargs
    ) -> go.Figure:
        """
        生成饼图
        
        Args:
            data: 数据DataFrame
            labels_col: 标签列名
            values_col: 数值列名
            title: 图表标题
            
        Returns:
            Plotly图表对象
        """
        logger.debug(f"Generating pie chart: {title}")
        
        fig = go.Figure(data=[go.Pie(
            labels=data[labels_col],
            values=data[values_col],
            textinfo='label+percent',
            hoverinfo='label+value+percent',
        )])
        
        fig.update_layout(
            title=title,
            template=self.theme,
            height=kwargs.get('height', self.default_height),
            width=kwargs.get('width', self.default_width),
        )
        
        return fig
    
    def generate_heatmap(
        self,
        data: pd.DataFrame,
        x_col: str,
        y_col: str,
        z_col: str,
        title: str,
        **kwargs
    ) -> go.Figure:
        """
        生成热力图
        
        Args:
            data: 数据DataFrame
            x_col: X轴列名
            y_col: Y轴列名
            z_col: 数值列名
            title: 图表标题
            
        Returns:
            Plotly图表对象
        """
        logger.debug(f"Generating heatmap: {title}")
        
        # 透视数据
        pivot_data = data.pivot(index=y_col, columns=x_col, values=z_col)
        
        fig = go.Figure(data=go.Heatmap(
            z=pivot_data.values,
            x=pivot_data.columns,
            y=pivot_data.index,
            colorscale='Viridis',
            hovertemplate='%{x}<br>%{y}<br>Value: %{z}<extra></extra>',
        ))
        
        fig.update_layout(
            title=title,
            template=self.theme,
            height=kwargs.get('height', self.default_height),
            width=kwargs.get('width', self.default_width),
        )
        
        return fig
    
    def generate_multi_chart(
        self,
        data: pd.DataFrame,
        x_col: str,
        y_cols: List[str],
        title: str,
        chart_types: Optional[List[str]] = None,
        **kwargs
    ) -> go.Figure:
        """
        生成组合图表（多个子图）
        
        Args:
            data: 数据DataFrame
            x_col: X轴列名
            y_cols: Y轴列名列表
            title: 图表标题
            chart_types: 图表类型列表（默认都是line）
            
        Returns:
            Plotly图表对象
        """
        logger.debug(f"Generating multi chart: {title}")
        
        if chart_types is None:
            chart_types = ['line'] * len(y_cols)
        
        rows = (len(y_cols) + 1) // 2  # 2列布局
        cols = 2 if len(y_cols) > 1 else 1
        
        fig = make_subplots(
            rows=rows,
            cols=cols,
            subplot_titles=y_cols,
        )
        
        for idx, (y_col, chart_type) in enumerate(zip(y_cols, chart_types)):
            row = (idx // 2) + 1
            col = (idx % 2) + 1
            
            if chart_type == 'line':
                fig.add_trace(
                    go.Scatter(x=data[x_col], y=data[y_col], mode='lines+markers'),
                    row=row, col=col
                )
            elif chart_type == 'bar':
                fig.add_trace(
                    go.Bar(x=data[x_col], y=data[y_col]),
                    row=row, col=col
                )
        
        fig.update_layout(
            title=title,
            template=self.theme,
            height=kwargs.get('height', rows * 300),
            width=kwargs.get('width', self.default_width),
            showlegend=False,
        )
        
        return fig
    
    def generate_geographic_map(
        self,
        data: pd.DataFrame,
        location_col: str,
        value_col: str,
        title: str,
        **kwargs
    ) -> go.Figure:
        """
        生成地理地图
        
        Args:
            data: 数据DataFrame
            location_col: 地理位置列名（国家代码或名称）
            value_col: 数值列名
            title: 图表标题
            
        Returns:
            Plotly图表对象
        """
        logger.debug(f"Generating geographic map: {title}")
        
        try:
            fig = px.choropleth(
                data,
                locations=location_col,
                locationmode='country names',
                color=value_col,
                hover_name=location_col,
                color_continuous_scale=px.colors.sequential.Plasma,
                title=title,
            )
            
            fig.update_layout(
                template=self.theme,
                height=kwargs.get('height', self.default_height),
                width=kwargs.get('width', self.default_width),
            )
            
            return fig
        
        except Exception as e:
            logger.error(f"Failed to generate geographic map: {e}")
            # 降级为简单的柱状图
            return self.generate_bar_chart(data, location_col, value_col, title, **kwargs)
    
    def save_chart(
        self,
        fig: go.Figure,
        filepath: str,
        format: str = 'html',
    ) -> None:
        """
        保存图表到文件
        
        Args:
            fig: Plotly图表对象
            filepath: 保存路径
            format: 格式（html/png/jpg/pdf）
        """
        logger.info(f"Saving chart to {filepath} (format: {format})")
        
        try:
            if format == 'html':
                fig.write_html(filepath)
            elif format in ['png', 'jpg', 'jpeg']:
                fig.write_image(filepath)
            elif format == 'pdf':
                fig.write_image(filepath, format='pdf')
            else:
                logger.warning(f"Unsupported format '{format}', using HTML")
                fig.write_html(filepath)
            
            logger.info(f"Chart saved successfully to {filepath}")
        
        except Exception as e:
            logger.error(f"Failed to save chart: {e}")
            raise
    
    def get_chart_html(self, fig: go.Figure) -> str:
        """
        获取图表的HTML代码
        
        Args:
            fig: Plotly图表对象
            
        Returns:
            HTML字符串
        """
        return fig.to_html(include_plotlyjs='cdn', full_html=False)
