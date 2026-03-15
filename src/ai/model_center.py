"""AI model center service: schema bootstrap, routing, and health checks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core import get_config, get_db, get_engine, get_logger
from src.domain import AIModelConfig, AIProviderConfig

logger = get_logger(__name__)

_schema_ready = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


async def get_active_model_routes() -> List[Dict[str, Any]]:
    """Return active model routes ordered by model priority."""
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
            if not provider or not provider.api_key:
                continue

            routes.append(
                {
                    "model_id": model.id,
                    "model_key": model.model_key,
                    "model_name": model.model_name,
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
                }
            )

        return routes


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
            "status": "unavailable",
            "message": str(exc),
            "model_name": model_name,
        }


async def update_model_check_result(model_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id)
        if model is None:
            return
        model.last_check_status = status
        model.last_check_message = message
        model.last_checked_at = _utcnow()
        await db.commit()


async def update_provider_check_result(provider_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id)
        if provider is None:
            return
        provider.last_check_status = status
        provider.last_check_message = message
        provider.last_checked_at = _utcnow()
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
