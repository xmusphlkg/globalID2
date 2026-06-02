"""Compose the decision-oriented report v4 document."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import DiseaseDirectoryItem, LocalizedText, ReportDocument, Section


def compose_report_document(
    *,
    evidence_packet: dict[str, Any],
    country: dict[str, Any],
    period_start: datetime,
    period_end: datetime,
) -> ReportDocument:
    summary = evidence_packet.get("summary_metrics") or {}
    death_reporting = evidence_packet.get("death_reporting") or {}
    risk_ranking = evidence_packet.get("risk_ranking") or []
    disease_cards = evidence_packet.get("diseases") or []
    disease_directory = _disease_directory(disease_cards, risk_ranking, death_reporting)
    data_quality = evidence_packet.get("data_quality") or {}
    evidence_index = evidence_packet.get("evidence_index") or {}

    country_zh = country.get("name_zh") or country.get("name_local") or country.get("name") or country.get("name_en") or "该地区"
    country_en = country.get("name_en") or country.get("name") or country_zh
    period_zh = f"{period_start:%Y-%m-%d} 至 {period_end:%Y-%m-%d}"
    period_en = f"{period_start:%Y-%m-%d} to {period_end:%Y-%m-%d}"
    lead = risk_ranking[0] if risk_ranking else {}
    lead_zh = lead.get("name_zh") or lead.get("name_en") or "暂无首要信号"
    lead_en = lead.get("name_en") or lead.get("name_zh") or "No lead signal"
    total_cases = int(summary.get("total_cases") or 0)
    latest_cases = int(summary.get("latest_cases") or 0)
    high_risk = int(summary.get("high_risk_diseases") or 0)
    disease_count = int(summary.get("disease_count") or 0)

    title = LocalizedText(
        zh=f"{country_zh}传染病监测决策简报 | {period_zh}",
        en=f"{country_en} Infectious Disease Decision Brief | {period_en}",
    )
    death_note = death_reporting.get("display_note") or {}
    summary_text = LocalizedText(
        zh=(
            f"本期共纳入 {disease_count} 个疾病信号，记录病例 {total_cases:,} 例，"
            f"最新一期病例 {latest_cases:,} 例。当前最需要关注的是 {lead_zh}。"
            f"{death_note.get('zh', '')}"
        ),
        en=(
            f"This report covers {disease_count} disease signals with {total_cases:,} cases "
            f"and {latest_cases:,} cases in the latest observation. The lead signal is {lead_en}. "
            f"{death_note.get('en', '')}"
        ),
    )
    key_findings = {
        "zh": [
            f"病例信号总量为 {total_cases:,} 例，最新一期为 {latest_cases:,} 例。",
            f"首要关注信号为 {lead_zh}，建议先进行主动复核。",
            death_note.get("zh") or "死亡数据按来源口径单独解释。",
        ],
        "en": [
            f"The evidence packet contains {total_cases:,} cases, including {latest_cases:,} in the latest observation.",
            f"The lead signal is {lead_en}; active review is recommended before escalation.",
            death_note.get("en") or "Death data are interpreted under the source-specific reporting scope.",
        ],
    }

    sections = [
        _decision_summary(summary, death_reporting, lead, country_zh, country_en),
        _priority_actions(summary, death_reporting, lead),
        _signal_evidence(risk_ranking, summary),
        _disease_context(disease_cards),
        _data_notes(evidence_packet, death_reporting),
        _method_appendix(evidence_packet),
    ]

    metrics = {
        "record_count": summary.get("record_count"),
        "disease_count": disease_count,
        "total_cases": total_cases,
        "latest_cases": latest_cases,
        "high_risk_diseases": high_risk,
        "total_deaths": death_reporting.get("total_deaths"),
        "reporting_cadence": evidence_packet.get("reporting_cadence"),
        "data_signature": evidence_packet.get("data_signature"),
    }

    return ReportDocument(
        title=title,
        summary=summary_text,
        key_findings=key_findings,
        sections=sections,
        metrics=metrics,
        death_reporting=_death_reporting_from_dict(death_reporting),
        data_quality=data_quality,
        disease_directory=disease_directory,
        risk_ranking=risk_ranking[:20],
        figures=[],
        references=evidence_packet.get("references") or [],
        evidence_index=evidence_index,
    )


def _death_reporting_from_dict(value: dict[str, Any]):
    from .models import DeathReporting

    return DeathReporting(
        status=value.get("status") or "unknown",
        total_deaths=value.get("total_deaths"),
        observed_periods=int(value.get("observed_periods") or 0),
        missing_periods=int(value.get("missing_periods") or 0),
        reported_zero_periods=int(value.get("reported_zero_periods") or 0),
        source_policy=value.get("source_policy") or {},
        display_note=value.get("display_note") or {},
    )


def _risk_label(level: Any, lang: str) -> str:
    key = str(level or "low").lower()
    if lang == "en":
        return key
    return {"critical": "极高", "high": "高", "moderate": "中等", "low": "低"}.get(key, key)


def _fmt_number(value: Any) -> str:
    number = _num(value)
    return f"{int(number):,}" if number is not None else "—"


def _fmt_pct(value: Any, lang: str) -> str:
    number = _num(value)
    if number is None:
        return "暂无可比数据" if lang == "zh" else "not comparable"
    digits = 0 if abs(number) >= 10 else 1
    return f"{number:+.{digits}f}%"


def _fmt_ratio(value: Any, lang: str) -> str:
    number = _num(value)
    if number is None:
        return "暂无同期基线" if lang == "zh" else "no same-season baseline"
    return f"{number:.2f}x"


def _fmt_percentile(value: Any, lang: str) -> str:
    number = _num(value)
    if number is None:
        return "暂无历史分位" if lang == "zh" else "no historical percentile"
    return f"{number:.1f} 分位" if lang == "zh" else f"{number:.1f}th percentile"


def _source_scope_note(death_reporting: dict[str, Any], lang: str) -> str:
    source_policy = death_reporting.get("source_policy") or {}
    case_scope = str(source_policy.get("case_scope") or "").lower()
    if case_scope == "sentinel":
        return (
            "该来源为哨点监测口径，病例数反映哨点报告信号；不宜直接当作全国真实感染人数。"
            if lang == "zh"
            else "This source is sentinel surveillance; counts reflect reported sentinel signals and should not be read as total national infections."
        )
    if case_scope == "national":
        return (
            "该来源为全国报告口径，仍需结合报告延迟、检测量和病例定义变化解释。"
            if lang == "zh"
            else "This source uses national reporting, but reporting lag, testing volume, and case-definition changes still need review."
        )
    return (
        "病例口径按来源定义解释，趋势判断需结合分层数据复核。"
        if lang == "zh"
        else "Case scope follows the source definition; trend interpretation still requires stratified review."
    )


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _int_or_none(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _slugify(value: Any, fallback: str) -> str:
    text = str(value or fallback or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or re.sub(r"[^a-z0-9]+", "-", str(fallback).lower()).strip("-") or "disease"


def _enum_label(kind: str, value: Any, lang: str) -> str:
    key = str(value or "unknown").strip().lower()
    labels: dict[str, dict[str, dict[str, str]]] = {
        "confidence": {
            "zh": {"high": "高", "medium": "中", "low": "低", "unknown": "未知"},
            "en": {"high": "high", "medium": "medium", "low": "low", "unknown": "unknown"},
        },
        "case_scope": {
            "zh": {
                "national": "全国报告",
                "sentinel": "哨点监测",
                "national_or_sentinel": "全国或哨点监测",
                "unknown": "未说明",
            },
            "en": {
                "national": "national reporting",
                "sentinel": "sentinel surveillance",
                "national_or_sentinel": "national or sentinel surveillance",
                "unknown": "unspecified",
            },
        },
        "rate_basis": {
            "zh": {
                "source_rate": "来源提供",
                "wpp_computed_crude": "按人口估算粗率",
                "unavailable": "未提供",
                "unknown": "未说明",
            },
            "en": {
                "source_rate": "source-provided",
                "wpp_computed_crude": "population-based crude rate",
                "unavailable": "unavailable",
                "unknown": "unspecified",
            },
        },
        "cadence": {
            "zh": {
                "daily": "每日",
                "weekly": "每周",
                "monthly": "每月",
                "quarterly": "每季度",
                "yearly": "每年",
                "unknown": "按来源更新",
            },
            "en": {
                "daily": "daily",
                "weekly": "weekly",
                "monthly": "monthly",
                "quarterly": "quarterly",
                "yearly": "yearly",
                "unknown": "source-defined",
            },
        },
    }
    return labels.get(kind, {}).get(lang, {}).get(key, key if lang == "en" else "未说明")


def _trend_signal(
    *,
    latest_cases: int,
    previous_cases: int | None,
    mom_change_pct: float | None,
    recent_change_pct: float | None,
    yoy_change_pct: float | None,
    long_window_change_pct: float | None,
    historical_context: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Classify table-of-contents trend signals without letting tiny denominators dominate."""
    absolute_delta = None if previous_cases is None else latest_cases - previous_cases
    comparison_floor = max(latest_cases, previous_cases or 0)
    low_base = comparison_floor < 20 and (absolute_delta is None or abs(absolute_delta) < 10)
    historical_observations = int(historical_context.get("observation_count") or 0)
    confidence = "high" if historical_observations >= 24 else "medium" if historical_observations >= 8 else "low"
    if low_base:
        confidence = "low"

    current = _current_movement(
        latest_cases=latest_cases,
        previous_cases=previous_cases,
        absolute_delta=absolute_delta,
        mom_change_pct=mom_change_pct,
        recent_change_pct=recent_change_pct,
        low_base=low_base,
    )
    seasonal = _seasonal_position(
        yoy_change_pct=yoy_change_pct,
        same_season_ratio=_num(historical_context.get("latest_to_same_season_median_ratio")),
        same_season_count=int(historical_context.get("same_season_baseline_count") or 0),
        latest_percentile=_num(historical_context.get("latest_percentile_prior")),
    )
    long_status = _long_window_status(long_window_change_pct)
    trend = _combined_trend(current, seasonal, long_status)
    drivers = [
        current["status"],
        seasonal["status"],
        long_status,
    ]
    if low_base:
        drivers.append("low_base_guard")

    basis = {
        "latest_cases": latest_cases,
        "previous_cases": previous_cases,
        "absolute_delta": absolute_delta,
        "mom_change_pct": mom_change_pct,
        "recent_change_pct": recent_change_pct,
        "yoy_change_pct": yoy_change_pct,
        "long_window_change_pct": long_window_change_pct,
        "same_season_ratio": seasonal.get("same_season_ratio"),
        "same_season_baseline_count": seasonal.get("same_season_baseline_count"),
        "latest_percentile_prior": seasonal.get("latest_percentile_prior"),
        "low_base": low_base,
        "confidence": confidence,
        "drivers": drivers,
    }
    trend["confidence"] = confidence
    trend["status"] = trend.get("status", current["status"])
    return {
        "trend": trend,
        "current_movement": current,
        "seasonal_position": seasonal,
        "trend_basis": basis,
    }


