"""
GlobalID V2 AI Base Agent

AI Agent Base Class - Provides unified LLM interaction functionality with multi-platform AI provider support
"""

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.ai.catalogue.errors import FailureKind, classify_failure
from src.ai.model_center import (
    clear_route_rate_limit,
    mark_provider_authentication_failed,
    mark_route_rate_limited,
    mark_route_unavailable,
    record_model_call_log,
    record_route_runtime_failure,
    record_route_runtime_success,
)
from src.ai.providers import ProviderTransport, sanitize_completion_params
from src.core import get_cache, get_config, get_logger

logger = get_logger(__name__)


class NoModelAvailable(Exception):
    """No route was even tried: a platform-wide condition, not a fault of the request."""


class BaseAgent(ABC):
    """
    AI Agent Base Class

    Provides durable invocation, route-specific caching, and provider transport.
    Supports multiple AI providers: OpenAI, Anthropic, QianWen, Azure, and more.
    """

    def __init__(
        self,
        name: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        """
        Initialize the agent.

        Args:
            name: Agent name
            temperature: Sampling temperature
            max_tokens: Output-budget hint retained in prompts and provenance
        """
        self.name = name
        self.config = get_config()
        self.cache = get_cache()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.transport: ProviderTransport | None = None

        # 对话历史记录
        self.conversation_history = []

        # Provider clients are constructed only for the dispatcher-selected route.

        logger.debug("Agent '{}' submits completions to the model dispatcher", name)

    @staticmethod
    def _decode_cached_completion(cached: Any) -> Tuple[Optional[str], Dict[str, Any]]:
        if isinstance(cached, dict):
            response = cached.get("response")
            if isinstance(response, str):
                return response, cached
        return None, {}

    def _append_conversation_entry(
        self,
        *,
        prompt: str,
        system: Optional[str],
        response_text: str,
        provider: Optional[str],
        model: Optional[str] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        duration: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conversation_entry = {
            "agent": self.name.lower(),
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt,
            "system_prompt": system,
            "response": response_text,
            "model": model,
            "provider": provider,
            "tokens": token_usage or {},
            "duration": round(duration, 2),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if metadata:
            conversation_entry["metadata"] = metadata
        self.conversation_history.append(conversation_entry)

    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        use_cache: bool = True,
        **kwargs,
    ) -> str:
        """Submit durable business work; provider execution belongs to the worker."""
        from src.ai.runtime import in_dispatcher, submit_completion

        if not in_dispatcher():
            return await submit_completion(self, prompt, system, use_cache, kwargs)
        if kwargs.get("_scheduled_route") is None:
            raise RuntimeError("Model dispatcher must pin an admitted route")
        return await self._complete_transport(
            prompt, system=system, use_cache=use_cache, **kwargs
        )

    async def _complete_transport(
        self,
        prompt: str,
        system: Optional[str] = None,
        use_cache: bool = True,
        **kwargs,
    ) -> str:
        """Execute exactly one dispatcher-selected route; the queue owns retries.

        Provider exceptions retain their types and causes. Cache and telemetry
        failures never turn an already successful generation into another call.
        """
        from src.ai.runtime import require_dispatcher

        require_dispatcher()
        route = kwargs.pop("_scheduled_route", None)
        if not route:
            raise NoModelAvailable(
                "Model dispatcher must select a route before transport"
            )
        if not route.get("available_for_routing", True):
            raise NoModelAvailable("Dispatcher-selected route is no longer routable")
        timeout = kwargs.pop("model_request_timeout_seconds", None)
        cooldown = kwargs.pop("timeout_cooldown_seconds", None)
        model = str(route["model_name"])
        provider = str(
            route.get("provider_key") or route.get("provider_name") or "runtime"
        )
        self._last_input_mode = None
        use_cache = (
            use_cache and self.config.ai.enable_cache and not kwargs.get("attachments")
        )
        cache_key = (
            self._make_cache_key(prompt, system, route=route, params=kwargs)
            if use_cache
            else None
        )
        if cache_key:
            try:
                cached = await self.cache.get(cache_key)
            except Exception as exc:
                logger.warning("Completion cache read failed: {}", type(exc).__name__)
                cached = None
            response, meta = self._decode_cached_completion(cached)
            if response and response.strip() and meta.get("model") == model:
                self._append_conversation_entry(
                    prompt=prompt,
                    system=system,
                    response_text=response,
                    provider=provider,
                    model=model,
                    token_usage=meta.get("token_usage") or {},
                    duration=0.0,
                    metadata={
                        "cache_hit": True,
                        "cache_key": cache_key,
                        "runtime_route": {
                            key: route.get(key)
                            for key in (
                                "model_id",
                                "provider_id",
                                "model_key",
                                "model_name",
                                "provider_key",
                            )
                        },
                    },
                )
                return response
        if self.transport is None:
            raise RuntimeError("Dispatcher must provide its provider transport")
        request_id = uuid4()
        started = time.perf_counter()
        try:
            response, tokens, self._last_input_mode = await self.transport.complete(
                route,
                prompt,
                system,
                temperature=self.temperature,
                attachments=kwargs.pop("attachments", None),
                params=kwargs,
                request_timeout_seconds=timeout,
            )
            if not isinstance(response, str) or not response.strip():
                raise RuntimeError("Model returned an empty completion response")
        except Exception as exc:
            elapsed = time.perf_counter() - started
            await self._record_attempt(
                request_id, route, model, provider, elapsed, error=exc
            )
            failure = classify_failure(exc)
            try:
                if failure.kind is FailureKind.AUTHENTICATION:
                    await mark_provider_authentication_failed(route, exc)
                elif failure.kind is FailureKind.RATE_LIMIT:
                    await mark_route_rate_limited(
                        route, str(exc), retry_after_seconds=failure.retry_after_seconds
                    )
                elif failure.kind is FailureKind.MODEL_UNAVAILABLE:
                    await mark_route_unavailable(route, str(exc))
                elif failure.scope == "model":
                    await record_route_runtime_failure(
                        route,
                        exc,
                        duration_seconds=elapsed,
                        cooldown_seconds=max(
                            5,
                            int(cooldown or self.config.ai.rate_limit_cooldown_seconds),
                        ),
                    )
            except Exception as health_error:
                logger.warning(
                    "Could not persist route failure: {}", type(health_error).__name__
                )
            raise
        elapsed = time.perf_counter() - started
        await self._record_attempt(request_id, route, model, provider, elapsed)
        try:
            await record_route_runtime_success(route, duration_seconds=elapsed)
            if (
                route.get("rate_limit_count")
                or route.get("last_check_status") == "rate_limited"
            ):
                await clear_route_rate_limit(
                    route, "Connection recovered after cooldown"
                )
        except Exception as exc:
            logger.warning("Could not persist route success: {}", type(exc).__name__)
        self._append_conversation_entry(
            prompt=prompt,
            system=system,
            response_text=response,
            provider=provider,
            model=model,
            token_usage=tokens,
            duration=elapsed,
            metadata={
                "cache_hit": False,
                "cache_key": cache_key,
                "runtime_route": {
                    key: route.get(key)
                    for key in (
                        "model_id",
                        "provider_id",
                        "model_key",
                        "model_name",
                        "provider_key",
                    )
                },
            },
        )
        if cache_key:
            try:
                await self.cache.set(
                    cache_key,
                    {
                        "response": response,
                        "token_usage": tokens,
                        "model": model,
                        "provider": provider,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                    },
                    ttl=self.config.ai.cache_ttl * 3600,
                )
            except Exception as exc:
                logger.warning("Completion cache write failed: {}", type(exc).__name__)
        return response

    async def _record_attempt(
        self, request_id, route, model, provider, duration, *, error=None
    ):
        try:
            await record_model_call_log(
                request_id=request_id,
                route=route,
                model_name=model,
                provider_key=provider,
                agent_name=self.name,
                status="failed" if error is not None else "success",
                latency_ms=duration * 1000,
                error=error,
            )
        except Exception as exc:
            logger.warning("Could not record model attempt: {}", type(exc).__name__)

    def _make_cache_key(
        self, prompt: str, system: Optional[str] = None, *, route=None, params=None
    ) -> str:
        """Route and generation options are part of the result's identity."""
        import hashlib
        import json

        route = route or {}
        temperature = route.get("temperature")
        content = {
            "agent": self.name,
            "route": {
                key: route.get(key)
                for key in (
                    "model_key",
                    "model_name",
                    "model_id",
                    "provider_id",
                    "api_style",
                    "base_url",
                )
            },
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "system": system,
            "prompt": prompt,
            "params": sanitize_completion_params(params or {}),
            "route_params": sanitize_completion_params(route.get("extra_params") or {}),
        }
        encoded = json.dumps(
            content, sort_keys=True, ensure_ascii=False, default=str
        ).encode()
        return f"agent:v2:{hashlib.sha256(encoded).hexdigest()}"

    async def invalidate_completion_cache(
        self, *, prompt: str, system: Optional[str] = None
    ) -> bool:
        """Invalidate the actual dispatched result, including a replayed conversation."""
        if not self.config.ai.enable_cache:
            return False
        keys = {
            entry.get("metadata", {}).get("cache_key")
            for entry in self.conversation_history
            if entry.get("prompt") == prompt and entry.get("system_prompt") == system
        }
        keys.discard(None)
        try:
            results = [await self.cache.delete(key) for key in keys]
            return any(results)
        except Exception as exc:
            logger.warning(
                "Failed to invalidate AI completion cache: {}", type(exc).__name__
            )
            return False

    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """获取对话历史记录"""
        return self.conversation_history.copy()

    def clear_conversation_history(self):
        """清除对话历史记录"""
        self.conversation_history = []

    def get_latest_conversation(self) -> Optional[Dict[str, Any]]:
        """获取最新的对话记录"""
        return self.conversation_history[-1] if self.conversation_history else None

    @abstractmethod
    async def process(self, **kwargs) -> Dict[str, Any]:
        """
        Process a task (must be implemented by subclasses).

        Args:
            **kwargs: Task-specific parameters

        Returns:
            Processing results
        """
        pass
