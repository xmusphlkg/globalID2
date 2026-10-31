"""Generation and public-projection commits share one immutable worker interval."""

import pytest

from src.domain import TaskType
from src.services.task_worker import (
    COLLECTION_STORE_TASK_TYPES,
    _release_memory_blocked_task_types,
)


@pytest.mark.parametrize(
    "store_type",
    [
        TaskType.CRAWL_DATA,
        TaskType.SYNC_LITERATURE,
        TaskType.REFRESH_SITUATION_SOURCES,
        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
        TaskType.UPDATE_DISEASE_KNOWLEDGE,
        TaskType.PROCESS_DATA,
    ],
)
def test_each_formal_store_prevents_release_from_claiming(store_type):
    assert store_type in COLLECTION_STORE_TASK_TYPES
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types([store_type])


def test_release_blocks_every_collection_store_until_projection_commit():
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.EXPORT_DATA]
    )
    assert COLLECTION_STORE_TASK_TYPES <= _release_memory_blocked_task_types(
        [TaskType.EXPORT_DATA]
    )


def test_waiting_release_drains_existing_writers_without_claiming_new_ones():
    blocked = _release_memory_blocked_task_types([], release_waiting=True)
    assert COLLECTION_STORE_TASK_TYPES <= blocked
    assert TaskType.EXPORT_DATA not in blocked
    assert TaskType.SEND_EMAIL not in blocked
    # Existing writes finish before EXPORT_DATA becomes runnable.
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.CRAWL_DATA], release_waiting=True
    )


def test_store_exclusion_does_not_change_other_task_concurrency():
    assert _release_memory_blocked_task_types([TaskType.SEND_EMAIL]) == set()
    # AI waiting is not a new task state or slot: its parent's existing task
    # type protects the eventual store just as an actively computing parent.
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.UPDATE_DISEASE_KNOWLEDGE]
    )