def _current_movement(
    *,
    latest_cases: int,
    previous_cases: int | None,
    absolute_delta: int | None,
    mom_change_pct: float | None,
    recent_change_pct: float | None,
    low_base: bool,
) -> dict[str, Any]:
    if previous_cases is None:
        if latest_cases > 0:
            return {"status": "new_or_reappearing", "direction": "watch", "zh": "新发/再现", "en": "New or reappearing"}
        return {"status": "watch", "direction": "watch", "zh": "待观察", "en": "Watch"}
    if low_base:
        return {"status": "low_base_fluctuation", "direction": "watch", "zh": "低基数波动", "en": "Low-base fluctuation"}
    if mom_change_pct is None:
        return {"status": "watch", "direction": "watch", "zh": "待观察", "en": "Watch"}

    delta = abs(absolute_delta or 0)
    meaningful_delta = delta >= 5
    large_delta = delta >= 10
    recent = recent_change_pct if recent_change_pct is not None else 0
    if mom_change_pct >= 75 and large_delta:
        return {"status": "rapid_up", "direction": "up", "zh": "短期快速上升", "en": "Short-term rapid rise"}
    if (mom_change_pct >= 20 and meaningful_delta) or (recent >= 50 and large_delta):
        return {"status": "up", "direction": "up", "zh": "短期上升", "en": "Short-term rise"}
    if mom_change_pct <= -75 and large_delta:
        return {"status": "rapid_down", "direction": "down", "zh": "短期快速下降", "en": "Short-term rapid decline"}
    if (mom_change_pct <= -20 and meaningful_delta) or (recent <= -50 and large_delta):
        return {"status": "down", "direction": "down", "zh": "短期下降", "en": "Short-term decline"}
    return {"status": "stable", "direction": "stable", "zh": "短期平稳", "en": "Short-term stable"}


