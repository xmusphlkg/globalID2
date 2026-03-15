"""AI model center service: schema bootstrap, routing, and health checks."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core import get_config, get_db, get_engine, get_logger
from src.domain import AIModelConfig, AIProviderConfig

logger = get_logger(__name__)

_schema_ready = False
_ROUTING_STATE_KEY = "routing_state"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _extract_routing_state(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    state = payload.get(_ROUTING_STATE_KEY)
    return dict(state) if isinstance(state, dict) else {}


def _write_routing_state(payload: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(payload or {}) if isinstance(payload, dict) else {}
    if state:
        updated[_ROUTING_STATE_KEY] = state
    else:
        updated.pop(_ROUTING_STATE_KEY, None)
    return updated


def _rate_limit_state(payload: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _utcnow()
    state = _extract_routing_state(payload)
    cooldown_until = _parse_datetime(state.get("cooldown_until"))
    last_rate_limit_at = _parse_datetime(state.get("last_rate_limit_at"))
    active = bool(cooldown_until and cooldown_until > now)
    remaining_seconds = max(0, int((cooldown_until - now).total_seconds())) if active and cooldown_until else 0
    try:
        count = int(state.get("rate_limit_count") or 0)
    except (TypeError, ValueError):
        count = 0

    return {
        "cooldown_until": cooldown_until,
        "cooldown_until_iso": cooldown_until.isoformat() if cooldown_until else None,
        "last_rate_limit_at": last_rate_limit_at,
        "last_rate_limit_at_iso": last_rate_limit_at.isoformat() if last_rate_limit_at else None,
        "rate_limit_count": count,
        "rate_limit_active": active,
        "rate_limit_remaining_seconds": remaining_seconds,
    }


def _mark_payload_rate_limited(payload: Any, cooldown_until: datetime, occurred_at: datetime) -> Dict[str, Any]:
    state = _extract_routing_state(payload)
    try:
        current_count = int(state.get("rate_limit_count") or 0)
    except (TypeError, ValueError):
        current_count = 0
    state["cooldown_until"] = cooldown_until.isoformat()
    state["last_rate_limit_at"] = occurred_at.isoformat()
    state["rate_limit_count"] = current_count + 1
    return _write_routing_state(payload, state)


def _clear_payload_rate_limit(payload: Any, recovered_at: Optional[datetime] = None) -> Dict[str, Any]:
    state = _extract_routing_state(payload)
    if not state:
        return dict(payload or {}) if isinstance(payload, dict) else {}

    state.pop("cooldown_until", None)
    if recovered_at is not None:
        state["last_recovered_at"] = recovered_at.isoformat()
    return _write_routing_state(payload, state)


def _combined_route_rate_limit_state(model: AIModelConfig, provider: AIProviderConfig) -> Dict[str, Any]:
    now = _utcnow()
    model_state = _rate_limit_state(model.extra_params, now)
    provider_state = _rate_limit_state(provider.extra_config, now)

    active_states = []
    if model_state["rate_limit_active"]:
        active_states.append(("model", model_state))
    if provider_state["rate_limit_active"]:
        active_states.append(("provider", provider_state))

    chosen_scope = None
    chosen_state: Optional[Dict[str, Any]] = None
    if active_states:
        chosen_scope, chosen_state = max(
            active_states,
            key=lambda item: item[1]["rate_limit_remaining_seconds"],
        )

    last_model_rate_limit_at = model_state["last_rate_limit_at_iso"] if model_state["rate_limit_count"] > 0 else None
    last_provider_rate_limit_at = provider_state["last_rate_limit_at_iso"] if provider_state["rate_limit_count"] > 0 else None

    return {
        "rate_limit_active": bool(chosen_state),
        "rate_limit_scope": chosen_scope,
        "rate_limit_cooldown_until": chosen_state["cooldown_until_iso"] if chosen_state else None,
        "rate_limit_remaining_seconds": chosen_state["rate_limit_remaining_seconds"] if chosen_state else 0,
        "rate_limit_count": chosen_state["rate_limit_count"] if chosen_state else model_state["rate_limit_count"],
        "last_rate_limit_at": (
            chosen_state["last_rate_limit_at_iso"]
            if chosen_state
            else last_model_rate_limit_at
        ),
        "model_rate_limit_count": model_state["rate_limit_count"],
        "provider_rate_limit_count": provider_state["rate_limit_count"],
        "model_last_rate_limit_at": last_model_rate_limit_at,
        "provider_last_rate_limit_at": last_provider_rate_limit_at,
    }


def get_provider_rate_limit_state(provider: AIProviderConfig) -> Dict[str, Any]:
    state = _rate_limit_state(provider.extra_config)
    return {
        "rate_limit_active": state["rate_limit_active"],
        "rate_limit_cooldown_until": state["cooldown_until_iso"],
        "rate_limit_remaining_seconds": state["rate_limit_remaining_seconds"],
        "rate_limit_count": state["rate_limit_count"],
        "last_rate_limit_at": state["last_rate_limit_at_iso"],
    }


def get_model_rate_limit_state(model: AIModelConfig, provider: Optional[AIProviderConfig] = None) -> Dict[str, Any]:
    if provider is not None:
        return _combined_route_rate_limit_state(model, provider)

    state = _rate_limit_state(model.extra_params)
    return {
        "rate_limit_active": state["rate_limit_active"],
        "rate_limit_scope": "model" if state["rate_limit_active"] else None,
        "rate_limit_cooldown_until": state["cooldown_until_iso"],
        "rate_limit_remaining_seconds": state["rate_limit_remaining_seconds"],
        "rate_limit_count": state["rate_limit_count"],
        "last_rate_limit_at": state["last_rate_limit_at_iso"],
    }


def _infer_provider_from_model(model_name: str) -> Optional[str]:
    model_lower = (model_name or "").lower()
    if any(k in model_lower for k in ["glm", "chatglm", "zhipu"]):
        return "glm"
    if any(k in model_lower for k in ["qwen", "qianwen"]):
        return "qianwen"
    if any(k in model_lower for k in ["claude", "anthropic"]):
        return "anthropic"
    if any(k in model_lower for k in ["gpt-4", "gpt-3.5", "text-davinci"]):
        return "openai"
    if "azure" in model_lower:
        return "azure"
    return None


def mask_api_key(api_key: Optional[str]) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


async def ensure_model_center_tables() -> None:
    """Create model-center tables if they do not exist."""
    global _schema_ready
    if _schema_ready:
        return

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(
            AIProviderConfig.metadata.create_all,
            tables=[AIProviderConfig.__table__, AIModelConfig.__table__],
        )
    _schema_ready = True
    logger.info("AI model center tables ensured")


def _env_provider_seed() -> List[Dict[str, Any]]:
    cfg = get_config().ai

    providers: List[Dict[str, Any]] = []

    if cfg.openai_api_key:
        providers.append(
            {
                "provider_key": "openai-default",
                "provider_name": "openai",
                "display_name": "OpenAI (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.openai_base_url,
                "api_key": cfg.openai_api_key,
                "priority": 100,
            }
        )

    if cfg.anthropic_api_key:
        providers.append(
            {
                "provider_key": "anthropic-default",
                "provider_name": "anthropic",
                "display_name": "Anthropic (env)",
                "api_style": "anthropic",
                "base_url": None,
                "api_key": cfg.anthropic_api_key,
                "priority": 100,
            }
        )

    if cfg.glm_api_key:
        providers.append(
            {
                "provider_key": "glm-default",
                "provider_name": "glm",
                "display_name": "GLM (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.glm_base_url,
                "api_key": cfg.glm_api_key,
                "priority": 100,
            }
        )

    if cfg.qianwen_api_key:
        providers.append(
            {
                "provider_key": "qianwen-default",
                "provider_name": "qianwen",
                "display_name": "Qianwen (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.qianwen_base_url,
                "api_key": cfg.qianwen_api_key,
                "priority": 100,
            }
        )

    if cfg.azure_api_key and cfg.azure_endpoint:
        providers.append(
            {
                "provider_key": "azure-default",
                "provider_name": "azure",
                "display_name": "Azure OpenAI (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.azure_endpoint,
                "api_key": cfg.azure_api_key,
                "priority": 100,
                "extra_config": {"api_version": cfg.azure_api_version},
            }
        )

    if cfg.custom_api_key and cfg.custom_base_url:
        providers.append(
            {
                "provider_key": "custom-default",
                "provider_name": "custom",
                "display_name": "Custom (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.custom_base_url,
                "api_key": cfg.custom_api_key,
                "priority": 100,
            }
        )

    return providers


async def bootstrap_model_center_from_env(force: bool = False) -> None:
    """Seed provider/model records from .env only when DB has no records (or force=True)."""
    await ensure_model_center_tables()

    async with get_db() as db:
        providers_existing = (await db.execute(select(AIProviderConfig.id))).first() is not None
        models_existing = (await db.execute(select(AIModelConfig.id))).first() is not None

        if (providers_existing or models_existing) and not force:
            return

        if force:
            for model in (await db.execute(select(AIModelConfig))).scalars().all():
                await db.delete(model)
            for provider in (await db.execute(select(AIProviderConfig))).scalars().all():
                await db.delete(provider)
            await db.commit()

        providers = _env_provider_seed()
        if not providers:
            logger.info("No provider credentials in env; skip model-center bootstrap")
            return

        provider_by_name: Dict[str, AIProviderConfig] = {}
        for i, item in enumerate(providers, start=1):
            provider = AIProviderConfig(
                provider_key=item["provider_key"],
                provider_name=item["provider_name"],
                display_name=item["display_name"],
                api_style=item.get("api_style", "openai_compatible"),
                base_url=item.get("base_url"),
                api_key=item.get("api_key"),
                priority=i * 10,
                extra_config=item.get("extra_config", {}),
                is_active=True,
                last_check_status="unknown",
            )
            db.add(provider)
            provider_by_name[provider.provider_name] = provider

        await db.flush()

        ai_cfg = get_config().ai
        chain = ai_cfg.model_chain

        if not chain:
            logger.info("No model chain in env; model-center providers seeded without models")
            await db.commit()
            return

        for idx, model_name in enumerate(chain, start=1):
            preferred_provider = _infer_provider_from_model(model_name) or ai_cfg.default_provider
            provider = provider_by_name.get(preferred_provider)
            if provider is None and provider_by_name:
                provider = list(provider_by_name.values())[0]
            if provider is None:
                continue

            model = AIModelConfig(
                provider_id=provider.id,
                model_key=f"{provider.provider_key}:{model_name}",
                model_name=model_name,
                display_name=model_name,
                api_style=None,
                priority=idx * 10,
                is_enabled=True,
                is_default=(idx == 1),
                last_check_status="unknown",
            )
            db.add(model)

        await db.commit()
        logger.info("Model center bootstrapped from env")


def is_rate_limit_error(error: Any) -> bool:
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None

    if normalized_status == 429:
        return True

    # Some providers return throttling as HTTP 400 with provider-specific error code.
    code_candidates = _extract_error_codes(error)
    message = str(error)

    if any(code in {"429", "2003", "insufficient_quota", "quota_exceeded", "rate_limit_exceeded"} for code in code_candidates):
        return True

    msg = message.lower()
    return any(
        keyword in msg
        for keyword in [
            "insufficient_quota",
            "rate limit",
            "429",
            "quota",
            "too many requests",
            "too frequent",
            "request frequency",
            "throttl",
            "retry later",
            "allocationquota",
            "free tier",
            "\u8bf7\u6c42\u9650\u9891",  # 请求限频
            "\u9650\u9891",  # 限频
            "\u9891\u7387\u9650\u5236",  # 频率限制
            "\u7a0d\u540e\u91cd\u8bd5",  # 稍后重试
        ]
    )


def _extract_error_codes(error: Any) -> List[str]:
    codes: List[str] = []

    direct_code = getattr(error, "code", None)
    if direct_code is not None:
        codes.append(str(direct_code).strip().lower())

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        body_code = body.get("code")
        if body_code is not None:
            codes.append(str(body_code).strip().lower())

        nested = body.get("error")
        if isinstance(nested, dict):
            nested_code = nested.get("code")
            if nested_code is not None:
                codes.append(str(nested_code).strip().lower())

    message = str(error)
    code_match = re.search(r"['\"]code['\"]\s*:\s*['\"]?([A-Za-z0-9_-]+)", message)
    if code_match:
        codes.append(code_match.group(1).strip().lower())

    return [c for c in codes if c]


def is_model_unavailable_error(error: Any) -> bool:
    """Detect unrecoverable model-level errors (missing model or no access)."""
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None

    code_candidates = _extract_error_codes(error)
    if any(code in {"model_not_found", "invalid_model", "model_access_denied"} for code in code_candidates):
        return True

    msg = str(error).lower()
    message_hit = any(
        keyword in msg
        for keyword in [
            "model not found",
            "model does not exist",
            "does not exist or you do not have access",
            "you do not have access to it",
            "invalid model",
            "unsupported model",
            "unknown model",
        ]
    )

    # 404 + model-related message usually means route/model is invalid for this provider key.
    if normalized_status == 404 and message_hit:
        return True

    return message_hit


def extract_retry_after_seconds(error: Any) -> Optional[int]:
    headers = None
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)

    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(1, int(float(str(retry_after).strip())))
            except (TypeError, ValueError):
                pass

    message = str(error)
    patterns = [
        r"retry after\s+(\d+(?:\.\d+)?)s",
        r"try again in\s+(\d+(?:\.\d+)?)s",
        r"in\s+(\d+(?:\.\d+)?)\s*seconds",
        r"after\s+(\d+(?:\.\d+)?)\s*seconds",
        r"(?:please\s*)?retry\s*(?:in\s*)?(\d+(?:\.\d+)?)\s*s",
        r"(\d+(?:\.\d+)?)\s*\u79d2\s*(?:\u540e)?\s*(?:\u518d\u8bd5|\u91cd\u8bd5)",
        r"\u8bf7\u5728\s*(\d+(?:\.\d+)?)\s*\u79d2\s*(?:\u540e)?\s*(?:\u91cd\u8bd5|\u518d\u8bd5)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            try:
                return max(1, int(float(match.group(1))))
            except (TypeError, ValueError):
                continue
    return None


async def get_runtime_routes() -> List[Dict[str, Any]]:
    """Return all enabled runtime routes with routing availability metadata."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        rows = (
            await db.execute(
                select(AIModelConfig)
                .options(selectinload(AIModelConfig.provider))
                .join(AIProviderConfig, AIModelConfig.provider_id == AIProviderConfig.id)
                .where(
                    AIModelConfig.is_enabled.is_(True),
                    AIProviderConfig.is_active.is_(True),
                )
                .order_by(AIModelConfig.priority.asc(), AIProviderConfig.priority.asc())
            )
        ).scalars().all()

        routes: List[Dict[str, Any]] = []
        for model in rows:
            provider = model.provider
            if not provider:
                continue

            rate_limit_state = _combined_route_rate_limit_state(model, provider)
            provider_status = str(provider.last_check_status or "").strip().lower()
            model_status = str(model.last_check_status or "").strip().lower()
            status_routable = provider_status != "unavailable" and model_status != "unavailable"

            routes.append(
                {
                    "model_id": model.id,
                    "model_key": model.model_key,
                    "model_name": model.model_name,
                    "priority": model.priority,
                    "provider_id": provider.id,
                    "provider_key": provider.provider_key,
                    "provider_name": provider.provider_name,
                    "api_style": model.api_style or provider.api_style or "openai_compatible",
                    "base_url": provider.base_url,
                    "api_key": provider.api_key,
                    "extra_headers": provider.extra_headers or {},
                    "extra_config": provider.extra_config or {},
                    "extra_params": model.extra_params or {},
                    "temperature": model.temperature,
                    "max_tokens": model.max_tokens,
                    "has_api_key": bool(provider.api_key),
                    "last_check_status": model.last_check_status or provider.last_check_status,
                    "available_for_routing": bool(provider.api_key)
                    and status_routable
                    and not rate_limit_state["rate_limit_active"],
                    **rate_limit_state,
                }
            )

        return routes


