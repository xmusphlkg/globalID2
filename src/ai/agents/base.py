"""
GlobalID V2 AI Base Agent

AI Agent Base Class - Provides unified LLM interaction functionality with multi-platform AI provider support
"""
import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from src.core import get_cache, get_config, get_logger, RateLimiter
from src.ai.model_center import (
    acquire_runtime_route_admission,
    clear_route_rate_limit,
    extract_retry_after_seconds,
    get_active_model_routes,
    get_runtime_routes,
    is_model_unavailable_error,
    is_rate_limit_error,
    mark_model_unavailable_by_name,
    record_route_runtime_failure,
    record_route_runtime_success,
    mark_route_rate_limited,
    mark_route_unavailable,
    runtime_route_admission_score,
)

logger = get_logger(__name__)


class BaseAgent(ABC):
    """
    AI Agent Base Class
    
    Provides common LLM invocation, caching, retry, and rate-limiting functionality.
    Supports multiple AI providers: OpenAI, Anthropic, QianWen, Azure, and more.
    """
    
    # Process-wide cooldowns used when a model/route hits quota or rate-limit errors.
    MODEL_COOLDOWNS: Dict[str, float] = {}

    # 启动时检查得到的可用模型列表（保持优先级顺序）。若不为 None，complete() 仅从该列表中选模型。
    AVAILABLE_MODEL_CHAIN: Optional[List[str]] = None
    # 模型中心返回的运行时路由（含 provider/api_key/base_url），若存在则优先使用。
    AVAILABLE_MODEL_ROUTES: Optional[List[Dict[str, Any]]] = None
    AVAILABLE_MODEL_ROUTES_LOADED_AT: Optional[float] = None
    # Route 级别冷却（provider:model），避免某条路由持续触发限流。
    ROUTE_COOLDOWNS: Dict[str, float] = {}
    # Model-center internal metadata keys that must never be forwarded to completion APIs.
    INTERNAL_COMPLETION_PARAM_KEYS = {
        "routing_state",
        "rate_limit_cooldown_seconds",
        "cooldown_until",
        "last_rate_limit_at",
        "last_recovered_at",
        "rate_limit_count",
        "runtime_cooldown_until",
        "runtime_failure_streak",
        "runtime_failure_count",
        "runtime_timeout_count",
        "runtime_success_count",
        "runtime_latency_ewma_ms",
        "runtime_last_latency_ms",
        "last_runtime_failure_kind",
        "last_runtime_failure_at",
        "last_runtime_success_at",
        "last_runtime_error",
    }
    
    def __init__(
        self,
        name: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: Optional[str] = None,
    ):
        """
        Initialize the agent.

        Args:
            name: Agent name
            model: Model to use (defaults to configured model)
            temperature: Sampling temperature
            max_tokens: Maximum number of tokens to generate
            provider: AI provider (optional, defaults to configured provider)
        """
        self.name = name
        self.config = get_config()
        self.cache = get_cache()
        self.rate_limiter = RateLimiter(
            max_requests=self.config.ai.rate_limit,
            window_seconds=60,
        )
        
        # 提供商和模型配置：若配置了 model_chain，优先用链中首个模型，避免使用 default_model（如 glm-4-7）当用户未配置时
        chain = getattr(self.config.ai, "model_chain", None) or []
        effective_model = model
        if effective_model is None and chain:
            effective_model = chain[0]
        self.model = effective_model or self.config.ai.default_model
        self.provider = provider or self.config.ai.default_provider
        # 根据模型名推断 provider，避免 qianwen 链却显示 glm 模型
        inferred = self._infer_provider_from_model(self.model)
        if inferred:
            self.provider = inferred
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = self.config.ai.max_retries
        
        # 对话历史记录
        self.conversation_history = []
        
        # 初始化客户端
        self.clients = {}
        self._init_clients()
        
        logger.info(
            "Agent '{}' initialized with bootstrap provider '{}' and model '{}'; "
            "live completions are routed by Model Center.",
            name,
            self.provider,
            self.model,
        )

    @classmethod
    def _purge_expired_cooldowns(cls) -> None:
        now = time.time()
        cls.MODEL_COOLDOWNS = {
            key: until for key, until in cls.MODEL_COOLDOWNS.items() if until > now
        }
        cls.ROUTE_COOLDOWNS = {
            key: until for key, until in cls.ROUTE_COOLDOWNS.items() if until > now
        }

    @classmethod
    def _is_model_cooling_down(cls, model_name: str) -> bool:
        cls._purge_expired_cooldowns()
        return (cls.MODEL_COOLDOWNS.get(model_name) or 0) > time.time()

    @classmethod
    def _is_route_cooling_down(cls, route_key: str) -> bool:
        cls._purge_expired_cooldowns()
        return (cls.ROUTE_COOLDOWNS.get(route_key) or 0) > time.time()

    @classmethod
    def _mark_model_cooling_down(cls, model_name: str, cooldown_seconds: int) -> None:
        cls.MODEL_COOLDOWNS[model_name] = time.time() + max(1, int(cooldown_seconds))

    @classmethod
    def _mark_route_cooling_down(cls, route_key: str, cooldown_seconds: int) -> None:
        cls.ROUTE_COOLDOWNS[route_key] = time.time() + max(1, int(cooldown_seconds))

    @classmethod
    def _cooldown_remaining_seconds(cls, until: Optional[float]) -> int:
        if not until:
            return 0
        return max(0, int(until - time.time()))

    @classmethod
    def _estimate_wait_seconds_for_empty_candidates(
        cls,
        runtime_routes: Optional[List[Dict[str, Any]]],
        chain: Optional[List[str]],
        wait_cap_seconds: int,
    ) -> int:
        """Estimate a bounded wait before retrying when all candidates are cooling down."""
        cls._purge_expired_cooldowns()

        waits: List[int] = []
        if runtime_routes:
            for route in runtime_routes:
                route_key = str(route.get("model_key") or route.get("model_name") or "")
                model_name = str(route.get("model_name") or "")

                route_wait = cls._cooldown_remaining_seconds(cls.ROUTE_COOLDOWNS.get(route_key))
                model_wait = cls._cooldown_remaining_seconds(cls.MODEL_COOLDOWNS.get(model_name))
                if route_wait > 0:
                    waits.append(route_wait)
                if model_wait > 0:
                    waits.append(model_wait)

                try:
                    db_wait = int(route.get("rate_limit_remaining_seconds") or 0)
                except (TypeError, ValueError):
                    db_wait = 0
                if db_wait > 0:
                    waits.append(db_wait)

                try:
                    runtime_wait = int(route.get("runtime_failure_remaining_seconds") or 0)
                except (TypeError, ValueError):
                    runtime_wait = 0
                if runtime_wait > 0:
                    waits.append(runtime_wait)

        for model_name in chain or []:
            model_wait = cls._cooldown_remaining_seconds(cls.MODEL_COOLDOWNS.get(model_name))
            if model_wait > 0:
                waits.append(model_wait)

        if not waits:
            return 1

        return max(1, min(min(waits), max(1, int(wait_cap_seconds))))

    def _build_candidates(
        self,
        runtime_routes: Optional[List[Dict[str, Any]]],
        ignore_local_cooldowns: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        candidates: List[Dict[str, Any]] = []
        chain_used: List[str] = []
        runtime_model_names: list[str] = []

        if runtime_routes is not None:
            for route in runtime_routes:
                route_key = str(route.get("model_key") or route.get("model_name") or "")
                model_name = str(route.get("model_name") or "")
                if not route_key or not model_name:
                    continue
                route_status = str(route.get("last_check_status") or "").strip().lower()
                if route_status == "unavailable":
                    continue
                # A final in-process probe may ignore only transient local
                # cooldown markers. It must never bypass a durable Model
                # Center rate-limit or runtime circuit.
                durably_blocked = bool(route.get("runtime_failure_active")) or bool(
                    route.get("rate_limit_active")
                )
                try:
                    durably_blocked = durably_blocked or int(
                        route.get("rate_limit_remaining_seconds") or 0
                    ) > 0
                except (TypeError, ValueError):
                    pass
                if not bool(route.get("available_for_routing", True)) and (
                    not ignore_local_cooldowns or durably_blocked
                ):
                    continue
                if not ignore_local_cooldowns and BaseAgent._is_route_cooling_down(route_key):
                    continue
                if not ignore_local_cooldowns and BaseAgent._is_model_cooling_down(model_name):
                    continue
                candidates.append(
                    {
                        "route_key": route_key,
                        "model_name": model_name,
                        "route": route,
                    }
                )
                if model_name not in runtime_model_names:
                    runtime_model_names.append(model_name)

            return candidates, list(runtime_model_names)

        # Legacy direct mode is kept only for explicit callers that bypass
        # model-center route loading by passing runtime_routes=None.
        chain = BaseAgent.AVAILABLE_MODEL_CHAIN
        if chain is None:
            chain = getattr(self.config.ai, "model_chain", None) or []
        chain_used = list(chain)

        if chain:
            for model_name in chain:
                if not ignore_local_cooldowns and BaseAgent._is_model_cooling_down(model_name):
                    continue
                candidates.append(
                    {
                        "route_key": model_name,
                        "model_name": model_name,
                        "route": None,
                    }
                )
            return candidates, chain_used

        seen = set()
        if self.model not in seen:
            candidates.append(
                {
                    "route_key": self.model,
                    "model_name": self.model,
                    "route": None,
                }
            )
            seen.add(self.model)
        fallback_model = getattr(self.config.ai, "fallback_model", None)
        if fallback_model and fallback_model not in seen:
            candidates.append(
                {
                    "route_key": fallback_model,
                    "model_name": fallback_model,
                    "route": None,
                }
            )

        return candidates, chain_used

    @staticmethod
    def _prioritize_candidates(
        candidates: List[Dict[str, Any]],
        preferred_models: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return candidates

        def order_runtime_by_admission(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            runtime = [candidate for candidate in group if candidate.get("route") is not None]
            direct = [candidate for candidate in group if candidate.get("route") is None]
            runtime.sort(
                key=lambda candidate: runtime_route_admission_score(candidate["route"]),
            )
            return runtime + direct

        if not preferred_models:
            return order_runtime_by_admission(candidates)

        preferred_order = {
            value.strip(): index
            for index, value in enumerate(preferred_models)
            if isinstance(value, str) and value.strip()
        }
        if not preferred_order:
            return candidates

        def order_group(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            prioritized: list[Dict[str, Any]] = []
            deferred: list[Dict[str, Any]] = []
            for candidate in group:
                model_name = str(candidate.get("model_name") or "").strip()
                route_key = str(candidate.get("route_key") or "").strip()
                if model_name in preferred_order or route_key in preferred_order:
                    prioritized.append(candidate)
                else:
                    deferred.append(candidate)
            prioritized.sort(
                key=lambda candidate: preferred_order.get(
                    str(candidate.get("route_key") or "").strip(),
                    preferred_order.get(
                        str(candidate.get("model_name") or "").strip(),
                        10**6,
                    ),
                )
            )
            return prioritized + deferred

        # A preferred shard may also exist only in the direct environment
        # fallback. It must not jump ahead of model-center routes that have a
        # current healthy status; preference reorders within each route class.
        runtime = [candidate for candidate in candidates if candidate.get("route") is not None]
        direct = [candidate for candidate in candidates if candidate.get("route") is None]
        preferred_runtime = [
            candidate
            for candidate in runtime
            if str(candidate.get("model_name") or "").strip() in preferred_order
            or str(candidate.get("route_key") or "").strip() in preferred_order
        ]
        deferred_runtime = [candidate for candidate in runtime if candidate not in preferred_runtime]
        # Preference selects a requested shard first; live admission pressure
        # balances routes only within that preference band.
        return (
            order_runtime_by_admission(order_group(preferred_runtime))
            + order_runtime_by_admission(order_group(deferred_runtime))
            + order_group(direct)
        )
    
    def _init_clients(self):
        """Initialize clients for supported AI providers."""
        # OpenAI客户端
        if self.config.ai.openai_api_key:
            try:
                self.clients['openai'] = AsyncOpenAI(
                    api_key=self.config.ai.openai_api_key,
                    base_url=self.config.ai.openai_base_url
                )
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI client: {e}")
        
        # GLM (Zhipu) client (uses OpenAI-compatible interface)
        if self.config.ai.glm_api_key:
            try:
                self.clients['glm'] = AsyncOpenAI(
                    api_key=self.config.ai.glm_api_key,
                    base_url=self.config.ai.glm_base_url
                )
            except Exception as e:
                logger.warning(f"Failed to initialize GLM client: {e}")
        
        # QianWen client (uses OpenAI-compatible interface)
        if self.config.ai.qianwen_api_key:
            try:
                self.clients['qianwen'] = AsyncOpenAI(
                    api_key=self.config.ai.qianwen_api_key,
                    base_url=self.config.ai.qianwen_base_url
                )
            except Exception as e:
                logger.warning(f"Failed to initialize QianWen client: {e}")
        
        # Anthropic client
        if self.config.ai.anthropic_api_key:
            try:
                self.clients['anthropic'] = AsyncAnthropic(
                    api_key=self.config.ai.anthropic_api_key
                )
            except Exception as e:
                logger.warning(f"Failed to initialize Anthropic client: {e}")
        
        # Azure OpenAI client
        if self.config.ai.azure_api_key:
            try:
                self.clients['azure'] = AsyncOpenAI(
                    api_key=self.config.ai.azure_api_key,
                    azure_endpoint=self.config.ai.azure_endpoint,
                    api_version=self.config.ai.azure_api_version
                )
            except Exception as e:
                logger.warning(f"Failed to initialize Azure client: {e}")
        
        # Custom client
        if self.config.ai.custom_api_key and self.config.ai.custom_base_url:
            try:
                self.clients['custom'] = AsyncOpenAI(
                    api_key=self.config.ai.custom_api_key,
                    base_url=self.config.ai.custom_base_url
                )
            except Exception as e:
                logger.warning(f"Failed to initialize custom client: {e}")
    
    def _infer_provider_from_model(self, model: str) -> Optional[str]:
        """根据模型名推断 provider，不依赖 self.provider。"""
        if not model:
            return None
        model_lower = model.lower()
        if any(k in model_lower for k in ['glm', 'chatglm', 'zhipu']):
            return 'glm'
        if any(k in model_lower for k in ['qwen', 'qianwen']):
            return 'qianwen'
        if any(k in model_lower for k in ['claude', 'anthropic']):
            return 'anthropic'
        if any(k in model_lower for k in ['gpt-4', 'gpt-3.5', 'text-davinci']):
            return 'openai'
        if 'azure' in model_lower:
            return 'azure'
        return None

    def get_provider_for_model(self, model: str) -> str:
        """Infer provider from model name."""
        inferred = self._infer_provider_from_model(model)
        return inferred if inferred else self.provider

    @classmethod
    def _sanitize_completion_params(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove internal routing/rate-limit metadata before provider calls."""
        sanitized: Dict[str, Any] = {}
        for key, value in (params or {}).items():
            if isinstance(key, str) and key in cls.INTERNAL_COMPLETION_PARAM_KEYS:
                continue
            if isinstance(key, str) and (
                key.startswith("routing_")
                or key.startswith("rate_limit_")
                or key.startswith("runtime_")
            ):
                continue
            sanitized[key] = value
        return sanitized

    @staticmethod
    def _extract_unexpected_kwarg(error: Exception) -> Optional[str]:
        """Parse unexpected keyword argument name from Python TypeError."""
        match = re.search(r"unexpected keyword argument '([^']+)'", str(error))
        return match.group(1) if match else None

    @staticmethod
    def _extract_unsupported_optional_param(
        error: Exception,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        """Identify optional OpenAI-compatible parameters rejected by a proxy.

        Some compatible endpoints accept normal chat completions but reject
        ``response_format``.  That capability difference must not make an
        otherwise healthy Model Center route unavailable.
        """
        message = str(error or "").lower()
        if "response_format" not in payload or "response_format" not in message:
            return None
        rejection_markers = (
            "unsupported",
            "unknown parameter",
            "invalid parameter",
            "unexpected",
            "not allowed",
            "unrecognized",
        )
        return "response_format" if any(marker in message for marker in rejection_markers) else None

    @staticmethod
    def _completion_content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text is not None:
                        parts.append(str(text))
                else:
                    text = getattr(item, "text", None) or getattr(item, "content", None)
                    if text is not None:
                        parts.append(str(text))
            return "\n".join(part for part in parts if part)
        return str(content)

    @classmethod
    def _extract_openai_compatible_response(cls, response: Any) -> Tuple[str, Dict[str, int]]:
        """Normalize OpenAI-compatible responses, including string-returning proxies."""
        token_usage: Dict[str, int] = {}
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if usage:
            prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else getattr(usage, "prompt_tokens", None)
            completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else getattr(usage, "completion_tokens", None)
            total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else getattr(usage, "total_tokens", None)
            token_usage = {
                "prompt": int(prompt_tokens or 0),
                "completion": int(completion_tokens or 0),
                "total": int(total_tokens or ((prompt_tokens or 0) + (completion_tokens or 0))),
            }

        if isinstance(response, str):
            return response, token_usage

        if isinstance(response, dict):
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message")
                    if isinstance(message, dict):
                        return cls._completion_content_to_text(message.get("content")), token_usage
                    return cls._completion_content_to_text(first.get("text") or first.get("content")), token_usage
            output_text = response.get("output_text")
            if output_text is not None:
                return cls._completion_content_to_text(output_text), token_usage
            return cls._completion_content_to_text(response.get("content")), token_usage

        choices = getattr(response, "choices", None)
        if choices:
            first = choices[0]
            message = getattr(first, "message", None)
            if message is not None:
                return cls._completion_content_to_text(getattr(message, "content", None)), token_usage
            return cls._completion_content_to_text(getattr(first, "text", None)), token_usage

        output_text = getattr(response, "output_text", None)
        if output_text is not None:
            return cls._completion_content_to_text(output_text), token_usage
        return cls._completion_content_to_text(getattr(response, "content", response)), token_usage

    async def _safe_openai_completion_create(
        self,
        client: AsyncOpenAI,
        request_payload: Dict[str, Any],
        max_retries: int = 3,
    ) -> Any:
        """Call OpenAI-compatible completion API and auto-drop unsupported kwargs."""
        payload = dict(request_payload or {})
        for _ in range(max(1, max_retries)):
            try:
                return await client.chat.completions.create(**payload)
            except TypeError as exc:
                unexpected_kwarg = self._extract_unexpected_kwarg(exc)
                if not unexpected_kwarg or unexpected_kwarg not in payload:
                    raise

                logger.warning(
                    "Dropping unsupported completion argument '{}' for model '{}'",
                    unexpected_kwarg,
                    payload.get("model"),
                )
                payload.pop(unexpected_kwarg, None)
            except Exception as exc:
                unsupported_param = self._extract_unsupported_optional_param(exc, payload)
                if not unsupported_param:
                    raise
                logger.warning(
                    "Dropping unsupported completion argument '{}' for model '{}'",
                    unsupported_param,
                    payload.get("model"),
                )
                payload.pop(unsupported_param, None)

        # Should be unreachable because loop returns or raises.
        return await client.chat.completions.create(**payload)

    @staticmethod
    def _decode_cached_completion(cached: Any) -> Tuple[Optional[str], Dict[str, Any]]:
        if isinstance(cached, str):
            return cached, {}
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
            "model": model or self.model,
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
        **kwargs
    ) -> str:
        """
        Generate a completion from the configured model/provider.

        Args:
            prompt: User prompt
            system: System prompt
            use_cache: Whether to use cache
            **kwargs: Additional provider-specific args

        Returns:
            Generated text
        """
        # Internal retry guard for a one-shot quota recovery pass.
        quota_recovery_attempted = bool(kwargs.pop("_quota_recovery_attempted", False))
        quota_recovery_round = int(kwargs.pop("_quota_recovery_round", 0) or 0)
        recovery_round_override = kwargs.pop("max_quota_recovery_rounds", None)
        wait_for_model_recovery = bool(kwargs.pop("wait_for_model_recovery", True))
        model_request_timeout_seconds = kwargs.pop("model_request_timeout_seconds", None)
        max_attempts_per_model = kwargs.pop("max_attempts_per_model", None)
        timeout_cooldown_seconds = max(
            0,
            int(kwargs.pop("timeout_cooldown_seconds", 0) or 0),
        )
        attempt_limit = (
            self.max_retries
            if max_attempts_per_model is None
            else max(1, min(self.max_retries, int(max_attempts_per_model)))
        )
        preferred_models_raw = kwargs.pop("preferred_models", None)
        preferred_models = (
            [item for item in preferred_models_raw if isinstance(item, str)]
            if isinstance(preferred_models_raw, list)
            else None
        )

        # 检查缓存
        if use_cache and self.config.ai.enable_cache:
            cache_key = self._make_cache_key(prompt, system)
            cached = await self.cache.get(cache_key)
            if cached:
                cached_response, cached_meta = self._decode_cached_completion(cached)
                if cached_response is not None:
                    cached_model = cached_meta.get("model")
                    model_name = cached_model.strip() if isinstance(cached_model, str) and cached_model.strip() else self.model

                    cached_provider = cached_meta.get("provider")
                    provider = cached_provider if isinstance(cached_provider, str) and cached_provider.strip() else self.provider
                    cached_tokens = cached_meta.get("token_usage")

                    self._append_conversation_entry(
                        prompt=prompt,
                        system=system,
                        response_text=cached_response,
                        provider=provider,
                        model=model_name,
                        token_usage=cached_tokens if isinstance(cached_tokens, dict) else {},
                        duration=0.0,
                        metadata={"cache_hit": True},
                    )
                logger.debug(f"Cache hit for agent '{self.name}'")
                return cached_response if cached_response is not None else cached
        
        # Rate limiting
        if self.config.ai.enable_rate_limiting:
            await self.rate_limiter.wait_if_needed()
            self.rate_limiter.record_request()
        
        # 调用 LLM：运行时路由由模型中心统一管理；env 链路只用于初始化模型中心。
        route_cache_ttl = max(1, int(getattr(self.config.ai, "route_cache_ttl_seconds", 15)))
        runtime_routes = BaseAgent.AVAILABLE_MODEL_ROUTES
        route_cache_expired = (
            BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT is None
            or (time.time() - BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT) >= route_cache_ttl
        )
        if runtime_routes is None or route_cache_expired:
            try:
                runtime_routes = await get_active_model_routes()
                if not runtime_routes:
                    # Keep model-center source of truth even when all routes are cooling/rate-limited.
                    all_runtime_routes = await get_runtime_routes()
                    if all_runtime_routes:
                        runtime_routes = all_runtime_routes
                BaseAgent.AVAILABLE_MODEL_ROUTES = runtime_routes
                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = time.time()
            except Exception as e:
                logger.warning(f"Failed to load model-center routes; no direct env fallback will be used: {e}")
                runtime_routes = []
                BaseAgent.AVAILABLE_MODEL_ROUTES = None
                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None

        candidates, chain_used = self._build_candidates(runtime_routes, ignore_local_cooldowns=False)
        candidates = self._prioritize_candidates(candidates, preferred_models)

        if not candidates and wait_for_model_recovery:
            wait_cap_seconds = max(
                3,
                int(
                    getattr(
                        self.config.ai,
                        "rate_limit_wait_cap_seconds",
                        getattr(self.config.ai, "rate_limit_cooldown_seconds", 300),
                    )
                ),
            )
            wait_seconds = BaseAgent._estimate_wait_seconds_for_empty_candidates(
                runtime_routes=runtime_routes,
                chain=chain_used,
                wait_cap_seconds=wait_cap_seconds,
            )
            logger.warning(
                f"No candidate model available for agent '{self.name}'. "
                f"Will wait {wait_seconds}s, refresh routes, and retry candidate selection once "
                f"(quota recovery round {quota_recovery_round})."
            )
            await asyncio.sleep(wait_seconds)

            BaseAgent.AVAILABLE_MODEL_ROUTES = None
            BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
            try:
                runtime_routes = await get_active_model_routes()
                if not runtime_routes:
                    all_runtime_routes = await get_runtime_routes()
                    if all_runtime_routes:
                        runtime_routes = all_runtime_routes
                BaseAgent.AVAILABLE_MODEL_ROUTES = runtime_routes
                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = time.time()
            except Exception as refresh_exc:
                logger.warning(f"Failed to refresh model-center routes after cooldown wait: {refresh_exc}")
                runtime_routes = []

            candidates, chain_used = self._build_candidates(runtime_routes, ignore_local_cooldowns=False)
            candidates = self._prioritize_candidates(candidates, preferred_models)

            # 最后做一次安全探测：忽略本地进程冷却，尝试首个候选，避免直接抛出空链路错误。
            if not candidates:
                probe_candidates, _ = self._build_candidates(runtime_routes, ignore_local_cooldowns=True)
                probe_candidates = self._prioritize_candidates(probe_candidates, preferred_models)
                if probe_candidates:
                    candidates = probe_candidates
                    logger.warning(
                        f"Candidate list still empty after wait/refresh. "
                        f"Will probe {len(candidates)} model(s) by ignoring local cooldown markers."
                    )
        elif not candidates:
            logger.warning(
                f"No candidate model available for agent '{self.name}'; "
                "model recovery waiting is disabled for this request."
            )

        last_error = None
        saw_quota_failure = False
        quota_wait_candidates: List[int] = []
        original_model = self.model

        for candidate in candidates:
            model_name = candidate["model_name"]
            route = candidate["route"]
            route_key = candidate["route_key"]
            self.model = model_name
            retry_count = 0
            start_time = time.time()

            while retry_count < attempt_limit:
                attempt_started_at = time.perf_counter()
                try:
                    if route:
                        provider = str(route.get("provider_key") or route.get("provider_name") or "runtime")
                        request = self._complete_with_runtime_route(
                            route,
                            prompt,
                            system,
                            request_timeout_seconds=model_request_timeout_seconds,
                            **kwargs,
                        )
                    else:
                        # Determine which provider to use for this model
                        provider = self.get_provider_for_model(self.model)
                        request = self._complete_with_provider(
                            provider, prompt, system, **kwargs
                        )
                    # Runtime-route admission is intentional local
                    # backpressure.  Its wait is outside the provider request
                    # deadline, which is enforced inside the admitted call.
                    if model_request_timeout_seconds is not None and not route:
                        response_text, token_usage = await asyncio.wait_for(
                            request,
                            timeout=max(0.01, float(model_request_timeout_seconds)),
                        )
                    else:
                        response_text, token_usage = await request
                    if not isinstance(response_text, str) or not response_text.strip():
                        raise RuntimeError("Model returned an empty completion response")
                    attempt_duration_seconds = time.perf_counter() - attempt_started_at
                    if route:
                        try:
                            await record_route_runtime_success(
                                route,
                                duration_seconds=getattr(
                                    self,
                                    "_runtime_route_request_duration_seconds",
                                    None,
                                )
                                or attempt_duration_seconds,
                            )
                        except Exception as health_exc:
                            logger.warning(
                                "Failed to persist runtime success for route '{}': {}",
                                route_key,
                                health_exc,
                            )
                    if route and (
                        route.get("rate_limit_count")
                        or route.get("last_check_status") == "rate_limited"
                    ):
                        try:
                            await clear_route_rate_limit(route, "Connection recovered after cooldown")
                            BaseAgent.AVAILABLE_MODEL_ROUTES = None
                            BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
                        except Exception as clear_exc:
                            logger.warning(f"Failed to clear route cooldown for '{route_key}': {clear_exc}")

                    # Success means local cooldown markers should no longer block this model/route.
                    BaseAgent.MODEL_COOLDOWNS.pop(model_name, None)
                    BaseAgent.ROUTE_COOLDOWNS.pop(route_key, None)

                    # 记录对话历史
                    duration = time.time() - start_time
                    interaction_metadata = {"cache_hit": False}
                    if route:
                        interaction_metadata["runtime_route"] = {
                            key: route.get(key)
                            for key in (
                                "model_id",
                                "provider_id",
                                "model_key",
                                "model_name",
                                "provider_key",
                            )
                        }
                    self._append_conversation_entry(
                        prompt=prompt,
                        system=system,
                        response_text=response_text,
                        provider=provider,
                        token_usage=token_usage,
                        duration=duration,
                        metadata=interaction_metadata,
                    )

                    # 缓存结果
                    if use_cache and self.config.ai.enable_cache:
                        await self.cache.set(
                            cache_key,
                            {
                                "response": response_text,
                                "token_usage": token_usage,
                                "model": self.model,
                                "provider": provider,
                                "temperature": self.temperature,
                                "max_tokens": self.max_tokens,
                            },
                            ttl=self.config.ai.cache_ttl * 3600,  # 转换为秒
                        )

                    # 成功立即返回
                    self.model = original_model
                    return response_text

                except Exception as e:
                    last_error = e
                    retry_count += 1

                    if route:
                        try:
                            runtime_failure = await record_route_runtime_failure(
                                route,
                                e,
                                duration_seconds=getattr(
                                    self,
                                    "_runtime_route_request_duration_seconds",
                                    None,
                                )
                                or (time.perf_counter() - attempt_started_at),
                                cooldown_seconds=max(
                                    5,
                                    int(
                                        timeout_cooldown_seconds
                                        or getattr(
                                            self.config.ai,
                                            "rate_limit_cooldown_seconds",
                                            60,
                                        )
                                    ),
                                ),
                            )
                            if runtime_failure.get("recorded"):
                                # A following task must read the durable model-center
                                # circuit, not reuse this process's stale route cache.
                                BaseAgent.AVAILABLE_MODEL_ROUTES = None
                                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
                        except Exception as health_exc:
                            logger.warning(
                                "Failed to persist runtime failure for route '{}': {}",
                                route_key,
                                health_exc,
                            )

                    quota_related = is_rate_limit_error(e)
                    unavailable_related = is_model_unavailable_error(e)
                    retry_after_seconds = extract_retry_after_seconds(e)
                    cooldown_seconds = retry_after_seconds or int(
                        getattr(self.config.ai, "rate_limit_cooldown_seconds", 300)
                    )

                    logger.warning(
                        f"Agent '{self.name}' error with model '{self.model}' "
                        f"(attempt {retry_count}/{attempt_limit}): {e}"
                    )

                    if isinstance(e, TimeoutError):
                        if timeout_cooldown_seconds:
                            BaseAgent._mark_model_cooling_down(
                                self.model,
                                timeout_cooldown_seconds,
                            )
                            BaseAgent._mark_route_cooling_down(
                                route_key,
                                timeout_cooldown_seconds,
                            )
                        logger.warning(
                            f"Model '{self.model}' exceeded its per-route request timeout; "
                            "switching to the next candidate."
                        )
                        break

                    if quota_related:
                        saw_quota_failure = True
                        quota_wait_candidates.append(max(1, int(cooldown_seconds)))
                        logger.warning(
                            f"Detected quota/rate-limit issue for model '{self.model}', "
                            f"will cool down this route for {cooldown_seconds}s and switch to the next model if available."
                        )
                        BaseAgent._mark_model_cooling_down(self.model, cooldown_seconds)
                        BaseAgent._mark_route_cooling_down(route_key, cooldown_seconds)
                        if route:
                            try:
                                persisted = await mark_route_rate_limited(
                                    route,
                                    str(e),
                                    retry_after_seconds=retry_after_seconds,
                                )
                                cooldown_seconds = int(persisted.get("cooldown_seconds") or cooldown_seconds)
                            except Exception as persist_exc:
                                logger.warning(
                                    f"Failed to persist route cooldown for '{route_key}': {persist_exc}"
                                )
                        BaseAgent.AVAILABLE_MODEL_ROUTES = None
                        BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
                        break

                    if unavailable_related:
                        unavailable_cooldown = max(300, int(getattr(self.config.ai, "rate_limit_cooldown_seconds", 300)))
                        logger.warning(
                            f"Detected unavailable model/permission issue for '{self.model}'. "
                            f"Will skip this model for now and continue with the next candidate."
                        )
                        BaseAgent._mark_model_cooling_down(self.model, unavailable_cooldown)
                        BaseAgent._mark_route_cooling_down(route_key, unavailable_cooldown)
                        if route:
                            try:
                                await mark_route_unavailable(route, str(e))
                            except Exception as unavailable_exc:
                                logger.warning(
                                    f"Failed to persist unavailable status for route '{route_key}': {unavailable_exc}"
                                )
                        else:
                            try:
                                updated = await mark_model_unavailable_by_name(self.model, str(e))
                                if updated <= 0:
                                    logger.warning(
                                        f"No model-center records matched unavailable model '{self.model}'."
                                    )
                            except Exception as unavailable_exc:
                                logger.warning(
                                    f"Failed to persist unavailable status by model name '{self.model}': {unavailable_exc}"
                                )
                        BaseAgent.AVAILABLE_MODEL_ROUTES = None
                        BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
                        break

                    if retry_count < attempt_limit:
                        await asyncio.sleep(2 ** retry_count)  # exponential backoff

            # 当前模型用尽重试仍失败，尝试下一个模型
            logger.error(
                f"Model '{self.model}' failed after {retry_count} attempt(s) for agent '{self.name}'."
            )

        # 所有模型都失败
        self.model = original_model

        max_recovery_rounds = (
            max(0, int(recovery_round_override))
            if recovery_round_override is not None
            else int(getattr(self.config.ai, "rate_limit_recovery_max_rounds", 1) or 1)
        )
        if saw_quota_failure and quota_recovery_round < max_recovery_rounds:
            wait_cap_seconds = max(
                3,
                int(
                    getattr(
                        self.config.ai,
                        "rate_limit_wait_cap_seconds",
                        getattr(self.config.ai, "rate_limit_cooldown_seconds", 300),
                    )
                ),
            )
            raw_wait = min(quota_wait_candidates) if quota_wait_candidates else 1
            wait_seconds = max(1, min(int(raw_wait), wait_cap_seconds))
            logger.warning(
                f"All candidate models failed and at least one error was quota-related. "
                f"Will wait {wait_seconds}s and run recovery retry pass "
                f"{quota_recovery_round + 1}/{max_recovery_rounds}."
            )
            await asyncio.sleep(wait_seconds)
            BaseAgent.AVAILABLE_MODEL_ROUTES = None
            BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
            return await self.complete(
                prompt=prompt,
                system=system,
                use_cache=use_cache,
                preferred_models=preferred_models,
                max_quota_recovery_rounds=max_recovery_rounds,
                wait_for_model_recovery=wait_for_model_recovery,
                model_request_timeout_seconds=model_request_timeout_seconds,
                max_attempts_per_model=attempt_limit,
                timeout_cooldown_seconds=timeout_cooldown_seconds,
                _quota_recovery_attempted=True,
                _quota_recovery_round=quota_recovery_round + 1,
                **kwargs,
            )

        logger.error(f"All models failed for agent '{self.name}': {last_error}")
        attempted_models = [c.get("model_name") for c in candidates if c.get("model_name")]
        if not attempted_models and self.model:
            attempted_models = [self.model]
        raise Exception(
            f"Agent completion failed after trying models {attempted_models}: {last_error}"
        )

    async def _complete_with_runtime_route(
        self,
        route: Dict[str, Any],
        prompt: str,
        system: Optional[str] = None,
        request_timeout_seconds: Optional[float] = None,
        **kwargs,
    ) -> Tuple[str, Dict[str, int]]:
        """Generate through a permit; timeout and telemetry cover only the API call."""
        self._runtime_route_request_duration_seconds = None
        admission = await acquire_runtime_route_admission(route)
        request_started_at = time.perf_counter()
        try:
            request = self._complete_with_admitted_runtime_route(
                route,
                prompt,
                system,
                **kwargs,
            )
            if request_timeout_seconds is not None:
                response = await asyncio.wait_for(
                    request,
                    timeout=max(0.01, float(request_timeout_seconds)),
                )
            else:
                response = await request
        except BaseException:
            self._runtime_route_request_duration_seconds = time.perf_counter() - request_started_at
            await admission.release(success=False)
            raise
        self._runtime_route_request_duration_seconds = time.perf_counter() - request_started_at
        await admission.release(success=True)
        return response

    async def _complete_with_admitted_runtime_route(
        self,
        route: Dict[str, Any],
        prompt: str,
        system: Optional[str] = None,
        **kwargs,
    ) -> Tuple[str, Dict[str, int]]:
        """Issue the provider request after Model Center admission succeeds."""
        style = str(route.get("api_style") or "openai_compatible").lower()
        model_name = str(route.get("model_name") or self.model)
        route_temperature = route.get("temperature")
        route_max_tokens = route.get("max_tokens")
        temperature = self.temperature if route_temperature is None else float(route_temperature)
        max_tokens = (
            self.max_tokens
            if route_max_tokens is None
            else min(self.max_tokens, int(route_max_tokens))
        )
        sanitized_kwargs = self._sanitize_completion_params(dict(kwargs))

        if style == "anthropic":
            client = AsyncAnthropic(api_key=route.get("api_key"))
            response = await client.messages.create(
                model=model_name,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                **sanitized_kwargs,
            )

            token_usage = {}
            if hasattr(response, "usage") and response.usage:
                token_usage = {
                    "prompt": response.usage.input_tokens,
                    "completion": response.usage.output_tokens,
                    "total": response.usage.input_tokens + response.usage.output_tokens,
                }
            return response.content[0].text, token_usage

        client = AsyncOpenAI(
            api_key=route.get("api_key"),
            base_url=route.get("base_url") or None,
            default_headers=(route.get("extra_headers") or None),
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        extra_params = dict(route.get("extra_params") or {})
        for reserved in ("model", "messages", "temperature", "max_tokens"):
            extra_params.pop(reserved, None)
        extra_params.update(sanitized_kwargs)
        extra_params = self._sanitize_completion_params(extra_params)

        request_payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **extra_params,
        }
        response = await self._safe_openai_completion_create(client, request_payload)

        return self._extract_openai_compatible_response(response)
    
    async def _complete_with_provider(
        self, 
        provider: str,
        prompt: str, 
        system: Optional[str] = None, 
        **kwargs
    ) -> Tuple[str, Dict[str, int]]:
        """根据提供商调用相应的完成方法，返回(响应文本, token使用量)"""
        if provider == 'anthropic':
            return await self._complete_anthropic(prompt, system, **kwargs)
        elif provider in ['openai', 'glm', 'qianwen', 'azure', 'custom']:
            return await self._complete_openai_compatible(provider, prompt, system, **kwargs)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    async def _complete_openai_compatible(
        self,
        provider: str,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> Tuple[str, Dict[str, int]]:
        """Generate using an OpenAI-compatible API (supports QianWen, GLM, Azure, etc.)."""
        client = self.clients.get(provider)
        if not client:
            raise ValueError(f"Client for provider '{provider}' not initialized")
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        # Handle provider-specific model name mapping
        model = self._map_model_name(provider, self.model)

        request_payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self._sanitize_completion_params(dict(kwargs)),
        }
        response = await self._safe_openai_completion_create(client, request_payload)
        
        return self._extract_openai_compatible_response(response)
    
    async def _complete_anthropic(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> Tuple[str, Dict[str, int]]:
        """Generate using the Anthropic API."""
        client = self.clients.get('anthropic')
        if not client:
            raise ValueError("Anthropic client not initialized")
        
        response = await client.messages.create(
            model=self.model,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs
        )
        
        # Extract token usage
        token_usage = {}
        if hasattr(response, 'usage') and response.usage:
            token_usage = {
                "prompt": response.usage.input_tokens,
                "completion": response.usage.output_tokens,
                "total": response.usage.input_tokens + response.usage.output_tokens,
            }
        
        return response.content[0].text, token_usage
    
    def _map_model_name(self, provider: str, model: str) -> str:
        """Map generic model names to provider-specific names."""
        if provider == 'glm':
            # GLM智谱AI模型名称映射
            mapping = {
                'glm-4': 'glm-4',
                'glm-4-7': 'glm-4',  # 将glm-4-7映射到glm-4
                'glm-4-plus': 'glm-4-plus',
                'glm-4-air': 'glm-4-air',
                'glm-4-airx': 'glm-4-airx',
                'glm-3-turbo': 'glm-3-turbo'
            }
            return mapping.get(model, model)
        elif provider == 'qianwen':
            # QianWen model name mapping
            mapping = {
                'qwen-turbo': 'qwen-turbo',
                'qwen-plus': 'qwen-plus',
                'qwen-max': 'qwen-max',
                'qwen-max-longcontext': 'qwen-max-longcontext'
            }
            return mapping.get(model, model)
        
        return model
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test connectivity with the configured AI provider."""
        provider = self.get_provider_for_model(self.model)
        
        try:
            test_prompt = self.config.ai.test_prompt
            # _complete_with_provider returns (response_text, token_usage) tuple
            response_text, _ = await self._complete_with_provider(provider, test_prompt)
            
            return {
                'success': True,
                'provider': provider,
                'model': self.model,
                'response': response_text[:100] + '...' if len(response_text) > 100 else response_text,
                'message': 'Connection test successful'
            }
        except Exception as e:
            return {
                'success': False,
                'provider': provider,
                'model': self.model,
                'error': str(e),
                'message': 'Connection test failed'
            }
    
    def _make_cache_key(self, prompt: str, system: Optional[str] = None) -> str:
        """Generate a cache key for a prompt/system pair."""
        import hashlib
        content = f"{self.name}:{self.model}:{system or ''}:{prompt}"
        return f"agent:{hashlib.md5(content.encode()).hexdigest()}"

    async def invalidate_completion_cache(
        self,
        *,
        prompt: str,
        system: Optional[str] = None,
    ) -> bool:
        """Discard a completion that failed a caller's post-generation checks."""
        if not self.config.ai.enable_cache:
            return False
        try:
            return bool(await self.cache.delete(self._make_cache_key(prompt, system)))
        except Exception as exc:
            logger.warning("Failed to invalidate AI completion cache: {}", exc)
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
