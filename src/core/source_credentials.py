"""Credential prerequisites shared by collection and its scheduler."""

import os
from urllib.parse import unquote

from src.core.country_library import get_country_bootstrap_config
from src.core.source_scopes import canonicalize_task_source


def kdca_service_key(*, service_key: str | None = None, env_name: str | None = None) -> str:
    if env_name is None:
        config = get_country_bootstrap_config("KR").get("crawler_config", {})
        env_name = config.get("service_key_env") or "DATA_GO_KR_SERVICE_KEY"
    for candidate in (service_key, os.getenv(env_name), os.getenv("KDCA_EID_SERVICE_KEY")):
        if candidate and (value := unquote(candidate.strip()).strip()):
            return value
    return ""


def source_credential_blocker(country_code: str, source: str | None) -> str | None:
    """Monthly KDCA retains its portal fallback; annual endpoints require a key."""
    if str(country_code).strip().upper() != "KR":
        return None
    scope = canonicalize_task_source(source, country_code="KR")
    if scope not in {"kdca_death_annual", "kdca_region_annual"} or kdca_service_key():
        return None
    config = get_country_bootstrap_config("KR").get("crawler_config", {})
    env_name = config.get("service_key_env") or "DATA_GO_KR_SERVICE_KEY"
    return f"{scope} requires {env_name} (or KDCA_EID_SERVICE_KEY); collection is waiting for credentials"
