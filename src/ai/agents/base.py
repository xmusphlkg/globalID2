"""
GlobalID V2 AI Base Agent

AI Agent 基类，提供通用的LLM交互功能
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
    AI Agent 基类
    
    提供通用的LLM调用、缓存、重试、速率限制等功能
    """
    
    def __init__(
        self,
        name: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        """
        初始化 Agent
        
        Args:
            name: Agent名称
            model: 使用的模型（默认使用配置中的模型）
            temperature: 生成温度
            max_tokens: 最大token数
        """
        self.name = name
        self.config = get_config()
        self.cache = get_cache()
        self.rate_limiter = RateLimiter(
            max_requests=self.config.ai.rate_limit,
            window_seconds=60,
        )
        
        # 模型配置
        self.model = model or self.config.ai.default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = self.config.ai.max_retries
        
        # 初始化客户端
        self.openai_client = AsyncOpenAI(api_key=self.config.ai.openai_api_key)
        self.anthropic_client = None
        if self.config.ai.anthropic_api_key:
            self.anthropic_client = AsyncAnthropic(api_key=self.config.ai.anthropic_api_key)
        
        logger.info(f"Agent '{name}' initialized with model '{self.model}'")
    
    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        use_cache: bool = True,
        **kwargs
    ) -> str:
        """
        生成完成
        
        Args:
            prompt: 用户提示
            system: 系统提示
            use_cache: 是否使用缓存
            **kwargs: 额外参数
            
        Returns:
            生成的文本
        """
        # 检查缓存
        if use_cache and self.config.ai.enable_cache:
            cache_key = self._make_cache_key(prompt, system)
            cached = await self.cache.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for agent '{self.name}'")
                return cached
        
        # 速率限制
        if self.config.ai.enable_rate_limiting:
            await self.rate_limiter.wait_if_needed()
            self.rate_limiter.record_request()
        
        # 调用LLM
        retry_count = 0
        last_error = None
        
        while retry_count < self.max_retries:
            try:
                # 判断使用哪个提供商
                if "claude" in self.model.lower():
                    response_text = await self._complete_anthropic(prompt, system, **kwargs)
                else:
                    response_text = await self._complete_openai(prompt, system, **kwargs)
                
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
                    await asyncio.sleep(2 ** retry_count)  # 指数退避
        
        # 所有重试失败
        logger.error(f"All retries failed for agent '{self.name}': {last_error}")
        raise Exception(f"Agent completion failed after {self.max_retries} retries: {last_error}")
    
    async def _complete_openai(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> str:
        """使用 OpenAI API 生成"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        response = await self.openai_client.chat.completions.create(
            model=self.model,
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
        """使用 Anthropic API 生成"""
        if not self.anthropic_client:
            raise ValueError("Anthropic API key not configured")
        
        response = await self.anthropic_client.messages.create(
            model=self.model,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs
        )
        
        return response.content[0].text
    
    def _make_cache_key(self, prompt: str, system: Optional[str] = None) -> str:
        """生成缓存键"""
        import hashlib
        content = f"{self.name}:{self.model}:{system or ''}:{prompt}"
        return f"agent:{hashlib.md5(content.encode()).hexdigest()}"
    
    @abstractmethod
    async def process(self, **kwargs) -> Dict[str, Any]:
        """
        处理任务（需要子类实现）
        
        Args:
            **kwargs: 任务参数
            
        Returns:
            处理结果
        """
        pass
