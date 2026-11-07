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
            for figure in section.get("figures") or []:
                lines.extend([self._figure_table(figure, document, locale), ""])

        lines.extend(["---", "", "*本报告由 GlobalID 自动生成，数值结论来自已存储证据。*", ""])
        markdown_text = "\n".join(str(item) for item in lines)
        logger.info("Markdown formatted: {} characters", len(markdown_text))
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
                return template.render(
                    document=document, sections=document.get("sections") or [], metadata=metadata,
                    figure_tables={figure["id"]: self._figure_table(figure, document, "zh") for figure in document.get("figures") or []},
                )
            except Exception as exc:
                logger.warning("Failed to render template {}: {}", template_name, exc)

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
            parts.extend(self._figure_table(figure, document, "zh") for figure in section.get("figures") or [])
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
        logger.info("Report saved to {}", path)

    @staticmethod
    def _figure_table(figure: dict[str, Any], document: dict[str, Any], locale: str) -> Markup:
        """Durable HTML/Markdown/PDF fallback; interactive ECharts live on the site."""
        dataset = ((document.get("figure_data") or {}).get("datasets") or {}).get(figure.get("data_key")) or {}
        if figure.get("contract") != "report_v4.figures.1" or not dataset:
            return Markup("")
        kind = figure.get("figure_type")
        t = lambda zh, en, fr: {"zh": zh, "en": en, "fr": fr}.get(locale, en)
        localized = lambda value: ReportFormatter._localized(value, locale)
        headers = [t("报告期", "Period", "Période"), t("病例", "Cases", "Cas")]
        rows = [[row.get("period"), row.get("cases")] for row in dataset.get("points") or []]
        if kind in {"attention_ranking_bar", "attention_dot", "missingness_bar"}:
            headers = [t("字段", "Field", "Champ") if kind == "missingness_bar" else t("疾病", "Disease", "Maladie"),
                       t("缺失比例 (%)", "Missing (%)", "Manquant (%)") if kind == "missingness_bar" else t("监测关注分", "Attention score", "Score d’attention")]
            rows = [[localized(row.get("names")), row.get("score")] for row in dataset.get("rows") or []]
        elif kind == "case_distribution_boxplot":
            headers = [t("统计量", "Statistic", "Statistique"), t("病例", "Cases", "Cas")]
            labels = [t("最小值", "Minimum", "Minimum"), "Q1", t("中位数", "Median", "Médiane"), "Q3", t("最大值", "Maximum", "Maximum")]
            rows = list(zip(labels, dataset.get("summary") or []))
            rows.append((t("报告期数", "Periods", "Périodes"), dataset.get("sample_size")))
        elif kind == "case_distribution_histogram":
            headers = [t("病例区间", "Case interval", "Intervalle des cas"), t("报告期数", "Periods", "Périodes")]
            rows = [[f"[{r['lower']:g}, {r['upper']:g}{']' if i == 4 else ')'}", r["count"]] for i, r in enumerate(dataset.get("bins") or [])]
        elif kind == "period_change_bar":
            headers[1] = t("病例增减", "Case difference", "Variation des cas")
        elif kind == "rolling_average_line":
            headers.append(t("三期均值", "Three-period mean", "Moyenne sur trois périodes"))
            rows = [[r.get("period"), r.get("cases"), r.get("mean")] for r in dataset.get("points") or []]
        elif kind == "historical_context_line":
            headers.append(t("范围", "Scope", "Périmètre"))
            for row in rows:
                row.append(t("历史背景", "Historical", "Historique") if row[0] < dataset["report_start"] else t("本报告期", "Report period", "Période du rapport"))
            points = dataset.get("points") or []
            segments = list(dict.fromkeys(r.get("segment") for r in points if r.get("segment")))
            if segments:
                headers.append(t("来源分段", "Source segment", "Segment source"))
                for row, point in zip(rows, points):
                    row.append(f"{point.get('source')} · {segments.index(point['segment']) + 1}" if point.get("segment") else None)
        title = ReportFormatter._escape(localized(figure.get("title")))
        caption = ReportFormatter._escape(localized(figure.get("caption")))
        parts = [f"<figure><figcaption>{title} — {caption}</figcaption><table><thead><tr>"]
        parts.extend(f"<th>{ReportFormatter._escape(header)}</th>" for header in headers)
        parts.append("</tr></thead><tbody>")
        for row in rows:
            parts.append("<tr>" + "".join(f"<td>{ReportFormatter._escape(str(value) if value is not None else '—')}</td>" for value in row) + "</tr>")
        parts.append("</tbody></table></figure>")
        return Markup("".join(parts))

    @staticmethod
    def _document(metadata: dict[str, Any], sections: list[dict[str, Any]]) -> dict[str, Any]:
        document = metadata.get("report_document_v4")
        if isinstance(document, dict):
            return document
        return {
            "schema_version": "report_v4.0",
            "default_locale": "zh",
            "locales": ["zh", "en", "fr"],
            "title": {"zh": metadata.get("title") or "疾病监测报告", "en": metadata.get("title") or "Surveillance report", "fr": "Rapport de surveillance"},
            "summary": {"zh": metadata.get("summary") or "", "en": metadata.get("summary") or "", "fr": "Traduction française en attente."},
            "key_findings": {"zh": metadata.get("key_findings") or [], "en": metadata.get("key_findings") or [], "fr": ["Traduction française en attente."]},
            "sections": [
                {
                    "id": section.get("section_type") or section.get("type") or f"section_{index}",
                    "type": section.get("section_type") or section.get("type") or "section",
                    "order": index,
                    "title": {"zh": section.get("title") or "", "en": section.get("title") or "", "fr": "Traduction française en attente."},
                    "body": {"zh": section.get("content") or "", "en": section.get("content") or "", "fr": "Traduction française en attente."},
                }
                for index, section in enumerate(sections, 1)
            ],
        }

    @staticmethod
    def _localized(value: Any, locale: str, fallback: str = "") -> str:
        if isinstance(value, dict):
            direct = value.get(locale)
            if isinstance(direct, str) and direct.strip():
                return direct
            if locale == "fr":
                return fallback or "Traduction française en attente."
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
