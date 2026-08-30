"""Explicit registry of independently maintained province adapters."""

from __future__ import annotations

from functools import lru_cache

from .anhui import ADAPTER as ANHUI
from .base import ProvinceSourceConfig
from .beijing import ADAPTER as BEIJING
from .chongqing import ADAPTER as CHONGQING
from .fujian import ADAPTER as FUJIAN
from .gansu import ADAPTER as GANSU
from .guangdong import ADAPTER as GUANGDONG
from .guangxi import ADAPTER as GUANGXI
from .guizhou import ADAPTER as GUIZHOU
from .hainan import ADAPTER as HAINAN
from .hebei import ADAPTER as HEBEI
from .heilongjiang import ADAPTER as HEILONGJIANG
from .henan import ADAPTER as HENAN
from .hubei import ADAPTER as HUBEI
from .hunan import ADAPTER as HUNAN
from .inner_mongolia import ADAPTER as INNER_MONGOLIA
from .jiangsu import ADAPTER as JIANGSU
from .jiangxi import ADAPTER as JIANGXI
from .jilin import ADAPTER as JILIN
from .liaoning import ADAPTER as LIAONING
from .ningxia import ADAPTER as NINGXIA
from .qinghai import ADAPTER as QINGHAI
from .shaanxi import ADAPTER as SHAANXI
from .shandong import ADAPTER as SHANDONG
from .shanghai import ADAPTER as SHANGHAI
from .shanxi import ADAPTER as SHANXI
from .sichuan import ADAPTER as SICHUAN
from .tianjin import ADAPTER as TIANJIN
from .tibet import ADAPTER as TIBET
from .xinjiang import ADAPTER as XINJIANG
from .yunnan import ADAPTER as YUNNAN
from .zhejiang import ADAPTER as ZHEJIANG

ADAPTERS = (
    BEIJING, TIANJIN, HEBEI, SHANXI, INNER_MONGOLIA, LIAONING, JILIN,
    HEILONGJIANG, SHANGHAI, JIANGSU, ZHEJIANG, ANHUI, FUJIAN, JIANGXI,
    SHANDONG, HENAN, HUBEI, HUNAN, GUANGDONG, GUANGXI, HAINAN, CHONGQING,
    SICHUAN, GUIZHOU, YUNNAN, TIBET, SHAANXI, GANSU, QINGHAI, NINGXIA,
    XINJIANG,
)


@lru_cache(maxsize=1)
def province_adapter_registry() -> dict[str, ProvinceSourceConfig]:
    registry = {adapter.code: adapter for adapter in ADAPTERS}
    if len(registry) != len(ADAPTERS):
        raise RuntimeError("Duplicate Chinese province adapter code")
    if len(registry) != 31:
        raise RuntimeError(f"Expected 31 Chinese province adapters, found {len(registry)}")
    adcodes = {adapter.adcode for adapter in ADAPTERS}
    if len(adcodes) != len(ADAPTERS):
        raise RuntimeError("Duplicate Chinese province administrative code")
    invalid = [
        adapter.code
        for adapter in ADAPTERS
        if not adapter.code.startswith("CN-")
        or len(adapter.code) != 5
        or len(adapter.adcode) != 6
        or not adapter.adcode.isdigit()
    ]
    if invalid:
        raise RuntimeError(f"Invalid Chinese province adapter identifiers: {invalid}")
    return registry


def get_province_adapter(code: str) -> ProvinceSourceConfig | None:
    return province_adapter_registry().get(str(code or "").strip().upper())
