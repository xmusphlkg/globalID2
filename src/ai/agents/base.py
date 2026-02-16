"""
GlobalID V2 AI Base Agent

AI Agent Base Class - Provides unified LLM interaction functionality with multi-platform AI provider support
"""
import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from src.core import get_cache, get_config, get_logger, RateLimiter

logger = get_logger(__name__)


class BaseAgent(ABC):
    """
    AI Agent Base Class
    
    Provides common LLM invocation, caching, retry, and rate-limiting functionality.
    Supports multiple AI providers: OpenAI, Anthropic, QianWen, Azure, and more.
    """
    
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
        
        # 提供商和模型配置
        self.provider = provider or self.config.ai.default_provider
        self.model = model or self.config.ai.default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = self.config.ai.max_retries
        
        # 初始化客户端
        self.clients = {}
        self._init_clients()
        
        logger.info(f"Agent '{name}' initialized with provider '{self.provider}' and model '{self.model}'")
    
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
    
    def get_provider_for_model(self, model: str) -> str:
        """Infer provider from model name."""
        model_lower = model.lower()
        
        if any(keyword in model_lower for keyword in ['glm', 'chatglm', 'zhipu']):
            return 'glm'
        elif any(keyword in model_lower for keyword in ['qwen', 'qianwen', 'qianwen']):
            return 'qianwen'
        elif any(keyword in model_lower for keyword in ['claude', 'anthropic']):
            return 'anthropic'
        elif any(keyword in model_lower for keyword in ['gpt-4', 'gpt-3.5', 'text-davinci']):
            return 'openai'
        elif 'azure' in model_lower:
            return 'azure'
        else:
            return self.provider
    
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
                logger.debug(f"Cache hit for agent '{self.name}'")
                return cached
        
        # Rate limiting
        if self.config.ai.enable_rate_limiting:
            await self.rate_limiter.wait_if_needed()
            self.rate_limiter.record_request()
        
        # 调用LLM
        retry_count = 0
        last_error = None
        
        while retry_count < self.max_retries:
            try:
                # Determine which provider to use
                provider = self.get_provider_for_model(self.model)
                response_text = await self._complete_with_provider(provider, prompt, system, **kwargs)
                
                # 缓存结果
                if use_cache and self.config.ai.enable_cache:
                    await self.cache.set(
                        cache_key,
                        response_text,
                        ttl=self.config.ai.cache_ttl * 3600,  # 转换为秒
                    )
                
                return response_text
                
            except Exception as e:
                last_error = e
                retry_count += 1
                logger.warning(f"Retry {retry_count}/{self.max_retries} after error: {e}")
                
                if retry_count < self.max_retries:
                    await asyncio.sleep(2 ** retry_count)  # exponential backoff
        
        # All retries failed
        logger.error(f"All retries failed for agent '{self.name}': {last_error}")
        raise Exception(f"Agent completion failed after {self.max_retries} retries: {last_error}")
    
    async def _complete_with_provider(
        self, 
        provider: str,
        prompt: str, 
        system: Optional[str] = None, 
        **kwargs
    ) -> str:
        """根据提供商调用相应的完成方法"""
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
    ) -> str:
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
        return response.choices[0].message.content
    
    async def _complete_anthropic(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> str:
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
        
        return response.content[0].text
    
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
            response = await self._complete_with_provider(provider, test_prompt)
            
            return {
                'success': True,
                'provider': provider,
                'model': self.model,
                'response': response[:100] + '...' if len(response) > 100 else response,
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
