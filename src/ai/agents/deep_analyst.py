"""Deep analytical interpretation agent for v3 reports."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from src.core import get_logger

from .base import BaseAgent

logger = get_logger(__name__)


class DeepAnalystAgent(BaseAgent):
    """Interpret a deterministic evidence packet without inventing facts."""

    def __init__(self):
        super().__init__(
            name="DeepAnalyst",
            temperature=0.25,
            max_tokens=1800,
        )

    async def process(
        self,
        evidence_packet: Dict[str, Any],
        language: str = "en",
        **kwargs,
    ) -> Dict[str, Any]:
        compact_packet = self._compact_packet(evidence_packet)
        prompt = (
            "You are a senior infectious-disease surveillance analyst. "
            "Interpret the deterministic evidence packet for a clear situation brief read by public-health decision makers and business stakeholders.\n\n"
            "Rules:\n"
            "- Do not invent numbers, dates, countries, diseases, causes, or recommendations.\n"
            "- Every insight must include evidence_refs copied from the packet.\n"
            "- Hypotheses must be clearly labeled as hypotheses and include remaining_uncertainties.\n"
            "- Prefer plain-language implications and next-watch framing over methodology-heavy wording.\n"
            "- You may propose figure_plan items from this whitelist only: epidemic_curve, cases_incidence_panel, recent_window_heatmap, risk_ranking_bar, signal_context_panel, seasonal_baseline_band, anomaly_marker_curve, risk_matrix.\n"
            "- Choose figures sparingly: 2-3 figures is typical, 4 is the maximum. Do not list every possible chart.\n"
            "- Include a figure only when it directly supports a specific judgement in the report; otherwise omit it.\n"
            "- Each figure_plan item must include figure_type, section_type, disease_id when disease-specific, position, and rationale.\n"
            "- Allowed section_type values for figures: priority_signals, trend_anomaly_analysis, disease_profiles, data_quality_limitations.\n"
            "- Return JSON only with keys: insights, hypotheses, open_questions, figure_plan, confidence.\n"
            f"- Text values must be in {'Chinese' if language == 'zh' else 'English'}.\n\n"
            f"Evidence packet:\n{json.dumps(compact_packet, ensure_ascii=False, indent=2)}"
        )
        system = "You only interpret supplied surveillance evidence. Output valid JSON only."

        try:
            response = await self.complete(prompt=prompt, system=system)
            parsed = self._parse_json(response)
            return self._normalize(parsed)
        except Exception as exc:
            logger.warning("Deep analysis failed; using deterministic fallback: {}", exc)
            return self.fallback(evidence_packet, language=language, reason=str(exc))

    @staticmethod
    def fallback(evidence_packet: Dict[str, Any], language: str = "en", reason: str = "") -> Dict[str, Any]:
        ranking = evidence_packet.get("risk_ranking") or []
        summary = evidence_packet.get("summary_metrics") or {}
        if language == "zh":
            text = (
                f"报告期内覆盖 {summary.get('disease_count', 0)} 种疾病，"
                f"累计 {summary.get('total_cases', 0):,} 例病例；"
                f"高风险疾病 {summary.get('high_risk_diseases', 0)} 种。"
            )
        else:
            disease_word = "disease" if summary.get("disease_count", 0) == 1 else "diseases"
            high_risk = int(summary.get("high_risk_diseases", 0) or 0)
            text = (
                f"The report covers {summary.get('disease_count', 0)} {disease_word}, "
                f"{summary.get('total_cases', 0):,} cumulative cases, and "
                f"{high_risk} high-risk {'disease' if high_risk == 1 else 'diseases'}."
            )
        refs = ["summary:total_cases", "summary:high_risk_diseases"]
        if ranking:
            refs.extend(ranking[0].get("evidence_refs") or [])
        return {
            "insights": [
                {
                    "title": "Surveillance summary" if language != "zh" else "监测摘要",
                    "interpretation": text,
                    "evidence_refs": refs,
                    "confidence": 0.82,
                }
            ],
            "hypotheses": [],
            "open_questions": [],
            "confidence": 0.82,
            "fallback_reason": reason,
        }

    @staticmethod
    def _compact_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "method_version": packet.get("method_version"),
            "country": packet.get("country"),
            "period": packet.get("period"),
            "reporting_cadence": packet.get("reporting_cadence"),
            "summary_metrics": packet.get("summary_metrics"),
            "risk_ranking": (packet.get("risk_ranking") or [])[:8],
            "data_quality": packet.get("data_quality"),
            "diseases": [
                {
                    "disease_id": item.get("disease_id"),
                    "name_en": item.get("name_en"),
                    "name_zh": item.get("name_zh"),
                    "metrics": item.get("metrics"),
                    "trend": item.get("trend"),
                    "anomaly": item.get("anomaly"),
                    "historical_context": item.get("historical_context"),
                    "visual_diagnostics": {
                        key: value
                        for key, value in (item.get("visual_diagnostics") or {}).items()
                        if key != "series"
                    },
                    "risk": item.get("risk"),
                    "limitations": item.get("limitations"),
                    "knowledge_context": item.get("knowledge_context"),
                    "evidence_id": item.get("evidence_id"),
                }
                for item in (packet.get("diseases") or [])[:12]
            ],
        }

    @staticmethod
    def _parse_json(response: str) -> Dict[str, Any]:
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response or "")
            if not match:
                raise
            return json.loads(match.group(0))

    @staticmethod
    def _normalize(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        insights = payload.get("insights")
        if not isinstance(insights, list):
            insights = []
        normalized_insights: List[Dict[str, Any]] = []
        for item in insights[:8]:
            if not isinstance(item, dict):
                continue
            refs = item.get("evidence_refs")
            if not isinstance(refs, list):
                refs = []
            interpretation = (
                item.get("interpretation")
                or item.get("summary")
                or item.get("text")
                or item.get("description")
                or item.get("conclusion")
                or ""
            )
            normalized_insights.append(
                {
                    "title": str(item.get("title") or "Insight"),
                    "interpretation": str(interpretation),
                    "evidence_refs": [str(ref) for ref in refs if str(ref).strip()][:8],
                    "confidence": _clamp_float(item.get("confidence"), default=0.7),
                }
            )
        return {
            "insights": normalized_insights,
            "hypotheses": _normalize_text_items(payload.get("hypotheses"))[:5],
            "open_questions": _normalize_text_items(payload.get("open_questions"))[:5],
            "figure_plan": _normalize_figure_plan(payload.get("figure_plan")),
            "confidence": _clamp_float(payload.get("confidence"), default=0.75),
        }


def _ensure_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _normalize_text_items(value: Any) -> List[str]:
    items = _ensure_list(value)
    normalized: List[str] = []
    for item in items:
        if isinstance(item, dict):
            text = (
                item.get("text")
                or item.get("question")
                or item.get("hypothesis")
                or item.get("summary")
                or item.get("interpretation")
                or item.get("title")
            )
            if text:
                normalized.append(str(text))
            continue
        if item is not None:
            text = str(item).strip()
            if text:
                normalized.append(text)
    return normalized


def _clamp_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return round(max(0.0, min(1.0, parsed)), 3)


def _normalize_figure_plan(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed_figures = {
        "epidemic_curve",
        "cases_incidence_panel",
        "recent_window_heatmap",
        "risk_ranking_bar",
        "signal_context_panel",
        "seasonal_baseline_band",
        "anomaly_marker_curve",
        "risk_matrix",
    }
    allowed_sections = {"priority_signals", "trend_anomaly_analysis", "disease_profiles", "data_quality_limitations"}
    normalized: List[Dict[str, Any]] = []
    for item in value[:4]:
        if not isinstance(item, dict):
            continue
        figure_type = str(item.get("figure_type") or "").strip()
        section_type = str(item.get("section_type") or "").strip()
        if figure_type not in allowed_figures or section_type not in allowed_sections:
            continue
        normalized.append(
            {
                "figure_type": figure_type,
                "section_type": section_type,
                "disease_id": str(item.get("disease_id")) if item.get("disease_id") is not None else None,
                "position": str(item.get("position") or "after_content"),
                "rationale": str(item.get("rationale") or ""),
            }
        )
    return normalized