def _seasonal_position(
    *,
    yoy_change_pct: float | None,
    same_season_ratio: float | None,
    same_season_count: int,
    latest_percentile: float | None,
) -> dict[str, Any]:
    status = "typical"
    direction = "stable"
    zh = "同期正常"
    en = "Seasonally typical"
    if same_season_count >= 2 and same_season_ratio is not None:
        if same_season_ratio >= 2.0:
            status, direction, zh, en = "very_high", "up", "显著高于同期", "Well above same-season baseline"
        elif same_season_ratio >= 1.25:
            status, direction, zh, en = "high", "up", "高于同期", "Above same-season baseline"
        elif same_season_ratio <= 0.5:
            status, direction, zh, en = "very_low", "down", "显著低于同期", "Well below same-season baseline"
        elif same_season_ratio <= 0.8:
            status, direction, zh, en = "low", "down", "低于同期", "Below same-season baseline"
    elif yoy_change_pct is not None:
        if yoy_change_pct >= 100:
            status, direction, zh, en = "very_high", "up", "同比显著偏高", "YoY well above"
        elif yoy_change_pct >= 25:
            status, direction, zh, en = "high", "up", "同比偏高", "YoY above"
        elif yoy_change_pct <= -50:
            status, direction, zh, en = "very_low", "down", "同比显著偏低", "YoY well below"
        elif yoy_change_pct <= -25:
            status, direction, zh, en = "low", "down", "同比偏低", "YoY below"

    if latest_percentile is not None and latest_percentile >= 90 and status == "typical":
        status, direction, zh, en = "historically_high", "up", "处于历史高位", "Historically high"

    return {
        "status": status,
        "direction": direction,
        "zh": zh,
        "en": en,
        "yoy_change_pct": yoy_change_pct,
        "same_season_ratio": same_season_ratio,
        "same_season_baseline_count": same_season_count,
        "latest_percentile_prior": latest_percentile,
    }


def _long_window_status(long_window_change_pct: float | None) -> str:
    if long_window_change_pct is None:
        return "long_window_unknown"
    if long_window_change_pct >= 25:
        return "long_window_up"
    if long_window_change_pct <= -25:
        return "long_window_down"
    return "long_window_neutral"


