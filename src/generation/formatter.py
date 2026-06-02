"""Markdown, HTML, and PDF formatting for canonical reports."""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from src.core import get_config, get_logger

logger = get_logger(__name__)


class ReportFormatter:
    """Format report_v4 documents for durable file exports."""

    def __init__(self, template_dir: str | Path | None = None):
        self.config = get_config()
        self.template_dir = Path(template_dir or Path(__file__).parent / "templates")
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.jinja_env.filters["markdown"] = self._markdown_to_html
        self.md_extensions = [
            "markdown.extensions.extra",
            "markdown.extensions.codehilite",
            "markdown.extensions.toc",
            "markdown.extensions.tables",
        ]

    @staticmethod
    def is_pdf_available() -> bool:
        return importlib.util.find_spec("weasyprint") is not None

    def _markdown_to_html(self, value: Any) -> Markup:
        text = value if isinstance(value, str) else ("" if value is None else str(value))
        return Markup(markdown.markdown(text, extensions=self.md_extensions))

    def format_markdown(self, sections: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
        document = self._document(metadata, sections)
        locale = document.get("default_locale") or "zh"
        title = self._localized(document.get("title"), locale, metadata.get("title") or "疾病监测报告")
        lines = [
            f"# {title}",
            "",
            f"**生成时间**: {metadata.get('generated_at') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**报告周期**: {metadata.get('period_start', '')} 至 {metadata.get('period_end', '')}",
            "",
            "## 执行摘要",
            "",
            self._localized(document.get("summary"), locale, metadata.get("summary") or ""),
            "",
        ]
        findings = (document.get("key_findings") or {}).get(locale) or []
        if findings:
            lines.extend(["## 关键发现", ""])
            lines.extend(f"- {finding}" for finding in findings)
            lines.append("")

        for index, section in enumerate(document.get("sections") or [], 1):
            title_text = self._localized(section.get("title"), locale, f"章节{index}")
            body_text = self._localized(section.get("body"), locale, "")
            lines.extend([f"## {index}. {title_text}", "", body_text, ""])

        lines.extend(["---", "", "*本报告由 GlobalID 自动生成，数值结论来自已存储证据。*", ""])
        markdown_text = "\n".join(str(item) for item in lines)
        logger.info("Markdown formatted: %s characters", len(markdown_text))
        return markdown_text

    def format_html(
        self,
        sections: list[dict[str, Any]],
        metadata: dict[str, Any],
        use_template: bool = True,
        template_name: str = "report.html",
    ) -> str:
        document = self._document(metadata, sections)
        if use_template:
            try:
                template = self.jinja_env.get_template(template_name)
                return template.render(document=document, sections=document.get("sections") or [], metadata=metadata)
            except Exception as exc:
                logger.warning("Failed to render template %s: %s", template_name, exc)

        title = self._localized(document.get("title"), "zh", metadata.get("title") or "疾病监测报告")
        parts = [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{self._escape(title)}</title>",
            "</head>",
            "<body>",
            f"<h1>{self._escape(title)}</h1>",
            self._markdown_to_html(self._localized(document.get("summary"), "zh", "")),
        ]
        for section in document.get("sections") or []:
            parts.append(f"<h2>{self._escape(self._localized(section.get('title'), 'zh', ''))}</h2>")
            parts.append(str(self._markdown_to_html(self._localized(section.get("body"), "zh", ""))))
        parts.extend(["</body>", "</html>"])
        return "\n".join(parts)

    def format_pdf(self, sections: list[dict[str, Any]], metadata: dict[str, Any]) -> bytes:
        try:
            from weasyprint import HTML
        except ImportError as exc:
            raise ImportError("Please install weasyprint: pip install weasyprint") from exc
        return HTML(string=self.format_html(sections, metadata, use_template=False)).write_pdf()

    def save(self, content: Any, filepath: str, format: str = "auto") -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        if format == "auto":
            format = "binary" if path.suffix == ".pdf" else "text"
        if format == "binary":
            path.write_bytes(content)
        else:
            path.write_text(str(content), encoding="utf-8")
        logger.info("Report saved to %s", path)

    @staticmethod
    def _document(metadata: dict[str, Any], sections: list[dict[str, Any]]) -> dict[str, Any]:
        document = metadata.get("report_document_v4")
        if isinstance(document, dict):
            return document
        return {
            "schema_version": "report_v4.0",
            "default_locale": "zh",
            "locales": ["zh", "en"],
            "title": {"zh": metadata.get("title") or "疾病监测报告", "en": metadata.get("title") or "Surveillance report"},
            "summary": {"zh": metadata.get("summary") or "", "en": metadata.get("summary") or ""},
            "key_findings": {"zh": metadata.get("key_findings") or [], "en": metadata.get("key_findings") or []},
            "sections": [
                {
                    "id": section.get("section_type") or section.get("type") or f"section_{index}",
                    "type": section.get("section_type") or section.get("type") or "section",
                    "order": index,
                    "title": {"zh": section.get("title") or "", "en": section.get("title") or ""},
                    "body": {"zh": section.get("content") or "", "en": section.get("content") or ""},
                }
                for index, section in enumerate(sections, 1)
            ],
        }

    @staticmethod
    def _localized(value: Any, locale: str, fallback: str = "") -> str:
        if isinstance(value, dict):
            direct = value.get(locale)
            if isinstance(direct, str):
                return direct
            zh = value.get("zh")
            if isinstance(zh, str):
                return zh
        if isinstance(value, str):
            return value
        return fallback

    @staticmethod
    def _escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _slugify(text: str) -> str:
        text = re.sub(r"[^\w\s-]", "", text.lower())
        return re.sub(r"[-\s]+", "-", text).strip("-")
