"""Shared model catalogue application service.

This module owns persistence orchestration, bootstrap and probe scheduling.
Pure policy lives in ``catalogue``; request admission and retries belong to the
activity runtime. Reports, knowledge, literature and situation callers share
these same catalogue and transport contracts.
"""

from __future__ import annotations

import asyncio
import re
import time
import weakref
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core import get_config, get_db, get_engine, get_logger
from src.domain import AIModelCallLog, AIModelConfig, AIProviderConfig

from .catalogue import errors, health, options, probes, responses, routes

logger = get_logger(__name__)

_schema_ready_engine: Any = None
_bootstrap_checked_engine: Any = None
_bootstrap_checked_at: float | None = None
_bootstrap_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)
_BOOTSTRAP_RECHECK_SECONDS = 60.0
_MODEL_RECOVERY_PROBE_INTERVAL_SECONDS = 300
_PROVIDER_AUTHENTICATION_FAILURE_MESSAGE = (
    "Provider credentials were rejected. Update the credentials and run a provider "
    "connection test before routing requests again."
)


_SECRET_VALUE_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_-]?key\s*[:=]\s*|"
    r"access[_-]?token\s*[:=]\s*|sk-[a-z0-9_-]{8,})[^\s,;]+"
)


async def record_model_call_log(
    *,
    request_id: UUID,
    route: dict[str, Any] | None,
    model_name: str,
    provider_key: str,
    agent_name: str,
    status: str,
    latency_ms: float,
    error: BaseException | None = None,
) -> None:
    """Persist a metadata-only record for one real provider attempt.

    Logging is best effort: a telemetry storage issue must never turn a valid
    model response into an application failure.
    """
    route = route or {}
    error_text = (
        (str(error).strip() or type(error).__name__) if error is not None else None
    )
    error_message = (
        _SECRET_VALUE_RE.sub(r"\1[REDACTED]", error_text)[:1000] if error_text else None
    )
    try:
        async with get_db() as db:
            db.add(
                AIModelCallLog(
                    request_id=str(request_id),
                    model_id=_safe_int(route.get("model_id")),
                    provider_id=_safe_int(route.get("provider_id")),
                    model_key=str(
                        route.get("model_key") or f"{provider_key}:{model_name}"
                    )[:200],
                    model_name=str(model_name)[:120],
                    provider_key=str(
                        route.get("provider_key") or provider_key or "unknown"
                    )[:120],
                    agent_name=str(agent_name)[:160],
                    status="success" if status == "success" else "failed",
                    latency_ms=round(max(0.0, float(latency_ms)), 1),
                    error_type=type(error).__name__[:160] if error else None,
                    error_message=error_message,
                )
            )
    except Exception as exc:
        logger.warning("Failed to persist AI model call log: {}", exc)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _infer_provider_from_model(model_name: str) -> str | None:
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


def mask_api_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


async def ensure_model_center_tables() -> None:
    """Create model-center tables if they do not exist."""
    global _schema_ready_engine
    engine = get_engine()
    if _schema_ready_engine is engine:
        return

    async with engine.begin() as conn:
        await conn.run_sync(
            AIProviderConfig.metadata.create_all,
            tables=[AIProviderConfig.__table__, AIModelConfig.__table__],
        )
    _schema_ready_engine = engine
    logger.info("AI model center tables ensured")


def invalidate_model_center_bootstrap_cache() -> None:
    """Force the next non-forced bootstrap call to verify database state.

    Normal callers do not need this: the cache expires after a short interval,
    so a database cleared out-of-band is detected and reseeded.  Tests and code
    that intentionally clear model-center rows can invalidate it immediately.
    No environment values or credentials are retained in this cache.
    """

    global _bootstrap_checked_engine, _bootstrap_checked_at
    _bootstrap_checked_engine = None
    _bootstrap_checked_at = None


