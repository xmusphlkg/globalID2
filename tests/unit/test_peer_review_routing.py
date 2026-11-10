"""Business review expresses diversity constraints without a local route pool."""
from unittest.mock import AsyncMock

from src.ai.peer_review_completer import ModelCenterCompleter
from src.ai.runtime.contracts import RoutingRequirements


async def test_review_tracks_actual_cached_provider_route_and_requires_diversity():
    complete = ModelCenterCompleter(allow_single_model=False, avoid_models=("prior:model",))
    author = complete.agents["author"]
    author.complete = AsyncMock(return_value="draft")
    author.conversation_history = [{"model": "same-name", "metadata": {
        "cache_hit": True, "runtime_route": {"model_key": "provider-a:same-name"},
    }}]
    assert (await complete("author", "system", "prompt")).model == "provider-a:same-name"
    reviewer = complete.agents["reviewer"]
    reviewer.complete = AsyncMock(return_value="accept")
    reviewer.conversation_history = [{"model": "same-name", "metadata": {
        "runtime_route": {"model_key": "provider-b:same-name"},
    }}]
    reply = await complete("reviewer", "system", "review")
    assert reply.model == "provider-b:same-name"
    routing = reviewer.complete.await_args.kwargs["routing"]
    assert routing == RoutingRequirements(
        avoid_models=("prior:model", "provider-a:same-name", "same-name"), require_distinct_model=True,
    )
    assert complete.review_mode == "single_model"


async def test_revision_preserves_author_preference_without_pinning_transport():
    complete = await ModelCenterCompleter.for_reviewing("provider:author", line="knowledge")
    assert complete.allow_single_model is False
    author = complete.agents["author"]
    author.complete = AsyncMock(return_value="revised")
    author.conversation_history = [{"model": "served"}]
    await complete("author", "system", "revise")
    assert author.complete.await_args.kwargs["routing"].preferred_models == ("provider:author",)
    assert "_scheduled_route" not in author.complete.await_args.kwargs


async def test_single_model_is_reported_from_actual_results():
    complete = await ModelCenterCompleter.from_model_center(allow_single_model=True)
    for role in ("author", "reviewer"):
        agent = complete.agents[role]
        agent.complete = AsyncMock(return_value="result")
        agent.conversation_history = [{"model": "only-model"}]
        await complete(role, "system", "prompt")
    assert complete.review_mode == "single_model"
    assert not complete.agents["reviewer"].complete.await_args.kwargs["routing"].require_distinct_model


async def test_default_review_requires_an_independent_model():
    complete = await ModelCenterCompleter.from_model_center()
    assert complete.allow_single_model is False