def _combined_trend(current: dict[str, Any], seasonal: dict[str, Any], long_status: str) -> dict[str, Any]:
    current_direction = current.get("direction")
    seasonal_status = seasonal.get("status")
    season_high = seasonal_status in {"high", "very_high", "historically_high"}
    season_low = seasonal_status in {"low", "very_low"}
    if current.get("status") == "low_base_fluctuation":
        return {"zh": "低基数波动", "en": "Low-base fluctuation", "direction": "watch", "status": "low_base_fluctuation"}
    if current.get("status") == "new_or_reappearing":
        return {"zh": "新发/再现", "en": "New or reappearing", "direction": "watch", "status": "new_or_reappearing"}

    if current_direction == "up":
        if season_low or long_status == "long_window_down":
            return {"zh": "短期反弹", "en": "Short-term rebound", "direction": "up", "status": "short_term_rebound"}
        if season_high:
            return {"zh": "高位上升", "en": "Rising from elevated baseline", "direction": "up", "status": "elevated_rise"}
        return {"zh": current["zh"], "en": current["en"], "direction": "up", "status": current["status"]}

    if current_direction == "down":
        if season_high:
            return {"zh": "高位回落", "en": "Declining from elevated baseline", "direction": "down", "status": "elevated_decline"}
        return {"zh": current["zh"], "en": current["en"], "direction": "down", "status": current["status"]}

    if season_high or long_status == "long_window_up":
        return {"zh": "同期偏高", "en": "Above seasonal baseline", "direction": "stable", "status": "seasonally_elevated"}
    if season_low or long_status == "long_window_down":
        return {"zh": "同期偏低", "en": "Below seasonal baseline", "direction": "stable", "status": "seasonally_low"}
    if current_direction == "watch":
        return {"zh": "待观察", "en": "Watch", "direction": "watch", "status": "watch"}
    return {"zh": "平稳", "en": "Stable", "direction": "stable", "status": "stable"}


