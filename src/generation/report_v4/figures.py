"""Bounded ECharts catalog: AI selects evidence-bound candidates, never chart code."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from src.generation.report_figures import ReportFigureSpec

FIGURE_VERSION = "report_v4.figures.1"
MAX_FIGURES = 2
CATALOG = (
    {
        "figure_type": "epidemic_curve", "section_type": "disease_context",
        "title": {"zh": "病例时间曲线", "en": "Reported case curve", "fr": "Courbe des cas déclarés"},
        "purpose": "Show within-report change for one disease discussed in this section; at least three observed periods.",
    },
    {
        "figure_type": "period_comparison_bar", "section_type": "disease_context",
        "title": {"zh": "相邻期病例对照", "en": "Adjacent-period cases", "fr": "Cas sur deux périodes consécutives"},
        "purpose": "Compare the last two adjacent observations for one disease; choose instead of its curve, never both.",
    },
    {
        "figure_type": "attention_ranking_bar", "section_type": "signal_evidence",
        "title": {"zh": "监测关注排序", "en": "Surveillance attention ranking", "fr": "Priorité de surveillance"},
        "purpose": "Compare calculated review-priority scores for diseases already discussed in this section; not health risk or probability.",
    },
)


# Additive contract: historical v1 figures remain readable. New candidates use
# server-calculated values; the model selects intent, never statistical methods.
_EXTRA = (
    ("historical_context_line", "disease_context", "重点疾病历史背景", "Priority disease historical context", "Contexte historique prioritaire", "For a disease explicitly discussed as needing review: show up to 52 periods including at least three pre-report observations; distinguish history from the report window and separate source/scope segments without cross-segment comparisons. Not a forecast or causal explanation."),
    ("period_cases_bar", "disease_context", "分期病例柱状图", "Cases by period", "Cas par période", "Show discrete period counts; at least three observations. Alternative to the case curve."),
    ("cumulative_cases_line", "disease_context", "窗口累计病例", "Within-window cumulative cases", "Cumul sur la fenêtre", "Show accumulation only within the report window; at least three complete contiguous periods. Not epidemic growth or unique people."),
    ("rolling_average_line", "disease_context", "三期移动均值", "Three-period moving mean", "Moyenne mobile sur trois périodes", "Show a trailing three-period mean alongside raw counts; at least six complete contiguous periods. Never forecast or hide raw counts."),
    ("period_change_bar", "disease_context", "相邻期病例增减", "Adjacent-period case changes", "Variation entre périodes", "Show signed absolute differences, not percentages; at least four complete contiguous periods. Changes are not new infections."),
    ("case_distribution_boxplot", "disease_context", "病例分布箱线图", "Period-count distribution", "Distribution des comptes", "Summarize spread across at least eight complete contiguous periods. Whiskers are minimum/maximum; quartiles use linear interpolation. Not confidence intervals or individual risk."),
    ("case_distribution_histogram", "disease_context", "病例分布直方图", "Period-count histogram", "Histogramme des comptes", "Show frequency of period counts in five equal-width bins; at least ten complete contiguous periods with variation. Last bin includes its upper bound. Not a distribution of people."),
    ("attention_dot", "signal_evidence", "监测关注点图", "Surveillance attention dot plot", "Points de priorité", "Compact alternative to the attention bar chart, using the same fixed zero-to-hundred review-priority scale. Not health risk."),
    ("missingness_bar", "data_notes", "字段缺失比例", "Field missingness", "Proportion de données manquantes", "Explain known missingness for at least two fields, with at least one missing value. Source-unreported fields are unavailable, not zero disease burden or reporting failure."),
)
CATALOG += tuple({
    "figure_type": kind, "section_type": section,
    "title": {"zh": zh, "en": en, "fr": fr}, "purpose": purpose,
} for kind, section, zh, en, fr, purpose in _EXTRA)
for _entry in CATALOG:
    _entry["position"] = "after_content"
    _entry["exclusive_group"] = _entry["section_type"]


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    offset = (len(ordered) - 1) * fraction
    low = int(offset)
    return round(ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (offset - low), 6)


def _derived_candidates(points: list[dict], series: list[dict]) -> list[tuple[str, dict]]:
    result = []
    if sum(p["cases"] is not None for p in points) >= 3:
        result.append(("period_cases_bar", {"points": points}))
    # Derived statistics require comparable, fully observed adjacent intervals.
    if len(points) < 3 or any(p["cases"] is None for p in points):
        return result
    if any(r.get("quality_status") == "partial_report_window" or not r.get("period_end") for r in series):
        return result
    if any(a["period_end"] != b["period"] for a, b in zip(series, series[1:])):
        return result
    values = [p["cases"] for p in points]
    total = 0
    cumulative = []
    for point in points:
        total += point["cases"]
        cumulative.append({"period": point["period"], "cases": total})
    result.append(("cumulative_cases_line", {"points": cumulative}))
    if len(points) >= 4:
        result.append(("period_change_bar", {"points": [
            {"period": points[i]["period"], "cases": values[i] - values[i - 1]} for i in range(1, len(points))
        ]}))
    if len(points) >= 6:
        result.append(("rolling_average_line", {"points": [
            {"period": point["period"], "cases": point["cases"], "mean": round(sum(values[i-2:i+1]) / 3, 6) if i >= 2 else None}
            for i, point in enumerate(points)
        ]}))
    if len(points) >= 8:
        result.append(("case_distribution_boxplot", {"summary": [_quantile(values, q) for q in (0, .25, .5, .75, 1)], "sample_size": len(values)}))
    if len(points) >= 10 and max(values) > min(values):
        low, high = min(values), max(values)
        step = (high - low) / 5
        bins = [{"lower": low + i * step, "upper": low + (i + 1) * step, "count": 0} for i in range(5)]
        for value in values:
            bins[min(4, int((value - low) / step))]["count"] += 1
        result.append(("case_distribution_histogram", {"bins": bins, "sample_size": len(values)}))
    return result


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _section_diseases(document: dict, section_type: str) -> set[str]:
    return {
        str(ref).split(":", 1)[1].split(".", 1)[0]
        for section in document.get("sections") or []
        if section.get("type") == section_type
        for ref in section.get("evidence_refs") or []
        if str(ref).startswith("disease:")
    }


def figure_candidates(packet: dict, document: dict) -> list[dict[str, Any]]:
    """Only offer candidates renderable from this report's evidence and sections."""
    candidates: list[dict[str, Any]] = []
    disease_ids = _section_diseases(document, "disease_context")
    for disease in packet.get("diseases") or []:
        disease_id = str(disease.get("disease_id") or "")
        if disease_id not in disease_ids:
            continue
        series = disease.get("figure_series", (disease.get("visual_diagnostics") or {}).get("series") or [])
        if not isinstance(series, list) or any(not isinstance(row, dict) or not row.get("period") for row in series):
            continue
        truncated = len(series) > 52
        series = series[-52:]
        points = [
            {"period": str(row["period"]), "cases": _number(row.get("cases"))}
            for row in series if isinstance(row, dict) and row.get("period")
        ][-52:]
        # Never silently reorder, merge duplicate periods, or substitute missing counts.
        periods = [row["period"] for row in points]
        if periods != sorted(set(periods)) or any(row["cases"] is not None and row["cases"] < 0 for row in points):
            continue
        complete_adjacent = (
            len(points) >= 2
            and not any(row.get("quality_status") == "partial_report_window" for row in series[-2:])
            and ("period_end" not in series[-2] or series[-2]["period_end"] == series[-1].get("period"))
        )
        # Insert a visible gap when a known reporting interval is missing.
        curve_points = []
        for index, row in enumerate(points):
            if index and (previous_end := series[-len(points):][index - 1].get("period_end")) and previous_end != row["period"]:
                curve_points.append({"period": str(previous_end), "cases": None})
            curve_points.append(row)
        names = {locale: str(disease.get(f"name_{locale}") or disease.get("name_en") or disease_id) for locale in ("zh", "en", "fr")}
        for kind in ("epidemic_curve", "period_comparison_bar"):
            usable = sum(row["cases"] is not None for row in points) >= 3 if kind == "epidemic_curve" else complete_adjacent and all(row["cases"] is not None for row in points[-2:])
            if not usable:
                continue
            candidates.append({
                "candidate_id": f"{kind}:{disease_id}", "figure_type": kind,
                "section_type": "disease_context", "disease_id": disease_id,
                "position": "after_content", "names": names,
                "evidence_refs": [f"disease:{disease_id}.total_cases"],
                "data": {"points": curve_points if kind == "epidemic_curve" else points[-2:]},
            })
        history = disease.get("historical_figure_series") or []
        if history:
            start = str((packet.get("period") or {}).get("start") or "")[:10]
            end = str((packet.get("period") or {}).get("end") or "")[:10]
            hpoints = [{"period": str(r["period"]), "cases": _number(r.get("cases")), **{k: r[k] for k in ("source", "segment", "source_scope") if k in r}} for r in history]
            periods_h = [r["period"] for r in hpoints]
            historical_count = sum(r["period"] < start and r["cases"] is not None for r in hpoints)
            if (start and end and len(hpoints) <= 52 and historical_count >= 3
                    and periods_h == sorted(set(periods_h)) and all(r["period"] <= end for r in hpoints)
                    and all(r["cases"] is None or r["cases"] >= 0 for r in hpoints)
                    and any(start <= r["period"] <= end and r["cases"] is not None for r in hpoints)):
                with_gaps = []
                for i, point in enumerate(hpoints):
                    if i and history[i-1].get("period_end") and history[i-1]["period_end"] != point["period"]:
                        with_gaps.append({"period": history[i-1]["period_end"], "cases": None})
                    with_gaps.append(point)
                candidates.append({"candidate_id": f"historical_context_line:{disease_id}", "figure_type": "historical_context_line",
                                   "section_type": "disease_context", "disease_id": disease_id, "names": names, "position": "after_content",
                                   "evidence_refs": [f"disease:{disease_id}.historical_context"],
                                   "data": {"points": with_gaps, "report_start": start, "report_end": end, "scope": "historical_context", "source_comparison": "separate_segments_only"}})
        for kind, data in _derived_candidates(points, series):
            if truncated and kind != "period_cases_bar":
                continue
            if kind == "period_cases_bar":
                data = {"points": curve_points}
            candidates.append({
                "candidate_id": f"{kind}:{disease_id}", "figure_type": kind,
                "section_type": "disease_context", "disease_id": disease_id,
                "position": "after_content", "names": names,
                "evidence_refs": [f"disease:{disease_id}.total_cases"], "data": data,
            })
    ranking_ids = _section_diseases(document, "signal_evidence")
    ranking = [
        {
            "disease_id": str(row["disease_id"]),
            "names": {locale: str(row.get(f"name_{locale}") or row.get("name_en") or row["disease_id"]) for locale in ("zh", "en", "fr")},
            "score": _number(row.get("attention_score", row.get("risk_score"))),
        }
        for row in packet.get("attention_ranking") or packet.get("risk_ranking") or []
        if str(row.get("disease_id")) in ranking_ids
        and _number(row.get("attention_score", row.get("risk_score"))) is not None
        and 0 <= row.get("attention_score", row.get("risk_score")) <= 100
    ][:5]
    if len(ranking) >= 2:
        candidates.append({
            "candidate_id": "attention_ranking_bar", "figure_type": "attention_ranking_bar",
            "section_type": "signal_evidence", "disease_id": None,
            "position": "after_content", "evidence_refs": [f"disease:{row['disease_id']}.latest_cases" for row in ranking],
            "data": {"rows": ranking},
        })
        candidates.append({**candidates[-1], "candidate_id": "attention_dot", "figure_type": "attention_dot"})
    fields = {
        "cases": {"zh": "病例", "en": "Cases", "fr": "Cas"},
        "deaths": {"zh": "死亡", "en": "Deaths", "fr": "Décès"},
        "incidence_rate": {"zh": "发病率", "en": "Incidence rate", "fr": "Taux d’incidence"},
        "mortality_rate": {"zh": "死亡率", "en": "Mortality rate", "fr": "Taux de mortalité"},
    }
    rates = (packet.get("data_quality") or {}).get("missing_rates") or {}
    missing = [{"names": names, "score": rates[field] * 100} for field, names in fields.items()
               if _number(rates.get(field)) is not None and 0 <= rates[field] <= 1]
    if any(s.get("type") == "data_notes" for s in document.get("sections") or []) and len(missing) >= 2 and any(r["score"] > 0 for r in missing):
        candidates.append({"candidate_id": "missingness_bar", "figure_type": "missingness_bar", "section_type": "data_notes", "disease_id": None,
                           "position": "after_content", "evidence_refs": ["data_quality:missing_rates"], "data": {"rows": missing}})
    return candidates