async def get_active_model_routes() -> List[Dict[str, Any]]:
    """Return routable model routes ordered by model priority."""
    routes = await get_runtime_routes()
    return [route for route in routes if route.get("available_for_routing")]


async def _test_openai_compatible(route: Dict[str, Any], model_name: str) -> None:
    client = AsyncOpenAI(
        api_key=route.get("api_key"),
        base_url=route.get("base_url") or None,
        default_headers=(route.get("extra_headers") or None),
    )
    await client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=16,
        temperature=0,
    )


async def _test_anthropic(route: Dict[str, Any], model_name: str) -> None:
    client = AsyncAnthropic(api_key=route.get("api_key"))
    await client.messages.create(
        model=model_name,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=16,
        temperature=0,
    )


async def test_route_connection(route: Dict[str, Any]) -> Dict[str, Any]:
    """Test one model route and return structured status."""
    style = str(route.get("api_style") or "openai_compatible").lower()
    model_name = str(route.get("model_name") or "")

    try:
        if style == "anthropic":
            await _test_anthropic(route, model_name)
        else:
            await _test_openai_compatible(route, model_name)

        return {
            "success": True,
            "status": "available",
            "message": "Connection successful",
            "model_name": model_name,
        }
    except Exception as exc:
        return {
            "success": False,
            "status": "rate_limited" if is_rate_limit_error(exc) else "unavailable",
            "message": str(exc),
            "model_name": model_name,
            "retry_after_seconds": extract_retry_after_seconds(exc),
        }


