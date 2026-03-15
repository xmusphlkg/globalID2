import os
from pathlib import Path
from typing import Any, Dict, Tuple

from src.core import get_logger
from src.core.config import get_config

logger = get_logger(__name__)
_TEMPLATE_CACHE: Dict[str, Tuple[str, float]] = {}
_HOT_RELOAD_OVERRIDE_ENV = "AI_PROMPT_AUTO_RELOAD"


class _SafeDict(dict):
    """Keep unknown placeholders unchanged during format_map."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _parse_bool_env(raw: str) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _is_hot_reload_enabled() -> bool:
    """Prompt hot reload policy: env override > explicit ai setting > development default."""
    env_raw = os.getenv(_HOT_RELOAD_OVERRIDE_ENV)
    if env_raw is not None:
        return _parse_bool_env(env_raw)

    cfg = get_config()
    ai_setting = getattr(cfg.ai, "prompt_auto_reload", False)
    return bool(ai_setting or cfg.is_development)


def _read_template(prompt_path: Path) -> Tuple[str, float]:
    with open(prompt_path, "r", encoding="utf-8") as f:
        template = f.read().strip()
    mtime = prompt_path.stat().st_mtime
    return template, mtime


def clear_prompt_template_cache() -> None:
    """Clear template cache, useful for tests and optional manual refresh endpoints."""
    _TEMPLATE_CACHE.clear()


def load_prompt_template(filename: str, default_template: str = "") -> str:
    """Load a prompt template from configs/prompts/ directory with in-process cache."""
    if not filename:
        return default_template

    project_root = Path(__file__).parent.parent.parent.parent
    prompt_path = project_root / "configs" / "prompts" / filename
    hot_reload = _is_hot_reload_enabled()

    cached = _TEMPLATE_CACHE.get(filename)
    if cached is not None and not hot_reload:
        return cached[0]

    if cached is not None and hot_reload:
        try:
            current_mtime = prompt_path.stat().st_mtime
        except FileNotFoundError:
            current_mtime = -1
        if cached[1] == current_mtime:
            return cached[0]

    try:
        template, mtime = _read_template(prompt_path)
        _TEMPLATE_CACHE[filename] = (template, mtime)
        return template
    except FileNotFoundError:
        logger.warning(f"Prompt template {filename} not found at {prompt_path}")
        return default_template


def render_prompt_template(
    filename: str,
    variables: Dict[str, Any],
    default_template: str = "",
) -> str:
    """Render template safely: missing variables won't crash prompt construction."""
    template = load_prompt_template(filename, default_template=default_template)
    if not template:
        return ""

    normalized = {
        str(key): "" if value is None else value
        for key, value in (variables or {}).items()
    }
    try:
        return template.format_map(_SafeDict(normalized))
    except Exception as exc:
        logger.warning(f"Failed to render prompt template {filename}: {exc}")
        return template
