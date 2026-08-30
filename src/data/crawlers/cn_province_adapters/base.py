"""Lightweight contract shared by all Chinese province adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProvinceSourceConfig:
    """A province-owned source definition consumed by the shared crawler."""

    code: str
    name_en: str
    name_zh: str
    adcode: str
    index_url: str
    parser: str
    timezone: str = "Asia/Shanghai"

    @property
    def parent_country_code(self) -> str:
        return "CN"

    @property
    def geography_key(self) -> str:
        return f"country:{self.code}:national"
