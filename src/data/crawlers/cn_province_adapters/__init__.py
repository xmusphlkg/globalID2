"""Per-province source adapters for Chinese monthly surveillance reports.

Transport, document conversion, and table parsing remain shared.  Each
province owns a small declarative module so source URLs and parser choices can
change independently without growing a single registry file.
"""

from .base import ProvinceSourceConfig
from .registry import get_province_adapter, province_adapter_registry

__all__ = [
    "ProvinceSourceConfig",
    "get_province_adapter",
    "province_adapter_registry",
]