async def update_model_check_result(model_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id)
        if model is None:
            return
        model.last_check_status = status
        model.last_check_message = message
        model.last_checked_at = _utcnow()
        if status == "available":
            model.extra_params = _clear_payload_rate_limit(model.extra_params, model.last_checked_at)
        await db.commit()


async def update_provider_check_result(provider_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id)
        if provider is None:
            return
        provider.last_check_status = status
        provider.last_check_message = message
        provider.last_checked_at = _utcnow()
        if status == "available":
            provider.extra_config = _clear_payload_rate_limit(provider.extra_config, provider.last_checked_at)
        await db.commit()


async def mark_route_rate_limited(
    route: Dict[str, Any],
    message: str,
    retry_after_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist provider/model cooldown after a runtime rate-limit error."""
    route_extra_params = route.get("extra_params") or {}
    route_extra_config = route.get("extra_config") or {}
    mark_provider_raw = route_extra_config.get("mark_provider_rate_limited", False)
    if isinstance(mark_provider_raw, str):
        mark_provider_rate_limited = mark_provider_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        mark_provider_rate_limited = bool(mark_provider_raw)
    cooldown_seconds = retry_after_seconds
    if cooldown_seconds is None:
        cooldown_seconds = int(
            route_extra_params.get("rate_limit_cooldown_seconds")
            or route_extra_config.get("rate_limit_cooldown_seconds")
            or get_config().ai.rate_limit_cooldown_seconds
        )

    now = _utcnow()
    cooldown_until = now + timedelta(seconds=max(1, int(cooldown_seconds)))

    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))
        provider = await db.get(AIProviderConfig, int(route["provider_id"]))

        if model is not None:
            model.last_check_status = "rate_limited"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _mark_payload_rate_limited(model.extra_params, cooldown_until, now)

        if provider is not None:
            if mark_provider_rate_limited:
                provider.last_check_status = "rate_limited"
                provider.last_check_message = message
                provider.last_checked_at = now
                provider.extra_config = _mark_payload_rate_limited(provider.extra_config, cooldown_until, now)
            else:
                # Default behavior: cooldown this model route only. This prevents one throttled model
                # from blocking sibling models under the same provider/API key.
                provider.extra_config = _clear_payload_rate_limit(provider.extra_config, now)
                if str(provider.last_check_status or "").strip().lower() == "rate_limited":
                    provider.last_check_status = "unknown"
                    provider.last_check_message = "Provider cooldown cleared; using model-level cooldown only"
                    provider.last_checked_at = now

        await db.commit()

    return {
        "cooldown_until": cooldown_until.isoformat(),
        "cooldown_seconds": max(1, int(cooldown_seconds)),
    }


async def mark_route_unavailable(
    route: Dict[str, Any],
    message: str,
) -> None:
    """Persist model-level unavailable status when provider reports model_not_found/no_access."""
    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))
        if model is not None:
            model.last_check_status = "unavailable"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)
            await db.commit()


async def mark_model_unavailable_by_name(
    model_name: str,
    message: str,
) -> int:
    """Persist unavailable status by model name when runtime route metadata is missing."""
    normalized = str(model_name or "").strip()
    if not normalized:
        return 0

    now = _utcnow()
    async with get_db() as db:
        models = (
            await db.execute(
                select(AIModelConfig).where(
                    AIModelConfig.model_name == normalized,
                    AIModelConfig.is_enabled.is_(True),
                )
            )
        ).scalars().all()

        if not models:
            return 0

        for model in models:
            model.last_check_status = "unavailable"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)

        await db.commit()
        return len(models)


async def clear_route_rate_limit(route: Dict[str, Any], message: str = "Connection successful") -> None:
    """Clear persisted cooldown after a route has recovered."""
    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))
        provider = await db.get(AIProviderConfig, int(route["provider_id"]))

        if model is not None:
            model.last_check_status = "available"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)

        if provider is not None:
            provider.last_check_status = "available"
            provider.last_check_message = message
            provider.last_checked_at = now
            provider.extra_config = _clear_payload_rate_limit(provider.extra_config, now)

        await db.commit()


async def check_model_by_id(model_id: int) -> Dict[str, Any]:
    """Test one model route by DB id and persist status."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        model = (
            await db.execute(
                select(AIModelConfig)
                .options(selectinload(AIModelConfig.provider))
                .where(AIModelConfig.id == model_id)
            )
        ).scalar_one_or_none()
        if model is None:
            return {"success": False, "status": "not_found", "message": "Model not found"}

        provider = model.provider
        if provider is None:
            return {"success": False, "status": "invalid", "message": "Provider not found"}

        route = {
            "model_id": model.id,
            "model_name": model.model_name,
            "provider_id": provider.id,
            "provider_key": provider.provider_key,
            "provider_name": provider.provider_name,
            "api_style": model.api_style or provider.api_style,
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "extra_headers": provider.extra_headers or {},
        }

    result = await test_route_connection(route)
    await update_model_check_result(model_id, result["status"], result["message"])
    await update_provider_check_result(route["provider_id"], result["status"], result["message"])
    return result


