"""Inner Mongolia official monthly bulletin configuration and death detail."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable

from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-NM", "Inner Mongolia, China", "内蒙古自治区", "150000",
    "https://wjw.nmg.gov.cn/zwgk/xxgk/zdlyxxgk/crbyq/", "narrative_only",
    pagination_url_template="https://wjw.nmg.gov.cn/zwgk/xxgk/zdlyxxgk/crbyq/index_{page}.html",
    max_index_pages=2,
)


def parse_named_monthly_deaths(
    text: str, *, report_date: date, title: str, source_url: str,
    artifact_sha256: str, config_path: str | Path,
    resolve_disease: Callable, source_row: Callable, source_id: str,
) -> list[dict[str, object]]:
    """Keep named death counts when the bulletin gives no disease case counts."""
    compact = re.sub(r"\s+", "", text)
    if not re.search(rf"{report_date.year}年0?{report_date.month}月份?", title):
        return []
    # Require the report's own month before the death clause. The clause
    # precedes last-month/year comparisons in these bulletins.
    match = re.search(
        rf"{report_date.year}年0?{report_date.month}月份?[，,]?[^。]{{0,300}}?"
        r"死亡(?P<total>[\d,]+)例[（(](?P<detail>.{1,180})[）)][，,]"
        r"(?:较|与|比|环比|同比)", compact,
    )
    if match is None:
        return []
    total = int(match["total"].replace(",", ""))
    pieces = re.split(r"[、,，]", match["detail"].rstrip("、,，"))
    if not pieces or len(pieces) > 30:
        return []
    parsed = []
    for piece in pieces:
        item = re.fullmatch(r"(?P<label>[^\d]+?)(?P<count>[\d,]+)例", piece)
        if item is None:
            return []
        disease = resolve_disease(item["label"], config_path)
        if disease is None:
            return []
        parsed.append((item["label"], int(item["count"].replace(",", "")), disease))
    if sum(count for _, count, _ in parsed) > total:
        return []
    codes = [str(disease["code"]) for _, _, disease in parsed]
    if len(codes) != len(set(codes)):
        return []
    rows = []
    for label, count, disease in parsed:
        code = str(disease["code"])
        row = source_row(
            report_date=report_date, province_code="CN-NM", disease=disease,
            raw_label=f"{label} [deaths]", cases=count, source_id=source_id,
            source_url=source_url,
            source_name="Inner Mongolia statutory infectious disease monthly bulletin",
            quality_status="raw",
            raw={
                "explicit_narrative_value": f"{label}{count}例",
                "explicit_narrative_evidence": [match.group(0)],
                "artifact_sha256": artifact_sha256, "title": title,
                "metric": "deaths", "paired_source_disease_code": code,
                "published_death_total": total,
                "named_death_subtotal": sum(n for _, n, _ in parsed),
                "named_death_breakdown_complete": sum(n for _, n, _ in parsed) == total,
            },
        )
        row["SourceDiseaseCode"] = f"{code}__deaths"
        row["Diseases"] = f"{code}__deaths"
        row["Measure"] = "deaths"
        rows.append(row)
    return rows