def _disease_analysis_sections(
    *,
    disease_id: str,
    name_zh: str,
    name_en: str,
    latest_cases: int,
    previous_cases: int | None,
    total_cases: int,
    mom_change_pct: float | None,
    yoy_change_pct: float | None,
    recent_change_pct: float | None,
    long_window_change_pct: float | None,
    trend_signal: dict[str, Any],
    risk_level: Any,
    risk_score: Any,
    death_reporting: dict[str, Any],
) -> list[dict[str, Any]]:
    trend = trend_signal.get("trend") or {}
    current = trend_signal.get("current_movement") or {}
    seasonal = trend_signal.get("seasonal_position") or {}
    basis = trend_signal.get("trend_basis") or {}
    absolute_delta = basis.get("absolute_delta")
    latest = _fmt_number(latest_cases)
    previous = _fmt_number(previous_cases)
    total = _fmt_number(total_cases)
    delta_zh = "暂无可比差值" if absolute_delta is None else f"{int(absolute_delta):+,} 例"
    delta_en = "not comparable" if absolute_delta is None else f"{int(absolute_delta):+,} cases"
    mom_zh = _fmt_pct(mom_change_pct, "zh")
    mom_en = _fmt_pct(mom_change_pct, "en")
    yoy_zh = _fmt_pct(yoy_change_pct, "zh")
    yoy_en = _fmt_pct(yoy_change_pct, "en")
    recent_zh = _fmt_pct(recent_change_pct, "zh")
    recent_en = _fmt_pct(recent_change_pct, "en")
    long_zh = _fmt_pct(long_window_change_pct, "zh")
    long_en = _fmt_pct(long_window_change_pct, "en")
    has_recent = _num(recent_change_pct) is not None
    recent_summary_zh = (
        f"近4期相较前4期变化 {recent_zh}，说明信号不是只有单期小幅抖动。"
        if has_recent
        else "近4期相较前4期暂缺可比数据，短窗斜率需要后续观测补强。"
    )
    recent_summary_en = (
        f"The latest four observations changed {recent_en} versus the prior four, so this is not just a one-period twitch."
        if has_recent
        else "The latest four observations cannot yet be compared reliably with the prior four, so the short-window slope needs follow-up observations."
    )
    recent_trend_zh = (
        f"二是近4期相较前4期 {recent_zh}，提示近期斜率明显变陡；"
        if has_recent
        else "二是近4期暂缺可比背景，不能用短窗斜率单独确认；"
    )
    recent_trend_en = (
        f"Second, the latest four observations changed {recent_en} versus the prior four, indicating a steeper recent slope."
        if has_recent
        else "Second, the latest four observations do not yet have a reliable prior-four comparison, so the short-window slope cannot confirm the signal by itself."
    )
    same_season_zh = _fmt_ratio(seasonal.get("same_season_ratio"), "zh")
    same_season_en = _fmt_ratio(seasonal.get("same_season_ratio"), "en")
    percentile_zh = _fmt_percentile(seasonal.get("latest_percentile_prior"), "zh")
    percentile_en = _fmt_percentile(seasonal.get("latest_percentile_prior"), "en")
    baseline_count = _fmt_number(seasonal.get("same_season_baseline_count"))
    trend_zh = trend.get("zh") or "待观察"
    trend_en = trend.get("en") or "Watch"
    current_zh = current.get("zh") or trend_zh
    current_en = current.get("en") or trend_en
    seasonal_zh = seasonal.get("zh") or "暂无同期基线"
    seasonal_en = seasonal.get("en") or "No same-season baseline"
    risk_zh = _risk_label(risk_level, "zh")
    risk_en = _risk_label(risk_level, "en")
    score = _fmt_number(risk_score)
    death_note_zh = (death_reporting.get("display_note") or {}).get("zh") or "死亡数据按来源口径解释。"
    death_note_en = (death_reporting.get("display_note") or {}).get("en") or "Death data are interpreted under the source scope."
    source_note_zh = _source_scope_note(death_reporting, "zh")
    source_note_en = _source_scope_note(death_reporting, "en")
    percentile = _num(seasonal.get("latest_percentile_prior"))
    historical_zh = (
        "同时处于历史高分位，应优先排查是否进入异常高位。"
        if percentile is not None and percentile >= 90
        else "但历史分位并未显示极端异常，更像是季节性上升通道中的快速抬升，需要连续观察确认。"
    )
    historical_en = (
        "It is also in a high historical percentile, so an unusually elevated signal should be ruled out first."
        if percentile is not None and percentile >= 90
        else "The historical percentile is not extreme, so this reads more like a rapid rise within a seasonal upswing that needs confirmation over subsequent observations."
    )

    return [
        {
            "section_type": "summary",
            "title": "摘要",
            "content": f"{name_zh} 在本报告窗口累计 {total} 例，最新一期报告 {latest} 例；较上一期的 {previous} 例增加 {delta_zh}，环比 {mom_zh}。综合判定为{trend_zh}，风险等级为{risk_zh}（{score}）。",
            "title_i18n": {"zh": "摘要", "en": "Summary"},
            "content_i18n": {
                "zh": f"{name_zh} 在本报告窗口累计 {total} 例，最新一期报告 {latest} 例；较上一期的 {previous} 例增加 {delta_zh}，环比 {mom_zh}。{recent_summary_zh}综合判定为{trend_zh}，风险等级为{risk_zh}（{score}）。",
                "en": f"{name_en} recorded {total} cases in this reporting window. The latest observation reported {latest} cases, {delta_en} versus {previous} in the previous observation ({mom_en}). {recent_summary_en} The combined judgement is {trend_en}, with {risk_en} risk ({score}).",
            },
            "evidence_refs": [f"disease:{disease_id}.latest_cases", f"disease:{disease_id}.change_pct"],
        },
        {
            "section_type": "highlights",
            "title": "要点",
            "content": "\n".join(
                [
                    f"- 当前移动：{current_zh}；最新 {latest} 例，较上一期 {delta_zh}，环比 {mom_zh}",
                    f"- 近期动量：近4期相较前4期 {recent_zh}，长窗口变化 {long_zh}",
                    f"- 同期背景：{seasonal_zh}；同期中位数比 {same_season_zh}，可用同期基线 {baseline_count} 个",
                    f"- 历史位置：最新值处于既往观测 {percentile_zh}",
                    f"- 口径限制：{source_note_zh} {death_note_zh}",
                ]
            ),
            "title_i18n": {"zh": "要点", "en": "Highlights"},
            "content_i18n": {
                "zh": "\n".join(
                    [
                        f"- 当前移动：{current_zh}；最新 {latest} 例，较上一期 {delta_zh}，环比 {mom_zh}",
                        f"- 近期动量：近4期相较前4期 {recent_zh}，长窗口变化 {long_zh}",
                        f"- 同期背景：{seasonal_zh}；同期中位数比 {same_season_zh}，可用同期基线 {baseline_count} 个",
                        f"- 历史位置：最新值处于既往观测 {percentile_zh}",
                        f"- 口径限制：{source_note_zh} {death_note_zh}",
                    ]
                ),
                "en": "\n".join(
                    [
                        f"- Current movement: {current_en}; latest {latest} cases, {delta_en} versus the previous observation ({mom_en})",
                        f"- Recent momentum: latest four observations versus prior four {recent_en}; long-window change {long_en}",
                        f"- Seasonal context: {seasonal_en}; same-season median ratio {same_season_en}, based on {baseline_count} same-season baselines",
                        f"- Historical position: latest value is at the {percentile_en} of prior observations",
                        f"- Scope limit: {source_note_en} {death_note_en}",
                    ]
                ),
            },
            "evidence_refs": [f"disease:{disease_id}.last4_change_pct", f"disease:{disease_id}.latest_percentile_prior"],
        },
        {
            "section_type": "key_findings",
            "title": "关键发现",
            "content": "\n".join(
                [
                    f"- 短期上升有实际病例量支撑：最新一期比上一期多 {delta_zh}，不是低基数百分比造成的假信号。",
                    f"- 同期背景提示{seasonal_zh}，同比 {yoy_zh}、同期中位数比 {same_season_zh}；{historical_zh}",
                    f"- 风险等级为{risk_zh}，但不应仅凭该信号直接按事件暴发定性；建议进入连续复核队列。",
                    f"- {source_note_zh}",
                ]
            ),
            "title_i18n": {"zh": "关键发现", "en": "Key findings"},
            "content_i18n": {
                "zh": "\n".join(
                    [
                        f"- 短期上升有实际病例量支撑：最新一期比上一期多 {delta_zh}，不是低基数百分比造成的假信号。",
                        f"- 同期背景提示{seasonal_zh}，同比 {yoy_zh}、同期中位数比 {same_season_zh}；{historical_zh}",
                        f"- 风险等级为{risk_zh}，但不应仅凭该信号直接按事件暴发定性；建议进入连续复核队列。",
                        f"- {source_note_zh}",
                    ]
                ),
                "en": "\n".join(
                    [
                        f"- The short-term rise has case-volume support: the latest observation is {delta_en} above the previous observation, not merely a low-denominator percentage artifact.",
                        f"- Seasonal context is {seasonal_en}: YoY {yoy_en}, same-season median ratio {same_season_en}. {historical_en}",
                        f"- Risk is {risk_en}, but this signal alone should not be classified as an event-level outbreak; keep it in active review.",
                        f"- {source_note_en}",
                    ]
                ),
            },
            "evidence_refs": [f"disease:{disease_id}.yoy_change_pct"],
        },
        {
            "section_type": "trend_analysis",
            "title": "趋势分析",
            "content": f"趋势判断：{trend_zh}。现有证据包括：一是最新一期相较上一期增加 {delta_zh}；{recent_trend_zh}三是长窗口为 {long_zh}，用于判断过去一段时间的背景是否同步变化。同期比较为 {same_season_zh}，历史位置为 {percentile_zh}，因此应把它解释为需要连续确认的季节性上升/高位上升信号。下一步应复核地区分布、年龄组、哨点数或报告完整性，以及是否存在学校/托幼机构聚集线索。",
            "title_i18n": {"zh": "趋势分析", "en": "Trend analysis"},
            "content_i18n": {
                "zh": f"趋势判断：{trend_zh}。现有证据包括：一是最新一期相较上一期增加 {delta_zh}；{recent_trend_zh}三是长窗口为 {long_zh}，用于判断过去一段时间的背景是否同步变化。同期比较为 {same_season_zh}，历史位置为 {percentile_zh}，因此应把它解释为需要连续确认的季节性上升/高位上升信号。下一步应复核地区分布、年龄组、哨点数或报告完整性，以及是否存在学校/托幼机构聚集线索。",
                "en": f"Trend judgement: {trend_en}. The available evidence includes: first, the latest observation is {delta_en} above the previous one. {recent_trend_en} Third, the long-window change is {long_en}, which helps judge whether the background is moving in the same direction. With a same-season ratio of {same_season_en} and historical position at the {percentile_en}, this should be treated as a seasonal/elevated rise that needs confirmation. Review geographic distribution, age groups, sentinel coverage or reporting completeness, and any school or childcare-cluster signals next.",
            },
            "evidence_refs": [f"disease:{disease_id}.last4_change_pct", f"disease:{disease_id}.latest_to_same_season_median_ratio"],
        },
    ]


