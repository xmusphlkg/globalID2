"""Situation Room v3 contracts and deterministic analysis pipeline."""

from .contracts import SituationReportV3
from .model import evaluate_frame_v3
from .reporting import build_daily_report_v3, build_period_report_v3
from .source_adapters import fetch_series_inputs_v3

__all__ = [
    "SituationReportV3",
    "build_daily_report_v3",
    "build_period_report_v3",
    "evaluate_frame_v3",
    "fetch_series_inputs_v3",
]
