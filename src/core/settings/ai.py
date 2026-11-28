"""AI provider, model routing, knowledge automation, and agent workflow settings."""

import json

from pydantic import Field

from .base import _BaseEnvSettings


class AISettings(_BaseEnvSettings):
    """AI模型配置"""

    # 提供商配置
    default_provider: str = Field(
        default="glm",
        description="默认AI提供商(openai/anthropic/glm/qianwen/azure/custom)",
    )

    # OpenAI配置
    openai_api_key: str = Field(default="", description="OpenAI API密钥")
    openai_base_url: str = Field(default="https://api.openai.com/v1", description="OpenAI API基础URL")

    # Anthropic配置
    anthropic_api_key: str = Field(default="", description="Anthropic API密钥")

    # GLM智谱AI配置
    glm_api_key: str = Field(default="", description="GLM API密钥")
    glm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", description="GLM API基础URL")

    # 千问配置
    qianwen_api_key: str = Field(default="", description="千问API密钥")
    qianwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="千问API基础URL",
    )

    # Azure OpenAI配置
    azure_api_key: str = Field(default="", description="Azure OpenAI API密钥")
    azure_endpoint: str = Field(default="", description="Azure OpenAI端点")
    azure_api_version: str = Field(default="2024-02-01", description="Azure API版本")

    # 自定义配置
    custom_api_key: str = Field(default="", description="自定义API密钥")
    custom_base_url: str = Field(default="", description="自定义API基础URL")

    # 默认模型配置
    default_model: str = Field(default="glm-4-7", description="默认使用的模型")
    fallback_model: str = Field(default="glm-4-plus", description="降级模型")
    model_chain_raw: str = Field(
        default="",
        description="环境初始化的显式模型列表，逗号分隔；为空时仅初始化提供商，不创建模型",
    )
    knowledge_generation_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=900,
        description="单个知识库语言版本生成的总超时秒数",
    )
    knowledge_model_request_timeout_seconds: int = Field(
        default=90,
        ge=10,
        le=600,
        description="知识库生成中单条模型路由请求的超时秒数",
    )
    knowledge_model_request_timeout_cap_seconds: int = Field(
        default=120,
        ge=30,
        le=600,
        description="知识库生成单条模型请求的运行时上限，防止旧环境值让个人额度长时间阻塞",
    )
    knowledge_timeout_cooldown_seconds: int = Field(
        default=60,
        ge=0,
        le=600,
        description="知识模型超时后的持久化冷却秒数，避免双语连续撞击同一路由",
    )
    knowledge_output_repair_attempts: int = Field(
        default=1,
        ge=0,
        le=2,
        description="知识模型输出格式或引用失败时允许的低成本修复次数",
    )
    knowledge_max_output_tokens: int = Field(
        default=3600,
        ge=800,
        le=8000,
        description="完整知识画像的最大输出 token，上限会按目标字段数自动下调",
    )
    knowledge_evidence_max_sources: int = Field(
        default=5,
        ge=2,
        le=8,
        description="知识模型单次生成使用的最大来源数量，证据选择会优先覆盖目标字段",
    )
    knowledge_evidence_manifest_max_characters: int = Field(
        default=12000,
        ge=4000,
        le=18000,
        description="知识模型提示词中 evidence_manifest 的最大正文字符数",
    )
    knowledge_translate_zh_from_en: bool = Field(
        default=True,
        description="中文知识画像优先从已通过引用校验的英文成稿翻译，避免重复消耗完整证据包",
    )
    knowledge_refresh_existing_on_source_change: bool = Field(
        default=False,
        description="来源指纹变化时是否默认重写已有完整知识画像；关闭时只修复缺失或不合格字段",
    )
    knowledge_source_discovery_max_rounds: int = Field(
        default=4,
        ge=1,
        le=12,
        validation_alias="AI__KNOWLEDGE_SOURCE_DISCOVERY_MAX_ROUNDS",
        description="知识画像缺少章节证据时允许的定向来源发现轮次",
    )
    knowledge_automation_enabled: bool = Field(
        default=True,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_ENABLED",
        description="由控制面持续补齐疾病知识 source-first 修复队列",
    )
    knowledge_automation_interval_seconds: int = Field(
        default=60,
        ge=15,
        le=3600,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_INTERVAL_SECONDS",
        description="知识库自动修复队列补位的轮询间隔秒数",
    )
    knowledge_automation_backlog_target: int = Field(
        default=12,
        ge=1,
        le=128,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_BACKLOG_TARGET",
        description="知识库 source-first 活跃任务的受控目标数量",
    )
    knowledge_automation_model_backlog_target: int = Field(
        default=12,
        ge=1,
        le=128,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_MODEL_BACKLOG_TARGET",
        description="旧证据策略重验证允许占用的模型生成 backlog；真实章节缺口不受此限制",
    )
    knowledge_automation_batch_size: int = Field(
        default=6,
        ge=1,
        le=64,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_BATCH_SIZE",
        description="单次知识库自动补位最多创建的任务数",
    )
    knowledge_automation_revalidate_stale: bool = Field(
        default=True,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_REVALIDATE_STALE",
        description="真实章节缺口清空后，自动复核旧证据策略的已发布画像",
    )
    knowledge_automation_evidence_retry_seconds: int = Field(
        default=21600,
        ge=300,
        le=604800,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_EVIDENCE_RETRY_SECONDS",
        description="来源发现轮次耗尽后再次自动发现前的退避秒数",
    )
    knowledge_automation_source_retry_seconds: int = Field(
        default=300,
        ge=30,
        le=21600,
        validation_alias="AI__KNOWLEDGE_AUTOMATION_SOURCE_RETRY_SECONDS",
        description="来源适配器超时或失败后的首次自动重试退避秒数；后续重试指数退避但不会进入人工队列",
    )
    idle_backlog_enabled: bool = Field(
        default=True,
        validation_alias="AI__IDLE_BACKLOG_ENABLED",
        description="闲时由控制面调度模型处理文献雷达、Situation 等 AI 审核积压",
    )
    idle_backlog_interval_seconds: int = Field(
        default=30,
        ge=10,
        le=3600,
        validation_alias="AI__IDLE_BACKLOG_INTERVAL_SECONDS",
        description="闲时积压处理的检查间隔；出现故障时以此为基数指数退避",
    )
    idle_backlog_rest_seconds: int = Field(
        default=1800,
        ge=60,
        le=86400,
        validation_alias="AI__IDLE_BACKLOG_REST_SECONDS",
        description="一轮处理没有进展或队列清空后，该条数据线再次检查前的休息秒数",
    )
    idle_backlog_slice_size: int = Field(
        default=200,
        ge=10,
        le=2000,
        validation_alias="AI__IDLE_BACKLOG_SLICE_SIZE",
        description="每次切片最多领取的条目数；切片之间会重新检查前台任务",
    )
    idle_backlog_review_buffer_size: int = Field(
        default=512,
        ge=1,
        le=512,
        validation_alias="AI__IDLE_BACKLOG_REVIEW_BUFFER_SIZE",
        description="闲时文献审核业务缓冲上限；目标随可用文本模型许可的两倍调整，模型并发由持久运行时控制",
    )
    idle_backlog_max_running_ai_tasks: int = Field(
        default=2,
        ge=0,
        le=32,
        validation_alias="AI__IDLE_BACKLOG_MAX_RUNNING_AI_TASKS",
        description="正在运行的前台 AI 任务超过该数量时不处理积压；有排队的 AI 任务或发布导出时一律让路",
    )
    agent_role_models_raw: str = Field(
        default="",
        description="多专家角色模型偏好，格式 role=model1|model2,role2=model3；为空时回退到 model_chain",
    )
    agent_max_replan_rounds: int = Field(default=1, ge=0, le=10, description="Agent workflow 最大重规划次数")
    agent_max_search_rounds: int = Field(default=2, ge=1, le=10, description="Agent workflow 最大搜索轮次")
    agent_step_token_budget: int = Field(default=2500, gt=0, description="单步 token 预算")
    agent_total_token_budget: int = Field(default=12000, gt=0, description="单次 workflow 总 token 预算")

    # 模型配置
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="生成温度")
    max_tokens: int = Field(default=2000, gt=0, description="最大生成tokens")
    # Reviewer threshold for approval (0.0-1.0)
    reviewer_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Reviewer approval threshold")

    # 成本控制
    enable_cache: bool = Field(default=True, description="是否启用缓存")
    cache_ttl: int = Field(default=168, description="缓存过期时间（小时）")
    rate_limit_cooldown_seconds: int = Field(
        default=10,
        gt=0,
        description="命中429/额度限制后的冷却秒数（连续限流时递增的上限）",
    )
    prompt_auto_reload: bool = Field(
        default=False,
        description="是否启用提示词模板热刷新（开发环境建议开启）",
    )

    @property
    def model_chain(self) -> list[str]:
        """
        Return an explicit environment bootstrap chain only.

        Runtime routing belongs to the model center. Leaving this setting empty
        must not turn legacy default/fallback values into a second routing
        authority.
        """
        raw = self.model_chain_raw.strip()
        candidates = [m.strip() for m in raw.split(",") if m.strip()] if raw else []

        seen: set[str] = set()
        ordered: list[str] = []
        for m in candidates:
            if m and m not in seen:
                seen.add(m)
                ordered.append(m)
        return ordered

    @property
    def agent_role_models(self) -> dict[str, list[str]]:
        """Parse role-specific model preferences.

        Supported formats:
        - JSON object: {"planner": ["gpt-4o-mini", "gpt-4o"], "reviewer": ["gpt-4o-mini"]}
        - Compact string: planner=gpt-4o-mini|gpt-4o,reviewer=gpt-4o-mini
        """
        raw = self.agent_role_models_raw.strip()
        if not raw:
            return {}

        parsed: dict[str, list[str]] = {}
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for key, value in payload.items():
                    role = str(key).strip().lower()
                    if not role:
                        continue
                    if isinstance(value, str):
                        models = [item.strip() for item in value.split("|") if item.strip()]
                    elif isinstance(value, list):
                        models = [str(item).strip() for item in value if str(item).strip()]
                    else:
                        models = []
                    if models:
                        parsed[role] = models
                return parsed

        for chunk in [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]:
            separator = "=" if "=" in chunk else (":" if ":" in chunk else None)
            if not separator:
                continue
            role, model_blob = chunk.split(separator, 1)
            role = role.strip().lower()
            models = [item.strip() for item in model_blob.split("|") if item.strip()]
            if role and models:
                parsed[role] = models
        return parsed


__all__ = [
    "AISettings",
]