def validate_figure_plan(raw: Any, candidates: list[dict]) -> tuple[list[dict], list[str]]:
    if not isinstance(raw, list) or len(raw) > MAX_FIGURES:
        return [], [f"guardrail: figure_plan must be a list of at most {MAX_FIGURES} selections"]
    by_id = {item["candidate_id"]: item for item in candidates}
    plan, notes, sections = [], [], set()
    for selection in raw:
        if not isinstance(selection, dict) or set(selection) != {"candidate_id", "rationale"}:
            notes.append("guardrail: figure selection accepts only candidate_id and rationale; no code, options or data")
            continue
        candidate = by_id.get(str(selection.get("candidate_id")))
        rationale = selection.get("rationale")
        if candidate is None or not isinstance(rationale, str) or not 10 <= len(rationale.strip()) <= 300 or re.search(r"[<>]|https?://", rationale):
            notes.append("guardrail: unavailable figure candidate or invalid rationale")
            continue
        section = candidate["section_type"]
        if section in sections:
            notes.append("guardrail: at most one figure per section; do not repeat a disease curve and comparison")
            continue
        sections.add(section)
        plan.append({"candidate_id": candidate["candidate_id"], "rationale": rationale.strip()})
    return plan, notes


def attach_figures(document: dict, packet: dict, plan: list[dict]) -> None:
    candidates = figure_candidates(packet, document)
    selections, notes = validate_figure_plan(plan, candidates)
    if notes:
        raise ValueError("Invalid reviewed report figures: " + "; ".join(notes))
    by_id = {item["candidate_id"]: item for item in candidates}
    catalog = {item["figure_type"]: item for item in CATALOG}
    sections = {section["type"]: section for section in document["sections"]}
    document["figures"] = []
    document["figure_data"] = {"version": FIGURE_VERSION, "datasets": {}}
    scope = (packet.get("death_reporting") or {}).get("source_policy") or {}
    for selection in sorted(selections, key=lambda item: sections[by_id[item["candidate_id"]]["section_type"]]["order"]):
        candidate = by_id[selection["candidate_id"]]
        kind, section = candidate["figure_type"], candidate["section_type"]
        spec = ReportFigureSpec(kind, section, candidate["disease_id"], rationale=selection["rationale"], source="ai_peer_review")
        title = dict(catalog[kind]["title"])
        if candidate.get("names"):
            title = {locale: f"{candidate['names'][locale]} · {text}" for locale, text in title.items()}
        caption = {
            "zh": "仅展示本报告窗口内、按来源口径记录的病例；空缺不视为零，不代表全国感染总量。",
            "en": "Source-reported cases within this report window. Missing observations are not zero; these are not national infection totals.",
            "fr": "Cas déclarés dans la période du rapport. Les valeurs manquantes ne valent pas zéro ; il ne s’agit pas du total national des infections.",
        } if kind not in {"attention_ranking_bar", "attention_dot"} else {
            "zh": "关注分仅用于安排监测复核顺序，未经概率校准，不代表感染、重症、死亡或暴发风险。",
            "en": "Attention scores only order surveillance review. They are not calibrated probabilities or estimates of infection, severity, mortality or outbreak risk.",
            "fr": "Les scores ordonnent uniquement la revue de surveillance ; ils ne sont ni des probabilités étalonnées ni des risques d’infection, de gravité, de décès ou d’épidémie.",
        }
        if kind == "missingness_bar":
            caption = {"zh": "缺失比例描述字段可用性；来源未提供不等于疾病负担为零，也不直接证明漏报。", "en": "Missingness describes field availability; unreported fields are not zero burden or evidence of underreporting.", "fr": "Les valeurs manquantes décrivent la disponibilité, non une charge nulle ni une sous-déclaration."}
        extra_captions = {
            "historical_context_line": ("含最多52期历史背景，着色区域为本报告期；来源或口径切换处分段，不跨段比较，不推断原因或未来走势。", "Includes up to 52 historical periods; shaded region marks this report. Source/scope changes form separate segments: no cross-segment comparison, causal inference or forecast.", "Jusqu’à 52 périodes ; zone colorée : période du rapport. Sources/périmètres séparés, sans comparaison entre segments, causalité ni prévision."),
            "cumulative_cases_line": ("累计从本窗口起点开始，不表示独立感染人数。", "Cumulative from this window's start, not unique infections.", "Cumul depuis le début de la fenêtre, non des infections uniques."),
            "rolling_average_line": ("三期后向均值；前两期无均值，保留原始病例线，不是预测。", "Trailing three-period mean; first two means unavailable. Raw counts retained; not a forecast.", "Moyenne rétrospective sur trois périodes ; deux premières indisponibles. Comptes bruts conservés, sans prévision."),
            "period_change_bar": ("表示与上一期相比的绝对增减，不是新增感染或百分比。", "Absolute difference from the preceding period, not new infections or a percentage.", "Différence absolue avec la période précédente, non de nouvelles infections ni un pourcentage."),
            "case_distribution_boxplot": ("须线为最小/最大值，分位数采用线性插值；不是置信区间。", "Whiskers: minimum/maximum; linearly interpolated quartiles, not confidence intervals.", "Moustaches : minimum/maximum ; quartiles interpolés linéairement, non des intervalles de confiance."),
            "case_distribution_histogram": ("五个等宽区间，左闭右开，末组含上界；频数为报告期数，不是人数。", "Five equal-width bins, left-inclusive and right-exclusive except the final upper bound; frequency counts periods, not people.", "Cinq classes égales, borne gauche incluse, droite exclue sauf la dernière ; fréquence des périodes, non des personnes."),
        }
        if kind in extra_captions:
            caption = {locale: f"{caption[locale]} {extra}" for locale, extra in zip(("zh", "en", "fr"), extra_captions[kind])}
        if kind == "historical_context_line":
            caption = dict(zip(("zh", "en", "fr"), extra_captions[kind]))
        key = candidate["candidate_id"]
        figure = {
            **spec.to_metadata(), "id": f"fig-{key}", "number": len(document["figures"]) + 1,
            "renderer": "echarts", "contract": FIGURE_VERSION, "data_key": key,
            "title": title, "caption": caption, "height": 340,
            "dataset_sha256": _dataset_digest(candidate["data"]),
            "data_scope": "historical_context" if kind == "historical_context_line" else "report_window",
            "evidence_refs": candidate["evidence_refs"], "source_scope": scope,
            "period": packet.get("period") or {}, "data_signature": packet.get("data_signature"),
        }
        document["figure_data"]["datasets"][key] = candidate["data"]
        document["figures"].append(figure)
        sections[section]["figures"] = [figure]