def _disease_directory(diseases: list[dict[str, Any]], ranking: list[dict[str, Any]], death_reporting: dict[str, Any]) -> list[DiseaseDirectoryItem]:
    ranked_by_id = {str(row.get("disease_id")): row for row in ranking if row.get("disease_id")}
    rows: list[DiseaseDirectoryItem] = []
    for item in diseases:
        disease_id = str(item.get("disease_id") or "")
        if not disease_id:
            continue
        metrics = item.get("metrics") or {}
        visual = item.get("visual_diagnostics") or {}
        history = item.get("historical_context") or {}
        risk = item.get("risk") or {}
        ranked = ranked_by_id.get(disease_id) or {}

        change_pct = _num(metrics.get("change_pct"))
        yoy_change_pct = _num(metrics.get("yoy_change_pct"))
        recent_change_pct = _num(visual.get("last4_change_pct"))
        long_window_change_pct = _num(history.get("long_window_change_pct"))
        latest_cases = _int_or_none(metrics.get("latest_cases")) or 0
        previous_cases = _int_or_none(metrics.get("previous_cases"))
        total_cases = _int_or_none(metrics.get("total_cases")) or 0
        risk_score = risk.get("score", ranked.get("risk_score"))
        risk_level = risk.get("level", ranked.get("risk_level"))
        name_en = str(item.get("name_en") or disease_id)
        name_zh = str(item.get("name_zh") or name_en)
        trend_signal = _trend_signal(
            latest_cases=latest_cases,
            previous_cases=previous_cases,
            mom_change_pct=change_pct,
            recent_change_pct=recent_change_pct,
            yoy_change_pct=yoy_change_pct,
            long_window_change_pct=long_window_change_pct,
            historical_context=history,
        )

        rows.append(
            DiseaseDirectoryItem(
                disease_id=disease_id,
                slug=_slugify(name_en, disease_id),
                name_zh=name_zh,
                name_en=name_en,
                category=item.get("category"),
                latest_cases=latest_cases,
                previous_cases=previous_cases,
                total_cases=total_cases,
                mom_change_pct=change_pct,
                yoy_change_pct=yoy_change_pct,
                recent_change_pct=recent_change_pct,
                long_window_change_pct=long_window_change_pct,
                trend=trend_signal["trend"],
                risk_score=risk_score,
                risk_level=risk_level,
                current_movement=trend_signal["current_movement"],
                seasonal_position=trend_signal["seasonal_position"],
                trend_basis=trend_signal["trend_basis"],
                analysis_sections=_disease_analysis_sections(
                    disease_id=disease_id,
                    name_zh=name_zh,
                    name_en=name_en,
                    latest_cases=latest_cases,
                    previous_cases=previous_cases,
                    total_cases=total_cases,
                    mom_change_pct=change_pct,
                    yoy_change_pct=yoy_change_pct,
                    recent_change_pct=recent_change_pct,
                    long_window_change_pct=long_window_change_pct,
                    trend_signal=trend_signal,
                    risk_level=risk_level,
                    risk_score=risk_score,
                    death_reporting=death_reporting,
                ),
                evidence_refs=[
                    f"disease:{disease_id}.latest_cases",
                    f"disease:{disease_id}.change_pct",
                    f"disease:{disease_id}.yoy_change_pct",
                    f"disease:{disease_id}.last4_change_pct",
                ],
            )
        )

    return sorted(
        rows,
        key=lambda row: (float(row.risk_score or 0), row.latest_cases, row.total_cases),
        reverse=True,
    )


