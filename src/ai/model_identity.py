"""Consistent model identity for routing constraints and review provenance."""

from collections.abc import Iterable


def model_identity(key: str | None) -> str:
    """Return a case-insensitive model name, independent of its provider route."""
    return str(key or "").strip().split(":", 1)[-1].casefold()


def model_matches(key: str, patterns: Iterable[str]) -> bool:
    """Match qualified routes exactly, and bare model names across providers."""
    normalized = str(key or "").strip().casefold()
    name = model_identity(normalized)
    for pattern in patterns:
        candidate = str(pattern or "").strip().casefold()
        if candidate and (
            normalized == candidate if ":" in candidate else name == candidate
        ):
            return True
    return False