def _bootstrap_cache_is_fresh(engine: Any) -> bool:
    return bool(
        _bootstrap_checked_engine is engine
        and _bootstrap_checked_at is not None
        and time.monotonic() - _bootstrap_checked_at < _BOOTSTRAP_RECHECK_SECONDS
    )


def _mark_bootstrap_cache_checked(engine: Any) -> None:
    global _bootstrap_checked_engine, _bootstrap_checked_at
    _bootstrap_checked_engine = engine
    _bootstrap_checked_at = time.monotonic()


def _bootstrap_lock() -> asyncio.Lock:
    """Return a lock scoped to the current event loop.

    Loop scoping keeps application processes single-flight while avoiding a
    lock bound to a closed pytest/application lifecycle loop.
    """

    loop = asyncio.get_running_loop()
    lock = _bootstrap_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _bootstrap_locks[loop] = lock
    return lock


def _env_provider_seed() -> list[dict[str, Any]]:
    cfg = get_config().ai

    providers: list[dict[str, Any]] = []

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


async def _bootstrap_model_center_from_env_uncached(force: bool = False) -> None:
    async with get_db() as db:
        if force:
            for model in (await db.execute(select(AIModelConfig))).scalars().all():
                await db.delete(model)
            for provider in (
                (await db.execute(select(AIProviderConfig))).scalars().all()
            ):
                await db.delete(provider)
            await db.commit()

        existing_provider_id = (
            await db.execute(select(AIProviderConfig.id).limit(1))
        ).scalar_one_or_none()
        if not force and existing_provider_id is not None:
            logger.debug("Model center already initialized; env bootstrap skipped")
            return

        providers = _env_provider_seed()
        if not providers:
            logger.debug("No provider credentials in env; skip model-center bootstrap")
            return

        provider_by_name: dict[str, AIProviderConfig] = {}
        provider_by_key: dict[str, AIProviderConfig] = {
            provider.provider_key: provider
            for provider in (await db.execute(select(AIProviderConfig))).scalars().all()
        }
        for i, item in enumerate(providers, start=1):
            provider = provider_by_key.get(item["provider_key"])
            if provider is None:
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
            else:
                provider.provider_name = provider.provider_name or item["provider_name"]
                provider.display_name = provider.display_name or item["display_name"]
                provider.api_style = provider.api_style or item.get(
                    "api_style", "openai_compatible"
                )
                provider.base_url = provider.base_url or item.get("base_url")
                provider.api_key = provider.api_key or item.get("api_key")
                provider.extra_config = provider.extra_config or item.get(
                    "extra_config", {}
                )
                provider.is_active = True
            provider_by_name[provider.provider_name] = provider

        await db.flush()

        ai_cfg = get_config().ai
        chain = ai_cfg.model_chain

        if not chain:
            logger.info(
                "No model chain in env; model-center providers seeded without models"
            )
            await db.commit()
            return

        existing_model_keys = {
            model.model_key
            for model in (await db.execute(select(AIModelConfig))).scalars().all()
        }

        route_specs: list[tuple[str, str]] = []
        for model_name in chain:
            route_specs.append(
                (
                    _infer_provider_from_model(model_name) or ai_cfg.default_provider,
                    model_name,
                )
            )

        represented_providers = {provider_name for provider_name, _ in route_specs}
        default_provider = ai_cfg.default_provider
        if (
            default_provider in provider_by_name
            and default_provider not in represented_providers
        ):
            for fallback_name in (ai_cfg.default_model, ai_cfg.fallback_model):
                if (
                    fallback_name
                    and (default_provider, fallback_name) not in route_specs
                ):
                    route_specs.append((default_provider, fallback_name))

        provider_default_models: dict[str, list[str]] = {
            "glm": [
                ai_cfg.default_model
                if _infer_provider_from_model(ai_cfg.default_model) == "glm"
                else "glm-4-7",
                ai_cfg.fallback_model
                if _infer_provider_from_model(ai_cfg.fallback_model) == "glm"
                else "glm-4-plus",
            ],
        }
        for provider_name, model_names in provider_default_models.items():
            if (
                provider_name not in provider_by_name
                or provider_name in represented_providers
            ):
                continue
            for model_name in model_names:
                if model_name and (provider_name, model_name) not in route_specs:
                    route_specs.append((provider_name, model_name))

        for idx, (preferred_provider, model_name) in enumerate(route_specs, start=1):
            provider = provider_by_name.get(preferred_provider)
            if provider is None and provider_by_name:
                provider = list(provider_by_name.values())[0]
            if provider is None:
                continue

            model_key = f"{provider.provider_key}:{model_name}"
            if model_key in existing_model_keys:
                continue

            model = AIModelConfig(
                provider_id=provider.id,
                model_key=model_key,
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


async def bootstrap_model_center_from_env(force: bool = False) -> None:
    """Seed provider/model records from env when needed.

    Non-forced calls are process-locally cached for a short interval and
    concurrent callers share one database verification.  The finite cache
    still detects an out-of-band database clear, while ``force=True`` always
    bypasses it and retains the explicit destructive rebuild behavior.
    """

    engine = get_engine()
    if force:
        # Make concurrent non-forced callers join the forced rebuild instead
        # of observing a stale successful check while rows are being replaced.
        invalidate_model_center_bootstrap_cache()
    elif _bootstrap_cache_is_fresh(engine):
        return

    async with _bootstrap_lock():
        if not force and _bootstrap_cache_is_fresh(engine):
            return

        await ensure_model_center_tables()
        await _bootstrap_model_center_from_env_uncached(force=force)
        _mark_bootstrap_cache_checked(engine)


async def get_runtime_routes() -> list[dict[str, Any]]:
    """Return all enabled runtime routes with routing availability metadata."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        rows = (
            (
                await db.execute(
                    select(AIModelConfig)
                    .options(selectinload(AIModelConfig.provider))
                    .join(
                        AIProviderConfig,
                        AIModelConfig.provider_id == AIProviderConfig.id,
                    )
                    .where(
                        AIModelConfig.is_enabled.is_(True),
                        AIProviderConfig.is_active.is_(True),
                    )
                    .order_by(
                        AIModelConfig.priority.asc(), AIProviderConfig.priority.asc()
                    )
                )
            )
            .scalars()
            .all()
        )

    return routes.build_runtime_routes(rows)


async def get_active_model_routes() -> list[dict[str, Any]]:
    """Return routable model routes ordered by model priority."""
    routes = await get_runtime_routes()
    return [route for route in routes if route.get("available_for_routing")]


async def live_model_permits(*, ceiling: int | None = None) -> int:
    """Model calls the center admits at once: the summed permits of routable routes.

    The one sizing rule for every AI pool (content review, Situation, ...). A pool
    that keeps this many items in flight fills the dispatcher without claiming a
    fixed batch ahead of the routes that can run it. Never below one, so an
    unreadable or empty center degrades to serial work instead of stopping it.
    """

    try:
        routes = await get_runtime_routes()
    except Exception as exc:  # noqa: BLE001 - a capacity miss must not abort the caller
        logger.warning("Could not read model capacity; pool keeps one slot | {}", exc)
        return 1
    total = 0
    for route in routes:
        if not route.get("available_for_routing"):
            continue
        try:
            total += max(0, int(route.get("runtime_model_capacity") or 0))
        except (TypeError, ValueError):
            continue
    total = max(1, total)
    return min(ceiling, total) if ceiling else total


async def discover_provider_models_by_id(provider_id: int) -> dict[str, Any]:
    """Read a provider's API model catalogue without altering routing state.

    Catalogue access proves credentials and endpoint reachability only. Routes
    become available exclusively after a structured completion probe succeeds.
    """
    await bootstrap_model_center_from_env(force=False)
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id)
        if provider is None:
            return {
                "success": False,
                "status": "not_found",
                "message": "Provider not found",
                "models": [],
            }
        if not provider.api_key:
            return {
                "success": False,
                "status": "invalid",
                "message": "API key is not configured",
                "models": [],
            }
        # Anthropic-format gateways on this host publish the same /v1/models
        # catalogue. Strip a stored /v1 suffix first so the client does not
        # request /v1/v1/models. A provider with no base URL stays on the
        # vendor default, which has no OpenAI catalogue.
        style = str(provider.api_style or "openai_compatible").lower()
        base_url = str(provider.base_url or "").rstrip("/")
        if style == "anthropic":
            normalized = options.anthropic_sdk_base_url(base_url)
            if normalized is None:
                return {
                    "success": False,
                    "status": "unsupported",
                    "message": "Anthropic does not expose an OpenAI-compatible model catalogue",
                    "models": [],
                }
            base_url = f"{normalized}/v1"
        api_key = provider.api_key
        headers = provider.extra_headers or {}

    candidates = options._openai_base_url_candidates(base_url)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            async with probes.client_session(
                AsyncOpenAI,
                api_key=api_key,
                base_url=candidate,
                default_headers=headers or None,
                timeout=60.0,
                max_retries=0,
            ) as client:
                models = responses._model_ids_from_catalogue(await client.models.list())
            message = f"Discovered {len(models)} API model(s)"
            async with get_db() as db:
                provider = await db.get(
                    AIProviderConfig, provider_id, with_for_update=True
                )
                if provider is not None:
                    config = dict(provider.extra_config or {})
                    config["model_discovery"] = {
                        "status": "available",
                        "count": len(models),
                        "checked_at": health._utcnow().isoformat(),
                    }
                    provider.extra_config = config
                    await db.commit()
            return {
                "success": True,
                "status": "available",
                "message": message,
                "models": models,
                "base_url": candidate or "default-openai-base-url",
            }
        except Exception as exc:
            last_error = exc
            if getattr(exc, "status_code", None) != 404:
                break
    return {
        "success": False,
        "status": errors._health_check_failure_status(
            last_error or RuntimeError("Model catalogue request failed")
        ),
        "message": str(last_error or "Model catalogue request failed"),
        "models": [],
    }


async def _store_native_files_probe(model_id: int, supported: bool) -> None:
    from src.ai.attachments import PROBE_KEY

    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        if model is None:
            return
        params = dict(model.extra_params or {})
        params[PROBE_KEY] = {"supported": supported, "checked_at_epoch": time.time()}
        model.extra_params = params
        await db.commit()


async def test_route_connection(
    route: dict[str, Any], *, structured: bool = False
) -> dict[str, Any]:
    """Test one model route with a connection or structured-workload probe."""
    from src.ai.runtime import require_dispatcher

    require_dispatcher()
    style = str(route.get("api_style") or "openai_compatible").lower()
    model_name = str(route.get("model_name") or "")

    try:
        if not route.get("api_key"):
            raise RuntimeError("API key is not configured")

        if style == "anthropic":
            details = await probes.probe_anthropic(
                route, model_name, client_factory=AsyncAnthropic, structured=structured
            )
        else:
            details = await probes.probe_openai(
                route, model_name, client_factory=AsyncOpenAI, structured=structured
            )

        probe_name = (
            "Literature workload probe" if structured else "Chat completion test"
        )
        marker = (
            "globalid-structured-probe-ok" if structured else probes._MODEL_TEST_MARKER
        )
        message = f"{probe_name} successful. Response: {details.get('response_preview') or marker}"

        return {
            "success": True,
            "status": "available",
            "message": message,
            "model_name": model_name,
            "test_type": "literature_workload" if structured else "chat_completion",
            "test_prompt": probes._STRUCTURED_MODEL_TEST_PROMPT
            if structured
            else probes._MODEL_TEST_PROMPT,
            **details,
        }
    except Exception as exc:
        failure_scope = (
            "provider"
            if errors.is_provider_authentication_error(exc)
            else "model"
            if errors.is_model_unavailable_error(exc)
            else None
        )
        return {
            "success": False,
            "status": errors._health_check_failure_status(exc),
            "failure_scope": failure_scope,
            "message": str(exc),
            "model_name": model_name,
            "test_type": "literature_workload" if structured else "chat_completion",
            "test_prompt": probes._STRUCTURED_MODEL_TEST_PROMPT
            if structured
            else probes._MODEL_TEST_PROMPT,
            "retry_after_seconds": errors.extract_retry_after_seconds(exc),
        }


async def update_model_check_result(model_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        if model is None:
            return
        # A failed probe is not a configuration verdict. Preserve any prior
        # hard-unavailable finding until a success proves recovery or an
        # explicit permanent failure is observed again.
        if (
            status in {"available", "unavailable", "invalid"}
            or str(model.last_check_status or "").lower() != "unavailable"
            or errors._legacy_transient_probe_unavailable(model.last_check_message)
        ):
            model.last_check_status = status
            model.last_check_message = message
        model.last_checked_at = health._utcnow()
        if status == "available":
            model.extra_params = health._write_runtime_success(
                health._clear_payload_rate_limit(
                    model.extra_params, model.last_checked_at
                ),
                occurred_at=model.last_checked_at,
                duration_seconds=None,
            )
        await db.commit()


async def update_provider_check_result(
    provider_id: int, status: str, message: str
) -> None:
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id, with_for_update=True)
        if provider is None:
            return
        # Provider responses may include an echoed token or authorization
        # header. Keep the control-plane status actionable without persisting
        # any upstream credential detail.
        safe_message = (
            _PROVIDER_AUTHENTICATION_FAILURE_MESSAGE
            if errors.is_provider_authentication_error(message)
            else message
        )
        if (
            status in {"available", "unavailable", "invalid"}
            or str(provider.last_check_status or "").lower() != "unavailable"
            or errors._legacy_transient_probe_unavailable(provider.last_check_message)
        ):
            provider.last_check_status = status
            provider.last_check_message = safe_message
        provider.last_checked_at = health._utcnow()
        if status == "available":
            provider.extra_config = health._write_runtime_success(
                health._clear_payload_rate_limit(
                    provider.extra_config, provider.last_checked_at
                ),
                occurred_at=provider.last_checked_at,
                duration_seconds=None,
            )
        await db.commit()


async def mark_route_rate_limited(
    route: dict[str, Any],
    message: str,
    retry_after_seconds: int | None = None,
) -> dict[str, Any]:
    """Persist a model-only cooldown after a runtime rate-limit error."""
    route_extra_params = route.get("extra_params") or {}
    # The configured value is now the escalation ceiling, not the first cooldown.
    ceiling = int(
        route_extra_params.get("rate_limit_cooldown_seconds")
        or get_config().ai.rate_limit_cooldown_seconds
    )

    now = health._utcnow()

    async with get_db() as db:
        model = await db.get(
            AIModelConfig, int(route["model_id"]), with_for_update=True
        )
        cooldown_seconds = retry_after_seconds
        if cooldown_seconds is None:
            streak = health._rate_limit_streak(
                model.extra_params if model is not None else None, now
            )
            cooldown_seconds = health._escalating_rate_limit_cooldown(streak, ceiling)
        cooldown_until = now + timedelta(seconds=max(1, int(cooldown_seconds)))

        if model is not None:
            if str(model.last_check_status or "").lower() != "unavailable":
                model.last_check_status = "rate_limited"
                model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = health._mark_payload_rate_limited(
                model.extra_params, cooldown_until, now
            )

        await db.commit()

    return {
        "cooldown_until": cooldown_until.isoformat(),
        "cooldown_seconds": max(1, int(cooldown_seconds)),
    }


async def record_route_runtime_failure(
    route: dict[str, Any],
    error: BaseException | str,
    *,
    duration_seconds: float | None = None,
    cooldown_seconds: int = 60,
) -> dict[str, Any]:
    """Persist a real production-call transport failure for this model route.

    Rate-limit and invalid-model failures already have durable specialised
    handlers. This function owns timeout, connection and transient 5xx
    failures at model scope; sibling routes must remain independently usable.
    """
    failure = errors.classify_failure(error)
    kind = (
        "upstream"
        if failure.kind is errors.FailureKind.CHANNEL_UNAVAILABLE
        else failure.kind.value
    )
    if kind not in health._RUNTIME_FAILURE_KINDS:
        return {"recorded": False, "reason": "not_a_transport_failure"}

    try:
        model_id = int(route.get("model_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_persistent_ids"}

    now = health._utcnow()
    message = (str(error).strip() or type(error).__name__)[:1000]
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        if model is None:
            return {"recorded": False, "reason": "model_not_found"}

        model.extra_params = health._write_runtime_failure(
            model.extra_params,
            kind=kind,
            error=message,
            occurred_at=now,
            duration_seconds=duration_seconds,
            cooldown_seconds=cooldown_seconds,
        )
        model_state = health._runtime_health_state(model.extra_params, now)

        await db.commit()

    return {
        "recorded": True,
        "kind": kind,
        "cooldown_seconds": model_state["runtime_failure_remaining_seconds"],
        "provider_circuit_open": False,
    }


async def record_route_runtime_success(
    route: dict[str, Any],
    *,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Persist a successful production completion and its observed latency."""
    try:
        model_id = int(route.get("model_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_persistent_ids"}

    now = health._utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        if model is None:
            return {"recorded": False, "reason": "model_not_found"}

        model.extra_params = health._write_runtime_success(
            model.extra_params,
            occurred_at=now,
            duration_seconds=duration_seconds,
        )
        await db.commit()

    return {"recorded": True}


async def mark_route_unavailable(
    route: dict[str, Any],
    message: str,
) -> None:
    """Persist model-level unavailable status when provider reports model_not_found/no_access."""
    now = health._utcnow()
    async with get_db() as db:
        model = await db.get(
            AIModelConfig, int(route["model_id"]), with_for_update=True
        )
        if model is not None:
            model.last_check_status = "unavailable"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = health._clear_payload_rate_limit(
                model.extra_params, now
            )
            await db.commit()


async def mark_provider_authentication_failed(
    route: dict[str, Any],
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    """Durably block every route sharing rejected provider credentials.

    The database intentionally stores a generic remediation message instead
    of the upstream error because provider errors can echo credential material.
    Provider activation is left unchanged so an operator can update the
    credential and explicitly recover the provider through its connection test.
    """
    try:
        provider_id = int(route.get("provider_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_provider_id"}

    now = health._utcnow()
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id, with_for_update=True)
        if provider is None:
            return {"recorded": False, "reason": "provider_not_found"}

        provider.last_check_status = "unavailable"
        provider.last_check_message = _PROVIDER_AUTHENTICATION_FAILURE_MESSAGE
        provider.last_checked_at = now
        provider.extra_config = health._clear_payload_rate_limit(
            provider.extra_config, now
        )
        await db.commit()

    # Keep the parameter for an expressive call site while ensuring raw
    # upstream errors never enter durable provider status fields.
    del error
    return {
        "recorded": True,
        "provider_id": provider_id,
        "status": "unavailable",
    }


async def clear_route_rate_limit(
    route: dict[str, Any], message: str = "Connection successful"
) -> None:
    """Clear the model route cooldown after that route has recovered."""
    now = health._utcnow()
    async with get_db() as db:
        model = await db.get(
            AIModelConfig, int(route["model_id"]), with_for_update=True
        )

        if model is not None:
            model.last_check_status = "available"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = health._clear_payload_rate_limit(
                model.extra_params, now
            )

        await db.commit()


async def _refresh_native_files_probe(model_id: int, route: dict[str, Any]) -> None:
    """Probe file support after a healthy check unless an operator override or a fresh result exists."""
    from src.ai.attachments import probe_is_fresh

    params = route.get("extra_params") or {}
    if isinstance(params.get("runtime_native_files"), bool) or probe_is_fresh(params):
        return
    supported = await probes.probe_native_file(
        route, openai_factory=AsyncOpenAI, anthropic_factory=AsyncAnthropic
    )
    if supported is not None:
        await _store_native_files_probe(model_id, supported)


async def check_model_by_id(
    model_id: int, *, structured: bool = True
) -> dict[str, Any]:
    """Test one model route by DB id and persist status.

    The default structured probe catches the common false-positive where a
    provider can echo a short marker but cannot reliably return JSON payloads.
    """
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
            return {
                "success": False,
                "status": "not_found",
                "message": "Model not found",
            }

        provider = model.provider
        if provider is None:
            return {
                "success": False,
                "status": "invalid",
                "message": "Provider not found",
            }

        route = {
            "model_id": model.id,
            "model_key": model.model_key,
            "model_name": model.model_name,
            "provider_id": provider.id,
            "provider_key": provider.provider_key,
            "provider_name": provider.provider_name,
            "api_style": model.api_style or provider.api_style,
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "extra_headers": provider.extra_headers or {},
            "extra_config": provider.extra_config or {},
            "extra_params": model.extra_params or {},
            "temperature": model.temperature,
            "max_tokens": model.max_tokens,
        }

    check_started_at = time.perf_counter()
    result = await test_route_connection(route, structured=structured)
    check_error = (
        None
        if result.get("success")
        else RuntimeError(
            str(result.get("message") or "Model-center health check failed")
        )
    )
    await record_model_call_log(
        request_id=uuid4(),
        route=route,
        model_name=str(route["model_name"]),
        provider_key=str(route["provider_key"]),
        agent_name="model_center_health_check",
        status="success" if result.get("success") else "failed",
        latency_ms=(time.perf_counter() - check_started_at) * 1000,
        error=check_error,
    )
    if result["status"] == "rate_limited":
        await mark_route_rate_limited(
            route,
            result["message"],
            retry_after_seconds=result.get("retry_after_seconds"),
        )
    await update_model_check_result(model_id, result["status"], result["message"])
    if result.get("success"):
        await _refresh_native_files_probe(model_id, route)
        await update_provider_check_result(
            route["provider_id"], result["status"], result["message"]
        )
    elif result["status"] == "unavailable" and (
        result.get("failure_scope") == "provider"
        or errors.is_provider_authentication_error(result["message"])
    ):
        await update_provider_check_result(
            route["provider_id"], result["status"], result["message"]
        )
    return result


async def check_provider_by_id(
    provider_id: int, *, structured: bool = True
) -> dict[str, Any]:
    """Test enabled provider models until one structured call succeeds."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        models = (
            (
                await db.execute(
                    select(AIModelConfig)
                    .options(selectinload(AIModelConfig.provider))
                    .where(
                        AIModelConfig.provider_id == provider_id,
                        AIModelConfig.is_enabled.is_(True),
                    )
                    .order_by(AIModelConfig.priority.asc())
                )
            )
            .scalars()
            .all()
        )

    if not models:
        result = {
            "success": False,
            "status": "invalid",
            "message": "No enabled model under this provider",
        }
        await update_provider_check_result(
            provider_id, result["status"], result["message"]
        )
        return result

    # Probe models in parallel under the same provider bound as check_all_models.
    # The semaphore is FIFO, so configured priority still decides start order,
    # and the first success cancels the remaining probes.
    provider = models[0].provider
    provider_capacity = sum(
        routes.RouteCapacity.from_route({"extra_params": model.extra_params}).model
        for model in models
    )
    semaphore = asyncio.Semaphore(
        probes.health_check_concurrency(
            {
                "extra_config": (provider.extra_config if provider else None) or {},
                "runtime_provider_max_capacity": provider_capacity,
            }
        )
    )

    async def _check(model: AIModelConfig) -> dict[str, Any]:
        async with semaphore:
            result = await check_model_by_id(model.id, structured=structured)
        result["model_name"] = model.model_name
        return result

    tasks = [asyncio.create_task(_check(model)) for model in models]
    model_results: list[dict[str, Any]] = []
    try:
        for finished in asyncio.as_completed(tasks):
            result = await finished
            if result.get("success"):
                return result
            model_results.append(result)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    status, message = health._provider_check_result_from_model_results(model_results)
    result = {
        "success": False,
        "status": status,
        "message": message,
        "models": model_results,
    }
    await update_provider_check_result(provider_id, result["status"], result["message"])
    return result


async def check_all_models(*, structured: bool = True) -> list[dict[str, Any]]:
    """Test all routes with realistic, provider-bounded parallel workload probes."""
    routes = await get_runtime_routes()
    semaphores: dict[int, asyncio.Semaphore] = {}
    for route in routes:
        provider_id = int(route["provider_id"])
        semaphores.setdefault(
            provider_id,
            asyncio.Semaphore(probes.health_check_concurrency(route)),
        )

    async def _check(route: dict[str, Any]) -> dict[str, Any]:
        provider_id = int(route["provider_id"])
        if route.get("routing_status") == "outside_window":
            return {
                "success": True,
                "status": "skipped",
                "message": "Skipped: outside the model's availability window",
                "model_id": route["model_id"],
                "provider_id": provider_id,
                "provider_key": route["provider_key"],
                "model_name": route.get("model_name"),
            }
        async with semaphores[provider_id]:
            result = await test_route_connection(route, structured=structured)
            if result["status"] == "rate_limited":
                await mark_route_rate_limited(
                    route,
                    result["message"],
                    retry_after_seconds=result.get("retry_after_seconds"),
                )
            await update_model_check_result(
                route["model_id"], result["status"], result["message"]
            )
            result.update(
                {
                    "model_id": route["model_id"],
                    "provider_id": provider_id,
                    "provider_key": route["provider_key"],
                    "provider_probe_concurrency": probes.health_check_concurrency(
                        route
                    ),
                }
            )
            return result

    results = list(await asyncio.gather(*(_check(route) for route in routes)))
    provider_results: dict[int, list[dict[str, Any]]] = {}
    for result in results:
        provider_results.setdefault(int(result["provider_id"]), []).append(result)
    for provider_id, provider_model_results in provider_results.items():
        checked = [
            item for item in provider_model_results if item.get("status") != "skipped"
        ]
        if not checked:
            continue
        status, message = health._provider_check_result_from_model_results(checked)
        await update_provider_check_result(provider_id, status, message)
    return results


async def probe_degraded_model_routes(
    *,
    limit: int = 2,
    minimum_interval_seconds: int = _MODEL_RECOVERY_PROBE_INTERVAL_SECONDS,
) -> list[dict[str, Any]]:
    """Run bounded realistic probes so chronically failed routes can recover.

    Production admission excludes a chronic route, so it cannot generate the
    success that clears its own circuit. The worker calls this function in a
    separate low-rate sweep after ordinary cooldowns have expired.
    """
    due = routes.recovery_probe_candidates(
        await get_runtime_routes(),
        now=health._utcnow(),
        limit=limit,
        minimum_interval_seconds=minimum_interval_seconds,
    )
    results: list[dict[str, Any]] = []
    for route in due:
        model_id = int(route["model_id"])
        result = await check_model_by_id(model_id, structured=True)
        results.append(
            {
                "model_id": model_id,
                "model_key": route.get("model_key"),
                "success": bool(result.get("success")),
                "status": result.get("status"),
                "message": result.get("message"),
            }
        )
    return results
