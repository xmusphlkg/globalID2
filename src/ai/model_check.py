"""
启动时检查配置中的模型是否可用，后续仅从可用模型中选择调用。

在报告生成等使用 AI 的命令开始时调用 ensure_available_models_checked()，
会依次对 model_chain 中的每个模型做一次轻量级 completion 测试，
得到可用列表并写入 BaseAgent.AVAILABLE_MODEL_CHAIN，供所有 Agent 使用。
"""
from typing import List, Optional

from src.core import get_config, get_logger
from src.ai.model_center import (
    check_all_models,
    get_active_model_routes,
)

logger = get_logger(__name__)


async def check_available_models() -> List[str]:
    """
    对配置中的模型链（model_chain）逐项做连通性测试，返回可用模型列表（保持原顺序）。
    若未配置 enable_api_test 或模型链为空，则返回完整链（不测试）。
    """
    config = get_config()

    # Prefer runtime routes from model center (DB-backed).
    routes = await get_active_model_routes()
    if routes:
        if not getattr(config.ai, "enable_api_test", True):
            return [str(r.get("model_name")) for r in routes if r.get("model_name")]

        results = await check_all_models()
        available: List[str] = []
        for item in results:
            if item.get("success"):
                model_name = str(item.get("model_name") or "")
                if model_name and model_name not in available:
                    available.append(model_name)
                    logger.info(f"Model '{model_name}' is available.")
            else:
                logger.warning(f"Model check failed: {item.get('message', 'unknown')} ({item.get('model_name')})")
        return available

    # Backward-compatible fallback to env model_chain when model center is empty.
    chain = getattr(config.ai, "model_chain", None) or []
    if not chain:
        logger.warning("Model chain is empty, no model to check.")
        return []

    if not getattr(config.ai, "enable_api_test", True):
        logger.info("API test disabled (enable_api_test=False), using full model chain.")
        return list(chain)

    from src.ai.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent()
    available: List[str] = []
    for model_name in chain:
        agent.model = model_name
        try:
            result = await agent.test_connection()
            if result.get("success"):
                available.append(model_name)
                logger.info(f"Model '{model_name}' is available.")
            else:
                err = result.get("error", result.get("message", "Unknown"))
                logger.warning(f"Model '{model_name}' failed: {err}")
        except Exception as e:
            logger.warning(f"Model '{model_name}' error: {e}")

    return available


def ensure_available_models_checked() -> Optional[List[str]]:
    """
    若尚未执行过模型检查，则执行一次并将结果写入 BaseAgent.AVAILABLE_MODEL_CHAIN；
    若已执行过则直接返回当前可用列表。
    返回当前用于调用的模型列表（可能为 None 表示使用默认逻辑）。
    此函数为同步接口，内部会获取事件循环并 run check_available_models。
    """
    from src.ai.agents.base import BaseAgent
    import asyncio

    if BaseAgent.AVAILABLE_MODEL_CHAIN is not None:
        return BaseAgent.AVAILABLE_MODEL_CHAIN

    if BaseAgent.AVAILABLE_MODEL_ROUTES is None:
        try:
            BaseAgent.AVAILABLE_MODEL_ROUTES = asyncio.run(get_active_model_routes())
        except Exception as e:
            logger.warning(f"Failed to load model-center routes: {e}")
            BaseAgent.AVAILABLE_MODEL_ROUTES = None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        available = asyncio.run(check_available_models())
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, check_available_models())
            available = future.result()

    config = get_config()
    chain = getattr(config.ai, "model_chain", None) or []
    if not available and chain:
        logger.warning(
            f"No model in chain is available; will still try full chain at runtime. Chain: {chain}"
        )
        BaseAgent.AVAILABLE_MODEL_CHAIN = list(chain)
    else:
        BaseAgent.AVAILABLE_MODEL_CHAIN = available if available else None
    return BaseAgent.AVAILABLE_MODEL_CHAIN


async def ensure_available_models_checked_async() -> Optional[List[str]]:
    """
    异步版本：若尚未执行过模型检查则执行并设置 BaseAgent.AVAILABLE_MODEL_CHAIN。
    在已处于 async 上下文中时使用此函数，避免嵌套事件循环。
    """
    from src.ai.agents.base import BaseAgent

    if BaseAgent.AVAILABLE_MODEL_CHAIN is not None:
        return BaseAgent.AVAILABLE_MODEL_CHAIN

    if BaseAgent.AVAILABLE_MODEL_ROUTES is None:
        try:
            BaseAgent.AVAILABLE_MODEL_ROUTES = await get_active_model_routes()
        except Exception as e:
            logger.warning(f"Failed to load model-center routes asynchronously: {e}")
            BaseAgent.AVAILABLE_MODEL_ROUTES = None

    available = await check_available_models()
    config = get_config()
    chain = getattr(config.ai, "model_chain", None) or []
    if not available and chain:
        logger.warning(
            f"No model in chain is available; will still try full chain at runtime. Chain: {chain}"
        )
        BaseAgent.AVAILABLE_MODEL_CHAIN = list(chain)
    else:
        BaseAgent.AVAILABLE_MODEL_CHAIN = available if available else None
    return BaseAgent.AVAILABLE_MODEL_CHAIN