def _decision_summary(summary: dict[str, Any], death_reporting: dict[str, Any], lead: dict[str, Any], country_zh: str, country_en: str) -> Section:
    refs = ["summary:total_cases", "summary:latest_cases"]
    lead_zh = lead.get("name_zh") or lead.get("name_en") or "暂无首要信号"
    lead_en = lead.get("name_en") or lead.get("name_zh") or "No lead signal"
    zh = "\n".join(
        [
            f"- 当前判断：{country_zh}本期以病例信号复核为主，首要关注 {lead_zh}。",
            f"- 影响范围：共纳入 {int(summary.get('disease_count') or 0)} 个疾病信号，累计病例 {int(summary.get('total_cases') or 0):,} 例。",
            f"- 死亡口径：{(death_reporting.get('display_note') or {}).get('zh', '死亡数据按来源口径解释。')}",
        ]
    )
    en = "\n".join(
        [
            f"- Current judgement: {country_en} should treat {lead_en} as the lead signal for active review.",
            f"- Scope: {int(summary.get('disease_count') or 0)} disease signals and {int(summary.get('total_cases') or 0):,} cases are included.",
            f"- Death-count scope: {(death_reporting.get('display_note') or {}).get('en', 'Death data are interpreted under the source scope.')}",
        ]
    )
    return Section("decision_summary", "decision_summary", 1, LocalizedText("当前判断", "Current Judgement"), LocalizedText(zh, en), refs)


def _priority_actions(summary: dict[str, Any], death_reporting: dict[str, Any], lead: dict[str, Any]) -> Section:
    lead_zh = lead.get("name_zh") or lead.get("name_en") or "首要信号"
    lead_en = lead.get("name_en") or lead.get("name_zh") or "the lead signal"
    zh = "\n".join(
        [
            f"- 主动复核：优先确认 {lead_zh} 是否持续上升、是否集中在特定地区或人群。",
            "- 补充分层：优先补齐地区、年龄组、机构来源和检测口径，以判断是否需要升级处置。",
            "- 升级条件：若连续期次上升、出现重症线索、或外部事件报告相互印证，再进入事件级响应。",
            f"- 死亡数据：{(death_reporting.get('display_note') or {}).get('zh', '死亡数据需单独核验。')}",
        ]
    )
    en = "\n".join(
        [
            f"- Active review: confirm whether {lead_en} is persistent and concentrated by place or population group.",
            "- Add stratification: prioritize place, age group, reporting source, and testing scope before escalation.",
            "- Escalate when: repeated increases, severity signals, or external event reports corroborate the signal.",
            f"- Death data: {(death_reporting.get('display_note') or {}).get('en', 'Death data require separate verification.')}",
        ]
    )
    return Section("priority_actions", "priority_actions", 2, LocalizedText("建议动作", "Priority Actions"), LocalizedText(zh, en), ["summary:high_risk_diseases"])