def _dataset_digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def figure_contract_issues(document: dict) -> list[str]:
    """Check placement and dataset bindings again at the publication quality gate."""
    figures = document.get("figures") or []
    if not figures and not any(section.get("figures") for section in document.get("sections") or []):
        return []
    issues = []
    if len(figures) > MAX_FIGURES:
        issues.append("Report figure limit exceeded")
    datasets = (document.get("figure_data") or {}).get("datasets") or {}
    allowed = {item["figure_type"]: item["section_type"] for item in CATALOG}
    seen = set()
    attached = []
    for section in document.get("sections") or []:
        section_figures = section.get("figures") or []
        if len(section_figures) > 1:
            issues.append("More than one figure in a section")
        for figure in section_figures:
            attached.append(figure)
            if figure.get("section_type") != section.get("type"):
                issues.append("Figure placed in a different section")
    if attached != figures:
        issues.append("Section figures differ from the ordered report figure list")
    for figure in figures:
        if (figure.get("contract") != FIGURE_VERSION or figure.get("renderer") != "echarts"
                or allowed.get(figure.get("figure_type")) != figure.get("section_type")
                or figure.get("position") != "after_content" or figure.get("data_key") not in datasets
                or figure.get("id") in seen or any(key in figure for key in ("html", "option", "code"))):
            issues.append("Invalid figure type, placement, duplicate, or data binding")
        digest = figure.get("dataset_sha256")
        if digest:
            try:
                if digest != _dataset_digest(datasets.get(figure.get("data_key")) or {}):
                    issues.append("Figure dataset changed after evidence binding")
            except (ValueError, TypeError):
                issues.append("Figure dataset contains invalid statistical values")
        seen.add(figure.get("id"))
        if figure.get("disease_id") and str(figure["disease_id"]) not in _section_diseases(document, str(figure.get("section_type"))):
            issues.append("Figure disease is absent from section evidence")
        if figure.get("data_signature") != (document.get("metrics") or {}).get("data_signature"):
            issues.append("Figure data signature differs from report evidence")
    return issues
