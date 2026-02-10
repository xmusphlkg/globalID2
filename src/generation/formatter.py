"""
GlobalID V2 Report Formatter

报告格式化器：将报告内容转换为不同格式（Markdown/HTML/PDF）
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core import get_config, get_logger

logger = get_logger(__name__)


class ReportFormatter:
    """
    报告格式化器
    
    支持的格式：
    - Markdown
    - HTML
    - PDF（通过HTML转换）
    """
    
    def __init__(self, template_dir: Optional[str] = None):
        """
        初始化格式化器
        
        Args:
            template_dir: 模板目录路径
        """
        self.config = get_config()
        
        # 设置模板目录
        if template_dir is None:
            template_dir = Path(__file__).parent / "templates"
        
        self.template_dir = Path(template_dir)
        self.template_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化Jinja2环境
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(['html', 'xml']),
        )
        
        # Markdown扩展
        self.md_extensions = [
            'markdown.extensions.extra',
            'markdown.extensions.codehilite',
            'markdown.extensions.toc',
            'markdown.extensions.tables',
        ]
        
        logger.info(f"ReportFormatter initialized with template dir: {template_dir}")
    
    def format_markdown(
        self,
        sections: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> str:
        """
        格式化为Markdown
        
        Args:
            sections: 报告章节列表
            metadata: 报告元数据
            
        Returns:
            Markdown文本
        """
        logger.debug("Formatting report as Markdown")
        
        lines = []
        
        # 标题和元数据
        lines.append(f"# {metadata.get('title', '疾病监测报告')}")
        lines.append("")
        lines.append(f"**生成时间**: {metadata.get('generated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}")
        lines.append(f"**报告周期**: {metadata.get('period_start', '')} 至 {metadata.get('period_end', '')}")
        lines.append(f"**国家/地区**: {metadata.get('country', '中国')}")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 目录
        lines.append("## 目录")
        lines.append("")
        for idx, section in enumerate(sections, 1):
            lines.append(f"{idx}. [{section.get('title', f'章节{idx}')}](#{self._slugify(section.get('title', f'section{idx}'))})")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 各章节内容
        for idx, section in enumerate(sections, 1):
            section_title = section.get('title', f'章节{idx}')
            lines.append(f"## {idx}. {section_title}")
            lines.append("")
            
            content = section.get('content', '')
            lines.append(content)
            lines.append("")
            
            # 如果有图表
            if 'chart_path' in section:
                lines.append(f"![{section_title} 图表]({section['chart_path']})")
                lines.append("")
            
            lines.append("---")
            lines.append("")
        
        # 页脚
        lines.append("---")
        lines.append("")
        lines.append(f"*本报告由 GlobalID V2 自动生成*")
        lines.append("")
        
        markdown_text = "\n".join(lines)
        
        logger.info(f"Markdown formatted: {len(markdown_text)} characters")
        return markdown_text
    
    def format_html(
        self,
        sections: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        use_template: bool = True,
        template_name: str = "report.html",
    ) -> str:
        """
        格式化为HTML
        
        Args:
            sections: 报告章节列表
            metadata: 报告元数据
            use_template: 是否使用模板
            template_name: 模板名称
            
        Returns:
            HTML文本
        """
        logger.debug("Formatting report as HTML")
        
        if use_template:
            # 使用Jinja2模板
            try:
                template = self.jinja_env.get_template(template_name)
                html = template.render(
                    sections=sections,
                    metadata=metadata,
                )
                return html
            except Exception as e:
                logger.warning(f"Failed to use template '{template_name}': {e}, falling back to simple HTML")
        
        # 简单HTML格式化
        html_parts = []
        
        # HTML头部
        html_parts.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #34495e;
            margin-top: 30px;
        }}
        .metadata {{
            background-color: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 30px;
        }}
        .section {{
            margin-bottom: 40px;
        }}
        .chart {{
            margin: 20px 0;
            text-align: center;
        }}
        .footer {{
            text-align: center;
            color: #7f8c8d;
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #bdc3c7;
        }}
    </style>
</head>
<body>
    <div class="container">
""".format(title=metadata.get('title', '疾病监测报告')))
        
        # 标题和元数据
        html_parts.append(f"<h1>{metadata.get('title', '疾病监测报告')}</h1>")
        html_parts.append('<div class="metadata">')
        html_parts.append(f"<p><strong>生成时间</strong>: {metadata.get('generated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</p>")
        html_parts.append(f"<p><strong>报告周期</strong>: {metadata.get('period_start', '')} 至 {metadata.get('period_end', '')}</p>")
        html_parts.append(f"<p><strong>国家/地区</strong>: {metadata.get('country', '中国')}</p>")
        html_parts.append('</div>')
        
        # 各章节
        for idx, section in enumerate(sections, 1):
            html_parts.append('<div class="section">')
            html_parts.append(f"<h2>{idx}. {section.get('title', f'章节{idx}')}</h2>")
            
            # 将Markdown内容转换为HTML
            content = section.get('content', '')
            content_html = markdown.markdown(content, extensions=self.md_extensions)
            html_parts.append(content_html)
            
            # 图表
            if 'chart_html' in section:
                html_parts.append('<div class="chart">')
                html_parts.append(section['chart_html'])
                html_parts.append('</div>')
            elif 'chart_path' in section:
                html_parts.append('<div class="chart">')
                html_parts.append(f'<img src="{section["chart_path"]}" alt="{section.get("title", "图表")}" style="max-width:100%;">')
                html_parts.append('</div>')
            
            html_parts.append('</div>')
        
        # 页脚
        html_parts.append('<div class="footer">')
        html_parts.append('<p><em>本报告由 GlobalID V2 自动生成</em></p>')
        html_parts.append('</div>')
        
        # HTML尾部
        html_parts.append("""
    </div>
</body>
</html>
""")
        
        html = "\n".join(html_parts)
        
        logger.info(f"HTML formatted: {len(html)} characters")
        return html
    
    def format_pdf(
        self,
        sections: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> bytes:
        """
        格式化为PDF
        
        Args:
            sections: 报告章节列表
            metadata: 报告元数据
            
        Returns:
            PDF二进制数据
        """
        logger.debug("Formatting report as PDF")
        
        try:
            from weasyprint import HTML
            
            # 先生成HTML
            html = self.format_html(sections, metadata, use_template=False)
            
            # 转换为PDF
            pdf_bytes = HTML(string=html).write_pdf()
            
            logger.info(f"PDF formatted: {len(pdf_bytes)} bytes")
            return pdf_bytes
        
        except ImportError:
            logger.error("WeasyPrint not installed, cannot generate PDF")
            raise ImportError("Please install weasyprint: pip install weasyprint")
        
        except Exception as e:
            logger.error(f"Failed to generate PDF: {e}")
            raise
    
    def save(
        self,
        content: Any,
        filepath: str,
        format: str = 'auto',
    ) -> None:
        """
        保存报告到文件
        
        Args:
            content: 报告内容（文本或字节）
            filepath: 保存路径
            format: 格式（auto/text/binary）
        """
        logger.info(f"Saving report to {filepath}")
        
        # 自动判断格式
        if format == 'auto':
            if filepath.endswith('.pdf'):
                format = 'binary'
            else:
                format = 'text'
        
        # 确保目录存在
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        # 保存文件
        try:
            if format == 'binary':
                with open(filepath, 'wb') as f:
                    f.write(content)
            else:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            logger.info(f"Report saved successfully to {filepath}")
        
        except Exception as e:
            logger.error(f"Failed to save report: {e}")
            raise
    
    @staticmethod
    def _slugify(text: str) -> str:
        """将文本转换为URL友好的slug"""
        import re
        import unicodedata
        
        # 转换为小写
        text = text.lower()
        
        # 移除非字母数字字符
        text = re.sub(r'[^\w\s-]', '', text)
        
        # 将空格替换为连字符
        text = re.sub(r'[-\s]+', '-', text)
        
        return text.strip('-')