def _signal_evidence(ranking: list[dict[str, Any]], summary: dict[str, Any]) -> Section:
    top_rows = ranking[:5]
    if not top_rows:
        zh_rows = ["- 当前窗口没有可排序的疾病信号。"]
        en_rows = ["- No rankable disease signal is available in this reporting window."]
    else:
        zh_rows = [
            f"- {row.get('name_zh') or row.get('name_en')}: 最新病例 {int(row.get('latest_cases') or 0):,} 例，风险等级 {_risk_label(row.get('risk_level'), 'zh')}。"
            for row in top_rows
        ]
        en_rows = [
            f"- {row.get('name_en') or row.get('name_zh')}: {int(row.get('latest_cases') or 0):,} latest cases, {_risk_label(row.get('risk_level'), 'en')} priority."
            for row in top_rows
        ]
    zh = "\n".join([f"- 最新一期总病例 {int(summary.get('latest_cases') or 0):,} 例。", *zh_rows])
    en = "\n".join([f"- The latest observation contains {int(summary.get('latest_cases') or 0):,} cases.", *en_rows])
    refs = ["summary:latest_cases", *[f"disease:{row.get('disease_id')}.latest_cases" for row in top_rows if row.get("disease_id")]]
    return Section("signal_evidence", "signal_evidence", 3, LocalizedText("关键证据", "Signal Evidence"), LocalizedText(zh, en), refs)


def _disease_context(diseases: list[dict[str, Any]]) -> Section:
    top = diseases[:5]
    if not top:
        zh = "- 当前没有疾病卡片可用于背景解释。"
        en = "- No disease cards are available for contextual interpretation."
    else:
        zh = "\n".join(
            f"- {item.get('name_zh') or item.get('name_en')}: 累计病例 {int((item.get('metrics') or {}).get('total_cases') or 0):,} 例；建议结合传播途径、季节性和重点人群继续解读。"
            for item in top
        )
        en = "\n".join(
            f"- {item.get('name_en') or item.get('name_zh')}: {int((item.get('metrics') or {}).get('total_cases') or 0):,} cumulative cases; interpret with transmission route, seasonality, and risk groups."
            for item in top
        )
    refs = [f"disease:{item.get('disease_id')}.total_cases" for item in top if item.get("disease_id")]
    return Section("disease_context", "disease_context", 4, LocalizedText("疾病背景", "Disease Context"), LocalizedText(zh, en), refs)


def _data_notes(packet: dict[str, Any], death_reporting: dict[str, Any]) -> Section:
    quality = packet.get("data_quality") or {}
    source_policy = death_reporting.get("source_policy") or {}
    confidence_zh = _enum_label("confidence", quality.get("confidence"), "zh")
    confidence_en = _enum_label("confidence", quality.get("confidence"), "en")
    case_scope_zh = _enum_label("case_scope", source_policy.get("case_scope"), "zh")
    case_scope_en = _enum_label("case_scope", source_policy.get("case_scope"), "en")
    rate_basis_zh = _enum_label("rate_basis", source_policy.get("rate_basis"), "zh")
    rate_basis_en = _enum_label("rate_basis", source_policy.get("rate_basis"), "en")
    score = quality.get("score")
    score_zh = f"{score}" if score is not None else "暂无评分"
    score_en = f"{score}" if score is not None else "not scored"
    zh = "\n".join(
        [
            f"- 数据置信度：{confidence_zh}，质量分 {score_zh}。",
            f"- 病例口径：{case_scope_zh}；发病率口径：{rate_basis_zh}。",
            f"- 死亡口径：{(death_reporting.get('display_note') or {}).get('zh', '死亡数据口径不明确。')}",
            "- 解读限制：病例上升不等同于暴发确认，需要结合检测量、报告延迟和分层分布复核。",
        ]
    )
    en = "\n".join(
        [
            f"- Data confidence: {confidence_en}, quality score {score_en}.",
            f"- Case scope: {case_scope_en}; rate basis: {rate_basis_en}.",
            f"- Death-count scope: {(death_reporting.get('display_note') or {}).get('en', 'Death-count scope is unclear.')}",
            "- Interpretation limit: increasing cases do not confirm an outbreak without testing volume, reporting lag, and stratified distribution review.",
        ]
    )
    return Section("data_interpretation_notes", "data_interpretation_notes", 5, LocalizedText("数据口径", "Data Notes"), LocalizedText(zh, en), ["quality:score"])


def _method_appendix(packet: dict[str, Any]) -> Section:
    signature = packet.get("data_signature") or "N/A"
    cadence = packet.get("reporting_cadence") or "unknown"
    cadence_zh = _enum_label("cadence", cadence, "zh")
    cadence_en = _enum_label("cadence", cadence, "en")
    zh = "\n".join(
        [
            "- 本附录用于审计追溯，不作为主阅读路径。",
            f"- 数据签名：`{signature}`",
            "- 计算版本：第四版报告引擎",
            f"- 报告频率：{cadence_zh}",
        ]
    )
    en = "\n".join(
        [
            "- This appendix supports audit tracing and is not the primary reading path.",
            f"- Data signature: `{signature}`",
            f"- Calculation version: `report_v4.0`",
            f"- Reporting cadence: {cadence_en}",
        ]
    )
    return Section("method_appendix", "method_appendix", 6, LocalizedText("方法附录", "Method Appendix"), LocalizedText(zh, en), ["summary:record_count"])