async def check_provider_by_id(provider_id: int) -> Dict[str, Any]:
    """Test provider by checking its highest-priority enabled model."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        model = (
            await db.execute(
                select(AIModelConfig)
                .options(selectinload(AIModelConfig.provider))
                .where(AIModelConfig.provider_id == provider_id, AIModelConfig.is_enabled.is_(True))
                .order_by(AIModelConfig.priority.asc())
            )
        ).scalars().first()

    if model is None:
        result = {
            "success": False,
            "status": "invalid",
            "message": "No enabled model under this provider",
        }
        await update_provider_check_result(provider_id, result["status"], result["message"])
        return result

    return await check_model_by_id(model.id)


async def check_all_models() -> List[Dict[str, Any]]:
    """Test all active model routes and persist statuses."""
    routes = await get_active_model_routes()
    results: List[Dict[str, Any]] = []
    for route in routes:
        result = await test_route_connection(route)
        if result["status"] == "rate_limited":
            await mark_route_rate_limited(
                route,
                result["message"],
                retry_after_seconds=result.get("retry_after_seconds"),
            )
        await update_model_check_result(route["model_id"], result["status"], result["message"])
        await update_provider_check_result(route["provider_id"], result["status"], result["message"])
        result.update(
            {
                "model_id": route["model_id"],
                "provider_id": route["provider_id"],
                "provider_key": route["provider_key"],
            }
        )
        results.append(result)
    return results
