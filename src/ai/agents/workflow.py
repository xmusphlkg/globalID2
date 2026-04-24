"""Structured workflow agents for the generic multi-expert system."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from src.core import get_logger

from .base import BaseAgent

logger = get_logger(__name__)


class WorkflowAgent(BaseAgent):
    """Small helper agent that prefers compact, structured JSON output."""

    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2500,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(
            name=name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.system_prompt = system_prompt.strip()

    async def process(
        self,
        *,
        prompt: str,
        system: Optional[str] = None,
        use_cache: bool = True,
        preferred_models: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        response = await self.complete(
            prompt=prompt,
            system=system or self.system_prompt,
            use_cache=use_cache,
            preferred_models=preferred_models,
        )
        payload: dict[str, Any] = self._parse_json(response)
        payload["raw_response"] = response
        return payload

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                logger.debug("WorkflowAgent returned non-JSON output")
                return {"parse_error": "non_json_output", "raw_response": raw}
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                return {"parse_error": "json_decode_failed", "raw_response": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"parse_error": "not_an_object", "raw_response": raw}
