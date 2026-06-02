"""Dataset normalization and source-policy handling for report v4."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.core import get_config, get_logger, normalize_rate_columns

logger = get_logger(__name__)


@dataclass(frozen=True)
class SourcePolicy:
    death_counts: str = "unknown"
    case_scope: str = "unknown"
    rate_basis: str = "unknown"
    source: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CASE_ONLY_COUNTRIES = frozenset({"AU", "BR", "CH", "HK", "JP", "KR", "NZ", "TW", "US"})


class DatasetBuilder:
    """Prepare report inputs before deterministic evidence calculations."""

    def __init__(self, policy_path: Path | None = None):
        base_dir = Path(get_config().app.base_dir)
        self.policy_path = policy_path or base_dir / "configs" / "reporting_sources.yml"
        self._policies = self._load_policies()

    def source_policy_for(self, country_code: str, data: pd.DataFrame | None = None) -> SourcePolicy:
        code = (country_code or "").strip().upper()
        country_cfg = self._policies.get(code) or {}
        default_cfg = country_cfg.get("default") if isinstance(country_cfg, dict) else {}
        if isinstance(default_cfg, dict) and default_cfg:
            return SourcePolicy(
                death_counts=str(default_cfg.get("death_counts") or "unknown"),
                case_scope=str(default_cfg.get("case_scope") or "unknown"),
                rate_basis=str(default_cfg.get("rate_basis") or "unknown"),
                source=f"config:{self.policy_path.name}",
            )

        inferred = self._infer_policy_from_metadata(data)
        if inferred:
            return inferred
        if code in CASE_ONLY_COUNTRIES:
            return SourcePolicy(
                death_counts="not_reported",
                case_scope="national_or_sentinel",
                rate_basis="unavailable",
                source="country_default",
            )
        if code == "CN":
            return SourcePolicy(
                death_counts="reported",
                case_scope="national",
                rate_basis="source_rate",
                source="country_default",
            )
        return SourcePolicy(source="fallback")

    def normalize(
        self,
        data: pd.DataFrame,
        *,
        country_code: str,
        historical_data: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame | None, SourcePolicy]:
        policy = self.source_policy_for(country_code, data)
        current = self._normalize_frame(data, policy)
        historical = self._normalize_frame(historical_data, policy) if historical_data is not None else None
        return current, historical, policy

    def _normalize_frame(self, data: pd.DataFrame | None, policy: SourcePolicy) -> pd.DataFrame:
        if data is None:
            return pd.DataFrame()
        frame = data.copy()
        if frame.empty:
            return frame
        if "time" in frame.columns:
            frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
        for column in ("cases", "deaths", "new_cases", "new_deaths"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if policy.death_counts == "not_reported":
            deaths = pd.to_numeric(frame.get("deaths", pd.Series(dtype=float)), errors="coerce")
            if not (deaths.dropna() > 0).any():
                frame["deaths"] = pd.NA
                if "new_deaths" in frame.columns:
                    frame["new_deaths"] = pd.NA
        frame["death_reporting_policy"] = policy.death_counts
        return normalize_rate_columns(frame, copy=False)

    def _load_policies(self) -> dict[str, Any]:
        if not self.policy_path.exists():
            return {}
        try:
            payload = yaml.safe_load(self.policy_path.read_text(encoding="utf-8")) or {}
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning("Failed to load report source policies from %s: %s", self.policy_path, exc)
            return {}

    @staticmethod
    def _infer_policy_from_metadata(data: pd.DataFrame | None) -> SourcePolicy | None:
        if data is None or data.empty or "metadata" not in data.columns:
            return None
        for metadata in data["metadata"].dropna().head(20):
            if not isinstance(metadata, dict):
                continue
            death_reporting = str(metadata.get("death_reporting") or "").lower()
            if death_reporting in {"not_provided_by_source", "not_reported", "case_only"}:
                return SourcePolicy(
                    death_counts="not_reported",
                    case_scope=str(metadata.get("case_scope") or "unknown"),
                    rate_basis=str(metadata.get("rate_basis") or "unavailable"),
                    source="record_metadata",
                )
        return None
