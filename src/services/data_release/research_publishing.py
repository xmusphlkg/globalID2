"""Shared publication switches used by Astro and the R2 publishing boundary."""

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parents[3] / "shared/research-publishing.json"


def research_social_cards_enabled() -> bool:
    # Read for each publication so a long-lived process observes the switch.
    # Missing or malformed settings must not accidentally enable paid work.
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(settings, dict) and settings.get("article_social_cards_enabled") is True


def is_research_card(key: str) -> bool:
    return key.lstrip("/").startswith("research/cards/")
