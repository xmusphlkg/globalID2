"""Request and response contracts for model center."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

ThinkingMode = Literal["default", "enabled", "disabled"]


class ProviderCreateRequest(BaseModel):
    provider_key: str = Field(..., min_length=2, max_length=120)
    provider_name: str = Field(..., min_length=2, max_length=80)
    display_name: str = Field(..., min_length=2, max_length=200)
    api_style: str = Field("openai_compatible")
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    organization: Optional[str] = None
    extra_headers: Dict[str, Any] = Field(default_factory=dict)
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    priority: int = 100

class ProviderBootstrapRequest(BaseModel):
    force: bool = False

class ProviderUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    api_style: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    organization: Optional[str] = None
    extra_headers: Optional[Dict[str, Any]] = None
    extra_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None

class ProviderOut(BaseModel):
    id: int
    provider_key: str
    provider_name: str
    display_name: str
    api_style: str
    base_url: Optional[str] = None
    organization: Optional[str] = None
    is_active: bool
    priority: int
    has_api_key: bool
    api_key_hint: Optional[str] = None
    extra_headers: Dict[str, Any] = {}
    extra_config: Dict[str, Any] = {}
    last_check_status: str
    last_check_message: Optional[str] = None
    last_checked_at: Optional[str] = None
    rate_limit_active: bool = False
    rate_limit_cooldown_until: Optional[str] = None
    rate_limit_remaining_seconds: int = 0
    rate_limit_count: int = 0
    last_rate_limit_at: Optional[str] = None
    runtime_failure_active: bool = False
    runtime_failure_cooldown_until: Optional[str] = None
    runtime_failure_remaining_seconds: int = 0
    runtime_failure_streak: int = 0
    runtime_timeout_count: int = 0
    runtime_latency_ewma_ms: Optional[float] = None
    runtime_last_error: Optional[str] = None

class AvailabilityWindowIn(BaseModel):
    start: str
    end: str

class ModelCreateRequest(BaseModel):
    provider_key: str = Field(..., min_length=2, max_length=120)
    model_name: str = Field(..., min_length=1, max_length=120)
    display_name: Optional[str] = None
    model_key: Optional[str] = None
    model_type: str = "chat"
    api_style: Optional[str] = None
    temperature: Optional[float] = None
    runtime_max_concurrency: Optional[int] = Field(default=None, ge=1, le=512)
    thinking_mode: Optional[ThinkingMode] = None
    availability_timezone: Optional[str] = None
    availability_windows: Optional[List[AvailabilityWindowIn]] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    is_default: bool = False
    priority: int = 100

class ModelUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    model_type: Optional[str] = None
    api_style: Optional[str] = None
    temperature: Optional[float] = None
    runtime_max_concurrency: Optional[int] = Field(default=None, ge=1, le=512)
    thinking_mode: Optional[ThinkingMode] = None
    availability_timezone: Optional[str] = None
    availability_windows: Optional[List[AvailabilityWindowIn]] = None
    extra_params: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    priority: Optional[int] = None

class ModelOut(BaseModel):
    id: int
    provider_id: int
    provider_key: str
    provider_name: str
    model_key: str
    model_name: str
    display_name: str
    model_type: str
    api_style: Optional[str] = None
    temperature: Optional[float] = None
    runtime_max_concurrency: Optional[int] = None
    thinking_mode: ThinkingMode = "default"
    availability_timezone: str = "Asia/Shanghai"
    availability_windows: List[AvailabilityWindowIn] = Field(default_factory=list)
    extra_params: Dict[str, Any] = {}
    is_enabled: bool
    is_default: bool
    priority: int
    last_check_status: str
    last_check_message: Optional[str] = None
    last_checked_at: Optional[str] = None
    rate_limit_active: bool = False
    rate_limit_scope: Optional[str] = None
    rate_limit_cooldown_until: Optional[str] = None
    rate_limit_remaining_seconds: int = 0
    rate_limit_count: int = 0
    last_rate_limit_at: Optional[str] = None
    runtime_failure_active: bool = False
    runtime_failure_scope: Optional[str] = None
    runtime_failure_cooldown_until: Optional[str] = None
    runtime_failure_remaining_seconds: int = 0
    runtime_failure_streak: int = 0
    runtime_timeout_count: int = 0
    runtime_latency_ewma_ms: Optional[float] = None
    runtime_last_error: Optional[str] = None

class RuntimeRouteOut(BaseModel):
    model_id: int
    model_key: str
    model_name: str
    provider_id: int
    provider_key: str
    provider_name: str
    api_style: str
    base_url: Optional[str] = None
    has_api_key: bool
    api_key_hint: Optional[str] = None
    priority: Optional[int] = None
    available_for_routing: bool = False
    routing_status: str = "probe_unavailable"
    routing_status_reason: Optional[str] = None
    last_check_status: Optional[str] = None
    last_checked_at: Optional[str] = None
    rate_limit_active: bool = False
    rate_limit_scope: Optional[str] = None
    rate_limit_cooldown_until: Optional[str] = None
    rate_limit_remaining_seconds: int = 0
    rate_limit_count: int = 0
    last_rate_limit_at: Optional[str] = None
    runtime_failure_active: bool = False
    runtime_failure_scope: Optional[str] = None
    runtime_failure_cooldown_until: Optional[str] = None
    runtime_failure_remaining_seconds: int = 0
    runtime_failure_kind: Optional[str] = None
    runtime_failure_streak: int = 0
    runtime_failure_streak_raw: int = 0
    runtime_failure_count: int = 0
    runtime_timeout_count: int = 0
    runtime_success_count: int = 0
    runtime_degraded: bool = False
    runtime_degraded_scope: Optional[str] = None
    runtime_degraded_reason: Optional[str] = None
    runtime_latency_ewma_ms: Optional[float] = None
    runtime_last_failure_at: Optional[str] = None
    runtime_last_success_at: Optional[str] = None
    runtime_last_error: Optional[str] = None
    runtime_provider_capacity: int = 1
    runtime_provider_inflight: int = 0
    runtime_provider_max_capacity: int = 1
    runtime_model_capacity: int = 1
    runtime_model_inflight: int = 0

class AIModelCallLogOut(BaseModel):
    id: int
    request_id: str
    model_key: str
    model_name: str
    provider_key: str
    agent_name: str
    status: str
    latency_ms: float
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
