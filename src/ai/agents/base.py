"""
GlobalID V2 AI Base Agent

AI Agent Base Class - Provides unified LLM interaction functionality with multi-platform AI provider support
"""
import asyncio
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from src.core import get_cache, get_config, get_logger, RateLimiter
from src.ai.model_center import (
    clear_route_rate_limit,
    extract_retry_after_seconds,
    get_active_model_routes,
    is_rate_limit_error,
    mark_route_rate_limited,
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
        
        logger.info(f"Agent '{name}' initialized with provider '{self.provider}' and model '{self.model}'")

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
        
        # 调用LLM（支持模型中心路由 + 配置链路双模式）
        route_cache_ttl = max(1, int(getattr(self.config.ai, "route_cache_ttl_seconds", 15)))
        runtime_routes = BaseAgent.AVAILABLE_MODEL_ROUTES
        route_cache_expired = (
            BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT is None
            or (time.time() - BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT) >= route_cache_ttl
        )
        if runtime_routes is None or route_cache_expired:
            try:
                runtime_routes = await get_active_model_routes()
                BaseAgent.AVAILABLE_MODEL_ROUTES = runtime_routes
                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = time.time()
            except Exception as e:
                logger.warning(f"Failed to load model-center routes, fallback to config chain: {e}")
                runtime_routes = []
                BaseAgent.AVAILABLE_MODEL_ROUTES = None
                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None

        candidates: List[Dict[str, Any]] = []
        if runtime_routes:
            for route in runtime_routes:
                route_key = str(route.get("model_key") or route.get("model_name") or "")
                model_name = str(route.get("model_name") or "")
                if not route_key or not model_name:
                    continue
                if BaseAgent._is_route_cooling_down(route_key):
                    continue
                if BaseAgent._is_model_cooling_down(model_name):
                    continue
                candidates.append(
                    {
                        "route_key": route_key,
                        "model_name": model_name,
                        "route": route,
                    }
                )
        else:
            # 若模型中心无可用路由，则回退到配置链路。
            chain = BaseAgent.AVAILABLE_MODEL_CHAIN
            if chain is None:
                chain = getattr(self.config.ai, "model_chain", None) or []
            if chain:
                for model_name in chain:
                    if BaseAgent._is_model_cooling_down(model_name):
                        continue
                    candidates.append(
                        {
                            "route_key": model_name,
                            "model_name": model_name,
                            "route": None,
                        }
                    )
            else:
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

        last_error = None
        original_model = self.model

        for candidate in candidates:
            model_name = candidate["model_name"]
            route = candidate["route"]
            route_key = candidate["route_key"]
            self.model = model_name
            retry_count = 0
            start_time = time.time()

            while retry_count < self.max_retries:
                try:
                    if route:
                        provider = str(route.get("provider_key") or route.get("provider_name") or "runtime")
                        response_text, token_usage = await self._complete_with_runtime_route(
                            route, prompt, system, **kwargs
                        )
                        if route.get("rate_limit_count") or route.get("last_check_status") == "rate_limited":
                            try:
                                await clear_route_rate_limit(route, "Connection recovered after cooldown")
                                BaseAgent.AVAILABLE_MODEL_ROUTES = None
                                BaseAgent.AVAILABLE_MODEL_ROUTES_LOADED_AT = None
                            except Exception as clear_exc:
                                logger.warning(f"Failed to clear route cooldown for '{route_key}': {clear_exc}")
                    else:
                        # Determine which provider to use for this model
                        provider = self.get_provider_for_model(self.model)
                        response_text, token_usage = await self._complete_with_provider(
                            provider, prompt, system, **kwargs
                        )

                    # 记录对话历史
                    duration = time.time() - start_time
                    self._append_conversation_entry(
                        prompt=prompt,
                        system=system,
                        response_text=response_text,
                        provider=provider,
                        token_usage=token_usage,
                        duration=duration,
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

                    quota_related = is_rate_limit_error(e)
                    retry_after_seconds = extract_retry_after_seconds(e)
                    cooldown_seconds = retry_after_seconds or int(
                        getattr(self.config.ai, "rate_limit_cooldown_seconds", 300)
                    )

                    logger.warning(
                        f"Agent '{self.name}' error with model '{self.model}' "
                        f"(attempt {retry_count}/{self.max_retries}): {e}"
                    )

                    if quota_related:
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

                    if retry_count < self.max_retries:
                        await asyncio.sleep(2 ** retry_count)  # exponential backoff

            # 当前模型用尽重试仍失败，尝试下一个模型
            logger.error(
                f"Model '{self.model}' failed after {self.max_retries} retries for agent '{self.name}'."
            )

        # 所有模型都失败
        self.model = original_model
        logger.error(f"All models failed for agent '{self.name}': {last_error}")
        raise Exception(
            f"Agent completion failed after trying models {[c['model_name'] for c in candidates]}: {last_error}"
        )

    async def _complete_with_runtime_route(
        self,
        route: Dict[str, Any],
        prompt: str,
        system: Optional[str] = None,
        **kwargs,
    ) -> Tuple[str, Dict[str, int]]:
        """Generate using model-center runtime route (provider+credential+style)."""
        style = str(route.get("api_style") or "openai_compatible").lower()
        model_name = str(route.get("model_name") or self.model)
        route_temperature = route.get("temperature")
        route_max_tokens = route.get("max_tokens")
        temperature = self.temperature if route_temperature is None else float(route_temperature)
        max_tokens = self.max_tokens if route_max_tokens is None else int(route_max_tokens)

        if style == "anthropic":
            client = AsyncAnthropic(api_key=route.get("api_key"))
            response = await client.messages.create(
                model=model_name,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
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
        extra_params.update(kwargs)

        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra_params,
        )

        token_usage = {}
        if hasattr(response, "usage") and response.usage:
            token_usage = {
                "prompt": response.usage.prompt_tokens,
                "completion": response.usage.completion_tokens,
                "total": response.usage.total_tokens,
            }
        return response.choices[0].message.content, token_usage
    
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
        
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs
        )
        
        # Extract token usage
        token_usage = {}
        if hasattr(response, 'usage') and response.usage:
            token_usage = {
                "prompt": response.usage.prompt_tokens,
                "completion": response.usage.completion_tokens,
                "total": response.usage.total_tokens,
            }
        
        return response.choices[0].message.content, token_usage
    
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
