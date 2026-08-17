"""Figure planning and shared ECharts data specs for analytical v3 reports."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from src.core import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReportFigureSpec:
    """A validated figure request selected from the report figure library."""

    figure_type: str
    section_type: str
    disease_id: Optional[str] = None
    position: str = "after_content"
    rationale: str = ""
    source: str = "rules"

    def key(self) -> tuple[str, str, Optional[str]]:
        return (self.section_type, self.figure_type, self.disease_id)

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "figure_type": self.figure_type,
            "section_type": self.section_type,
            "disease_id": self.disease_id,
            "position": self.position,
            "rationale": self.rationale,
            "source": self.source,
        }


class ReportFigureLibrary:
    """Whitelist-backed figure planner and shared-data builder for v3 reports."""

    ALLOWED_FIGURES = {
        "epidemic_curve",
        "cases_incidence_panel",
        "recent_window_heatmap",
        "risk_ranking_bar",
        "signal_context_panel",
        "seasonal_baseline_band",
        "anomaly_marker_curve",
        "risk_matrix",
    }
    ALLOWED_SECTIONS = {
        "priority_signals",
        "trend_anomaly_analysis",
        "disease_profiles",
        "data_quality_limitations",
    }
    ALLOWED_POSITIONS = {"before_content", "after_content"}

    def plan_figures(
        self,
        *,
        packet: Dict[str, Any],
        deep_analysis: Optional[Dict[str, Any]] = None,
        language: str = "en",
        max_figures: int = 4,
    ) -> List[ReportFigureSpec]:
        """Build a small, validated figure plan from AI selections plus rules."""

        specs: List[ReportFigureSpec] = []
        seen: set[tuple[str, str, Optional[str]]] = set()

        for spec in self._ai_specs(packet, deep_analysis or {}):
            if self._can_render(packet, spec) and spec.key() not in seen:
                specs.append(spec)
                seen.add(spec.key())

        for spec in self._default_specs(packet, language):
            if self._can_render(packet, spec) and spec.key() not in seen:
                specs.append(spec)
                seen.add(spec.key())

        return specs[:max_figures]

    def render_figures(
        self,
        *,
        packet: Dict[str, Any],
        specs: List[ReportFigureSpec],
        language: str = "en",
    ) -> List[Dict[str, Any]]:
        """Render validated specs into compact figure payloads.

        Figures intentionally do not carry full ECharts options.  They reference
        shared data keys so generated reports can reuse one JSON series across
        multiple visualizations.
        """

        figures: List[Dict[str, Any]] = []
        for spec in specs:
            if not self._can_render(packet, spec):
                continue
            title, caption, legend, evidence_refs = self._figure_text(packet, spec, language)
            payload = spec.to_metadata()
            payload.update(
                {
                    "id": f"fig-{len(figures) + 1}-{spec.figure_type}",
                    "number": len(figures) + 1,
                    "renderer": "echarts",
                    "height": self._height_for(packet, spec),
                    "data_key": self._data_key(spec),
                    "title": title,
                    "caption": caption,
                    "legend": legend,
                    "evidence_refs": evidence_refs,
                }
            )
            figures.append(payload)
        return figures

    def build_figure_data(
        self,
        *,
        packet: Dict[str, Any],
        figures: List[Dict[str, Any]],
        language: str = "en",
    ) -> Dict[str, Any]:
        """Build de-duplicated chart data referenced by figure payloads."""

        required_diseases = {
            str(figure.get("disease_id"))
            for figure in figures
            if figure.get("disease_id")
        }
        series: Dict[str, Dict[str, Any]] = {}
        for disease_id in sorted(required_diseases):
            disease = self._find_disease(packet, disease_id)
            if not disease:
                continue
            df = self._series_frame(disease)
            visual = disease.get("visual_diagnostics") or {}
            key = f"disease:{disease_id}"
            series[key] = {
                "disease_id": disease_id,
                "name": self._disease_name(disease, language),
                "name_en": self._disease_name(disease, "en"),
                "name_zh": self._disease_name(disease, "zh"),
                "periods": df["period"].tolist(),
                "cases": [int(value) for value in df["cases"].tolist()],
                "deaths": [int(value) for value in df["deaths"].tolist()],
                "death_observed": [int(value) for value in df["_death_observed"].tolist()],
                "incidence_rate_per_100k": [
                    None if pd.isna(value) else round(float(value), 6)
                    for value in pd.to_numeric(df.get("incidence_rate_per_100k"), errors="coerce").tolist()
                ],
                "visual": {
                    "pre_latest_median_cases": visual.get("pre_latest_median_cases"),
                    "peak_period": visual.get("peak_period"),
                    "peak_cases": visual.get("peak_cases"),
                    "latest_4_period_cases": visual.get("latest_4_period_cases"),
                    "previous_4_period_cases": visual.get("previous_4_period_cases"),
                    "last4_change_pct": visual.get("last4_change_pct"),
                    "latest_to_baseline_ratio": visual.get("latest_to_baseline_ratio"),
                    "recent_slope_cases_per_period": visual.get("recent_slope_cases_per_period"),
                    "consecutive_increase_periods": visual.get("consecutive_increase_periods"),
                    "rolling_mean_cases": (disease.get("metrics") or {}).get("rolling_mean_cases"),
                    "previous_cases": (disease.get("metrics") or {}).get("previous_cases"),
                    "anomaly": disease.get("anomaly") or {},
                    "baseline": disease.get("baseline") or {},
                    "derived": self._derived_series(df),
                    "data_quality": disease.get("data_quality") or {},
                },
            }

        ranking_rows = []
        for row in (packet.get("attention_ranking") or packet.get("risk_ranking") or [])[:10]:
            ranking_rows.append(
                {
                    "disease_id": row.get("disease_id"),
                    "name": row.get("name_zh") if language == "zh" else row.get("name_en"),
                    "name_en": row.get("name_en"),
                    "name_zh": row.get("name_zh"),
                    "attention_score": row.get("attention_score", row.get("risk_score")),
                    "attention_level": row.get("attention_level", row.get("risk_level")),
                    "risk_score": row.get("attention_score", row.get("risk_score")),
                    "risk_level": row.get("attention_level", row.get("risk_level")),
                    "latest_cases": row.get("latest_cases"),
                    "change_pct": row.get("change_pct"),
                }
            )

        return {
            "version": "report_v4.figure_data.2",
            "language": language,
            "series": series,
            "attention_ranking": ranking_rows,
            "score_semantics": packet.get("score_semantics") or {},
            # Deprecated compatibility alias for existing renderers.
            "risk_ranking": ranking_rows,
        }

    @staticmethod
    def strip_html(figures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Retain compact JSON chart payloads and remove obsolete runtime HTML."""
        return [
            {key: value for key, value in figure.items() if key != "html"}
            for figure in figures
        ]

    @staticmethod
    def compact_specs(figures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return metadata-only specs for section metadata where chart data is redundant."""
        return [
            {key: value for key, value in figure.items() if key not in {"html", "option"}}
            for figure in figures
        ]

    def _ai_specs(
        self,
        packet: Dict[str, Any],
        deep_analysis: Dict[str, Any],
    ) -> List[ReportFigureSpec]:
        raw_plan = deep_analysis.get("figure_plan")
        if not isinstance(raw_plan, list):
            return []

        specs: List[ReportFigureSpec] = []
        top_id = self._top_disease(packet).get("disease_id")
        for item in raw_plan[:4]:
            if not isinstance(item, dict):
                continue
            figure_type = str(item.get("figure_type") or "").strip()
            section_type = str(item.get("section_type") or "").strip()
            position = str(item.get("position") or "after_content").strip()
            if figure_type not in self.ALLOWED_FIGURES:
                continue
            if section_type not in self.ALLOWED_SECTIONS:
                continue
            if position not in self.ALLOWED_POSITIONS:
                position = "after_content"
            disease_id = item.get("disease_id")
            if figure_type not in {"risk_ranking_bar", "risk_matrix"}:
                disease_id = str(disease_id or top_id or "").strip() or None
            else:
                disease_id = None
            specs.append(
                ReportFigureSpec(
                    figure_type=figure_type,
                    section_type=section_type,
                    disease_id=disease_id,
                    position=position,
                    rationale=str(item.get("rationale") or ""),
                    source="ai",
                )
            )
        return specs

    def _default_specs(self, packet: Dict[str, Any], language: str) -> List[ReportFigureSpec]:
        top = self._top_disease(packet)
        top_id = top.get("disease_id")
        series_len = self._series_len(top)
        specs: List[ReportFigureSpec] = []
        if len(packet.get("attention_ranking") or packet.get("risk_ranking") or []) >= 2:
            specs.append(
                ReportFigureSpec(
                    figure_type="risk_matrix" if len(packet.get("attention_ranking") or packet.get("risk_ranking") or []) >= 5 else "risk_ranking_bar",
                    section_type="priority_signals",
                    rationale="Multiple diseases are present, so one compact prioritization figure is sufficient.",
                )
            )
        if top_id:
            specs.append(
                ReportFigureSpec(
                    figure_type="signal_context_panel",
                    section_type="priority_signals",
                    disease_id=str(top_id),
                    rationale="A compact signal-context panel summarizes latest burden against baseline and recent-window comparators.",
                )
            )
            if self._has_notable_anomaly(top):
                specs.append(
                    ReportFigureSpec(
                        figure_type="anomaly_marker_curve",
                        section_type="trend_anomaly_analysis",
                        disease_id=str(top_id),
                        rationale="An anomaly marker is included only when the deterministic anomaly rule is relevant.",
                    )
                )
            elif series_len >= 8:
                specs.append(
                    ReportFigureSpec(
                        figure_type="seasonal_baseline_band",
                        section_type="trend_anomaly_analysis",
                        disease_id=str(top_id),
                        rationale="A baseline band makes the latest signal readable against expected background variability.",
                    )
                )
            elif series_len >= 2:
                specs.append(
                    ReportFigureSpec(
                        figure_type="epidemic_curve",
                        section_type="trend_anomaly_analysis",
                        disease_id=str(top_id),
                        rationale="The leading signal needs a time-series curve when baseline context is limited.",
                    )
                )
            if self._has_incidence(top):
                specs.append(
                    ReportFigureSpec(
                        figure_type="cases_incidence_panel",
                        section_type="disease_profiles",
                        disease_id=str(top_id),
                        rationale="Case counts and crude incidence should be read together when denominators are available.",
                    )
                )
            elif series_len >= 24 and self._strong_short_window(top):
                specs.append(
                    ReportFigureSpec(
                        figure_type="recent_window_heatmap",
                        section_type="trend_anomaly_analysis",
                        disease_id=str(top_id),
                        rationale="A recent-window heatmap is useful when the main question is persistence across many periods.",
                    )
                )
        return specs

    def _can_render(self, packet: Dict[str, Any], spec: ReportFigureSpec) -> bool:
        if spec.figure_type == "risk_ranking_bar":
            return len(packet.get("attention_ranking") or packet.get("risk_ranking") or []) >= 2
        if spec.figure_type == "risk_matrix":
            return len(packet.get("attention_ranking") or packet.get("risk_ranking") or []) >= 2
        disease = self._find_disease(packet, spec.disease_id)
        if not disease:
            return False
        series = (disease.get("visual_diagnostics") or {}).get("series") or []
        if spec.figure_type in {"epidemic_curve", "seasonal_baseline_band"}:
            return len(series) >= 2
        if spec.figure_type == "signal_context_panel":
            return len(series) >= 2
        if spec.figure_type == "anomaly_marker_curve":
            return len(series) >= 4
        if spec.figure_type == "recent_window_heatmap":
            return len(series) >= 4
        if spec.figure_type == "cases_incidence_panel":
            return len(series) >= 2 and self._has_incidence(disease)
        return False

    def _height_for(self, packet: Dict[str, Any], spec: ReportFigureSpec) -> int:
        if spec.figure_type == "cases_incidence_panel":
            return 500
        if spec.figure_type == "risk_ranking_bar":
            rows = (packet.get("attention_ranking") or packet.get("risk_ranking") or [])[:10]
            return max(280, 120 + len(rows) * 38)
        if spec.figure_type == "risk_matrix":
            return 420
        if spec.figure_type == "signal_context_panel":
            return 330
        if spec.figure_type == "recent_window_heatmap":
            disease = self._find_disease(packet, spec.disease_id)
            series = ((disease or {}).get("visual_diagnostics") or {}).get("series") or []
            size = min(52, len(series))
            columns = 13 if size >= 13 else max(size, 1)
            rows = int(math.ceil(size / columns))
            return max(260, 110 + rows * 68)
        if spec.figure_type == "seasonal_baseline_band":
            return 440
        if spec.figure_type == "anomaly_marker_curve":
            return 430
        return 430

    @staticmethod
    def _data_key(spec: ReportFigureSpec) -> str:
        if spec.figure_type in {"risk_ranking_bar", "risk_matrix"}:
            return "attention_ranking"
        return f"disease:{spec.disease_id}"

    @staticmethod
    def _series_len(disease: Dict[str, Any]) -> int:
        return len((disease.get("visual_diagnostics") or {}).get("series") or [])

    @staticmethod
    def _has_notable_anomaly(disease: Dict[str, Any]) -> bool:
        anomaly = disease.get("anomaly") or {}
        if anomaly.get("is_anomaly"):
            return True
        try:
            return abs(float(anomaly.get("robust_z"))) >= 3.0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _strong_short_window(disease: Dict[str, Any]) -> bool:
        visual = disease.get("visual_diagnostics") or {}
        try:
            return abs(float(visual.get("last4_change_pct"))) >= 50.0
        except (TypeError, ValueError):
            return False

    def _figure_text(
        self,
        packet: Dict[str, Any],
        spec: ReportFigureSpec,
        language: str,
    ) -> tuple[str, str, List[str], List[str]]:
        disease = self._find_disease(packet, spec.disease_id)
        name = self._disease_name(disease or {}, language)
        cadence = packet.get("reporting_cadence") or "reporting-period"
        if spec.figure_type == "risk_ranking_bar":
            title = "Surveillance attention priority by disease" if language != "zh" else "疾病监测关注优先级"
            caption = (
                "This deterministic review-priority score combines reported burden, short-term change, mortality signals when available, anomaly status, historical position, and data quality. It is not a public-health risk estimate."
                if language != "zh"
                else "按报告病例负担、近期变化、可用死亡线索、异常提示、历史位置和数据完整性安排复核顺序；该分值不是公共卫生风险估计。"
            )
            refs = [
                ref
                for item in (packet.get("attention_ranking") or packet.get("risk_ranking") or [])[:10]
                for ref in (item.get("evidence_refs") or [])
            ]
            legend = ["Bar length = deterministic attention score.", "Color encodes review-priority band, not disease severity."] if language != "zh" else ["条形越长，监测复核优先级越高。", "颜色表示复核优先级分档，不代表疾病严重程度。"]
            return title, caption, legend, list(dict.fromkeys(refs))[:24]
        if spec.figure_type == "risk_matrix":
            title = "Attention matrix: burden versus acceleration" if language != "zh" else "监测关注矩阵：病例数与增长"
            caption = (
                "Diseases are positioned by latest burden and short-term percentage change; color and size encode deterministic review priority, not public-health risk."
                if language != "zh"
                else "按最新病例数和近期变化定位疾病；颜色和点大小表示监测复核优先级，不是公共卫生风险。"
            )
            refs = [
                ref
                for item in (packet.get("attention_ranking") or packet.get("risk_ranking") or [])[:10]
                for ref in (item.get("evidence_refs") or [])
            ]
            legend = (
                ["X axis = latest reported cases.", "Y axis = change versus previous observation.", "Bubble size/color = attention score and review-priority band."]
                if language != "zh"
                else ["横轴 = 最新报告病例数。", "纵轴 = 较上一期变化。", "气泡越大、颜色越深，优先级越高。"]
            )
            return title, caption, legend, list(dict.fromkeys(refs))[:24]

        refs = self._disease_refs(disease or {}, spec.figure_type)
        if spec.figure_type == "epidemic_curve":
            title = f"{name} epidemic curve" if language != "zh" else f"{name} 流行曲线"
            caption = (
                f"{name} reported cases by {cadence} period. The dashed reference line marks the pre-latest median; the diamond marks the observed peak."
                if language != "zh"
                else f"{name} 按{cadence}报告期的病例曲线。虚线为近期常态中位数，菱形为观察峰值。"
            )
            legend = (
                ["Solid line = reported cases.", "Dotted line = 3-period moving mean.", "Dashed reference = pre-latest median."]
                if language != "zh"
                else ["实线 = 报告病例。", "点线 = 3期移动均值。", "虚线参考 = 近期常态中位数。"]
            )
            return title, caption, legend, refs
        if spec.figure_type == "seasonal_baseline_band":
            title = f"{name} baseline band" if language != "zh" else f"{name} 趋势参照区间"
            caption = (
                "Reported cases are shown against a deterministic baseline band derived from the same pre-latest time series."
                if language != "zh"
                else "将病例曲线与近期通常波动范围放在一起，帮助判断本期是否明显偏高。"
            )
            legend = (
                ["Line = reported cases.", "Shaded band = pre-latest median +/- robust dispersion.", "Dashed line = pre-latest median."]
                if language != "zh"
                else ["折线 = 报告病例。", "阴影带 = 近期通常波动范围。", "虚线 = 近期常态中位数。"]
            )
            return title, caption, legend, refs
        if spec.figure_type == "anomaly_marker_curve":
            title = f"{name} anomaly markers" if language != "zh" else f"{name} 异常标注"
            caption = (
                "Latest, peak, and robust-threshold markers show whether the current signal clears the statistical alert rule."
                if language != "zh"
                else "用最新值、观察峰值和预警线标出当前变化是否需要升级关注。"
            )
            legend = (
                ["Line = reported cases.", "Diamond = observed peak.", "Red marker = latest point.", "Threshold = robust baseline rule when available."]
                if language != "zh"
                else ["折线 = 报告病例。", "菱形 = 观察峰值。", "红点 = 最新一期。", "预警线 = 达到时需要升级关注。"]
            )
            return title, caption, legend, refs
        if spec.figure_type == "cases_incidence_panel":
            title = f"{name}: cases and crude incidence" if language != "zh" else f"{name}：病例与规模参照"
            caption = (
                "Counts and crude incidence per 100,000 are shown together so the burden signal can be read with its available denominator context."
                if language != "zh"
                else "病例数与每10万人估算率值并列展示，用于判断病例规模和趋势是否同步变化。"
            )
            legend = (
                ["Upper panel = reported cases.", "Lower panel = crude incidence per 100,000 population.", "Computed rates are contextual when source data are sentinel-based."]
                if language != "zh"
                else ["上方面板 = 报告病例。", "下方面板 = 每10万人估算率值。", "定点监测来源下，率值只作规模参照。"]
            )
            return title, caption, legend, refs
        if spec.figure_type == "signal_context_panel":
            title = f"{name} signal context" if language != "zh" else f"{name} 近期变化对照"
            caption = (
                "Latest burden is compared with the previous observation, the pre-latest median, the rolling mean, and recent 4-period windows."
                if language != "zh"
                else "将最新病例数与上一期、近期常态、近期平均和最近4期累计进行对照。"
            )
            legend = (
                ["Bars compare observed cases across deterministic evidence windows.", "Reference values are derived from the same standardized time series."]
                if language != "zh"
                else ["柱形用于比较不同时间参照下的病例数。", "所有参照均来自同一条监测时间序列。"]
            )
            return title, caption, legend, refs
        title = f"{name} recent-period heatmap" if language != "zh" else f"{name} 近期热图"
        caption = (
            "Recent reporting periods are arranged sequentially; darker cells represent higher reported case counts."
            if language != "zh"
            else "近期报告期按顺序排列；颜色越深表示报告病例数越高。"
        )
        legend = (
            ["Each cell = one reporting period.", "Color intensity = reported cases.", "Blank cells are padding, not missing observations."]
            if language != "zh"
            else ["每个单元格 = 一个报告期。", "颜色强度 = 报告病例数。", "空白单元格为排版补齐，不代表缺失观测。"]
        )
        return title, caption, legend, refs

    @staticmethod
    def _top_disease(packet: Dict[str, Any]) -> Dict[str, Any]:
        diseases = packet.get("diseases") or []
        if not diseases:
            return {}
        return sorted(diseases, key=lambda item: (item.get("risk") or {}).get("score", 0), reverse=True)[0]

    @staticmethod
    def _find_disease(packet: Dict[str, Any], disease_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if disease_id is None:
            return ReportFigureLibrary._top_disease(packet)
        for item in packet.get("diseases") or []:
            if str(item.get("disease_id")) == str(disease_id):
                return item
        return None

    @staticmethod
    def _series_frame(disease: Dict[str, Any]) -> pd.DataFrame:
        series = (disease.get("visual_diagnostics") or {}).get("series") or []
        df = pd.DataFrame(series)
        if df.empty:
            return pd.DataFrame(columns=["period", "cases", "deaths", "_death_observed", "incidence_rate_per_100k"])
        df["period"] = df["period"].astype(str)
        df["cases"] = pd.to_numeric(df.get("cases"), errors="coerce").fillna(0)
        raw_deaths = pd.to_numeric(df.get("deaths"), errors="coerce")
        if "deaths_observed" in df.columns:
            df["_death_observed"] = df["deaths_observed"].astype(bool).astype(int)
        else:
            df["_death_observed"] = raw_deaths.notna().astype(int)
        df["deaths"] = raw_deaths.fillna(0)
        df["incidence_rate_per_100k"] = pd.to_numeric(df.get("incidence_rate_per_100k"), errors="coerce")
        return df.reset_index(drop=True)

    @staticmethod
    def _derived_series(df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            return {}
        cases = pd.to_numeric(df.get("cases", pd.Series(dtype=float)), errors="coerce")
        baseline = cases.iloc[:-1].dropna() if len(cases) > 1 else cases.dropna()
        median = float(baseline.median()) if not baseline.empty else None
        mad = float((baseline - median).abs().median()) if median is not None and not baseline.empty else None
        if median is None:
            lower = upper = threshold = None
        else:
            spread = 1.4826 * mad if mad and mad > 0 else max(1.0, median * 0.2)
            lower = max(0.0, median - (2 * spread))
            upper = median + (2 * spread)
            threshold = median + (3.5 * spread)

        def constant(value: Optional[float]) -> List[Optional[float]]:
            if value is None or not math.isfinite(value):
                return [None for _ in range(len(df))]
            rounded = round(float(value), 4)
            return [rounded for _ in range(len(df))]

        rolling_mean_3 = (
            cases.rolling(window=3, min_periods=2)
            .mean()
            .round(4)
            .where(lambda values: values.notna(), None)
            .tolist()
        )
        incidence = pd.to_numeric(df.get("incidence_rate_per_100k"), errors="coerce")
        return {
            "rolling_mean_3": [
                None if pd.isna(value) else float(value)
                for value in rolling_mean_3
            ],
            "baseline_median": None if median is None else round(median, 4),
            "baseline_lower": constant(lower),
            "baseline_upper": constant(upper),
            "anomaly_threshold": constant(threshold),
            "availability": {
                "cases": [0 if pd.isna(value) else 1 for value in pd.to_numeric(df.get("cases"), errors="coerce").tolist()],
                "deaths": [int(value) for value in df.get("_death_observed", pd.Series(dtype=int)).tolist()],
                "incidence_rate_per_100k": [0 if pd.isna(value) else 1 for value in incidence.tolist()],
            },
        }

    @staticmethod
    def _has_incidence(disease: Dict[str, Any]) -> bool:
        df = ReportFigureLibrary._series_frame(disease)
        if df.empty:
            return False
        return not pd.to_numeric(df.get("incidence_rate_per_100k"), errors="coerce").dropna().empty

    @staticmethod
    def _disease_name(disease: Dict[str, Any], language: str) -> str:
        return str(
            disease.get("name_zh" if language == "zh" else "name_en")
            or disease.get("name_en")
            or disease.get("disease_id")
            or "Disease"
        )

    @staticmethod
    def _disease_refs(disease: Dict[str, Any], figure_type: str) -> List[str]:
        disease_id = disease.get("disease_id")
        if not disease_id:
            return []
        refs = [
            f"disease:{disease_id}.latest_cases",
            f"disease:{disease_id}.change_pct",
            f"disease:{disease_id}.latest_4_period_cases",
            f"disease:{disease_id}.peak_cases",
        ]
        if figure_type == "cases_incidence_panel":
            refs.extend(
                [
                    f"disease:{disease_id}.latest_incidence_rate_per_100k",
                    f"disease:{disease_id}.period_crude_incidence_per_100k",
                ]
            )
        return refs

    @staticmethod
    def _risk_color(level: Any) -> str:
        return {
            "critical": "#991b1b",
            "high": "#b91c1c",
            "moderate": "#b45309",
            "low": "#0f766e",
        }.get(str(level or "low").lower(), "#0f766e")
