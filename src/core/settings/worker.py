"""Background task worker runtime configuration."""

from typing import Literal

from pydantic import Field

from .base import _BaseEnvSettings


class TaskWorkerSettings(_BaseEnvSettings):
    """后台任务 worker 配置"""

    role: Literal["general", "ai"] = Field(
        default="general",
        validation_alias="TASK_WORKER_ROLE",
        description="general 执行采集和发布；ai 独立执行 AI 业务组织与模型调度",
    )
    concurrency: int = Field(
        default=2,
        validation_alias="TASK_WORKER_CONCURRENCY",
        ge=1,
        le=64,
        description="任务 worker 最大并发数",
    )
    general_concurrency: int = Field(
        default=12,
        validation_alias="TASK_WORKER_GENERAL_CONCURRENCY",
        ge=1,
        le=64,
        description="采集、整理、邮件等普通任务的并行槽位，不与发布或 AI 共用",
    )
    release_concurrency: int = Field(
        default=1,
        validation_alias="TASK_WORKER_RELEASE_CONCURRENCY",
        ge=1,
        le=8,
        description="数据发布专用槽位",
    )
    ai_concurrency: int = Field(
        default=1,
        validation_alias="TASK_WORKER_AI_CONCURRENCY",
        ge=1,
        le=64,
        description="AI 业务组织者的并发上限，包含等待模型的父任务；模型调用容量独立由模型中心控制",
    )
    ai_dynamic_concurrency_enabled: bool = Field(
        default=True,
        validation_alias="TASK_WORKER_AI_DYNAMIC_CONCURRENCY_ENABLED",
        description="模型中心无可用路线时暂停新的 AI 业务任务；关闭后始终使用配置的组织者上限",
    )
    ai_concurrency_min: int = Field(
        default=1,
        validation_alias="TASK_WORKER_AI_CONCURRENCY_MIN",
        ge=1,
        le=64,
        description="AI 组织者配置值的下界；动态模式在无可用路线时可暂停至 0",
    )
    ai_concurrency_adjust_seconds: int = Field(
        default=20,
        validation_alias="TASK_WORKER_AI_CONCURRENCY_ADJUST_SECONDS",
        ge=1,
        le=3600,
        description="重新读取模型路线可用性的最小间隔（秒）",
    )
    knowledge_source_concurrency: int = Field(
        default=3,
        validation_alias="TASK_WORKER_KNOWLEDGE_SOURCE_CONCURRENCY",
        ge=1,
        le=64,
        description="知识库外部来源发现任务的最大并发，避免上游站点超时放大",
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        validation_alias="TASK_WORKER_POLL_INTERVAL",
        gt=0,
        description="任务 worker 拉取队列轮询间隔（秒）",
    )
    idle_log_every: int = Field(
        default=300,
        validation_alias="TASK_WORKER_IDLE_LOG_EVERY",
        ge=1,
        description="worker 空闲日志输出周期（轮）",
    )
    task_heartbeat_seconds: int = Field(
        default=15,
        validation_alias="TASK_WORKER_TASK_HEARTBEAT_SECONDS",
        ge=5,
        le=300,
        description="正在执行的任务租约心跳间隔（秒）",
    )
    shutdown_grace_seconds: int = Field(
        default=30,
        validation_alias="TASK_WORKER_SHUTDOWN_GRACE_SECONDS",
        ge=5,
        le=600,
        description="收到停机信号后等待可续跑任务自然收束的最长时间（秒）",
    )
    stale_task_seconds: int = Field(
        default=180,
        validation_alias="TASK_WORKER_STALE_TASK_SECONDS",
        ge=30,
        le=86400,
        description="RUNNING 任务失去心跳后可恢复的阈值（秒）",
    )
    recovery_scan_seconds: int = Field(
        default=60,
        validation_alias="TASK_WORKER_RECOVERY_SCAN_SECONDS",
        ge=10,
        le=3600,
        description="扫描过期 RUNNING 任务的间隔（秒）",
    )
    runtime_lease_ttl_seconds: int = Field(
        default=180,
        validation_alias="TASK_WORKER_RUNTIME_LEASE_TTL_SECONDS",
        ge=60,
        le=900,
        description="worker 单例租约 TTL；须容忍既有同步 crawler 的事件循环阻塞",
    )
    runtime_heartbeat_ttl_seconds: int = Field(
        default=120,
        validation_alias="TASK_WORKER_RUNTIME_HEARTBEAT_TTL_SECONDS",
        ge=45,
        le=900,
        description="worker readiness 心跳 TTL；systemd 仍负责快速进程重启",
    )
    scheduler_worker_grace_seconds: int = Field(
        default=90,
        validation_alias="SCHEDULER_WORKER_GRACE_SECONDS",
        ge=30,
        le=3600,
        description="scheduler 等待 worker 心跳的容忍时间（秒）",
    )


__all__ = [
    "TaskWorkerSettings",
]
