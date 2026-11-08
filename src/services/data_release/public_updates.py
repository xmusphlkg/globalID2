"""Public, deliberately narrow projection of acknowledged publication markers.

The database remains the source of truth; the feed can be regenerated after an
upload failure without changing a publication task or its acknowledgements.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import re

from sqlalchemy import func, select

from src.domain import Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty

CHANNELS = frozenset({'surveillance', 'situation', 'literature', 'knowledge'})
_SCOPE = re.compile(r'[A-Z]{2}(?:-[A-Z0-9]+)?\Z')
_DISEASE = re.compile(r'D[A-Z0-9_-]{2,30}\Z')


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc))


def project_updates(rows) -> dict[str, bytes]:
    """Build deterministic UTC-month files from successful published markers.

    No task inputs, raw logs, internal URLs, errors, or marker IDs are exposed.
    Rows must come from ``read_published_markers``, which verifies task success.
    """
    grouped = {}
    for line, scope, published_at, task_uuid in rows:
        if line not in CHANNELS or not published_at or not task_uuid:
            continue
        key = (str(task_uuid), line)
        record = grouped.setdefault(key, {'channel': line, 'published_at': _utc(published_at), 'scopes': set()})
        record['published_at'] = max(record['published_at'], _utc(published_at))
        if isinstance(scope, str) and ((line == 'surveillance' and _SCOPE.fullmatch(scope))
                                      or (line == 'knowledge' and _DISEASE.fullmatch(scope))):
            record['scopes'].add(scope)
    months = defaultdict(list)
    for record in grouped.values():
        date = record['published_at']
        event = {'channel': record['channel'], 'published_at': date.isoformat(timespec='microseconds').replace('+00:00', 'Z')}
        if record['scopes']:
            event['scopes'] = sorted(record['scopes'])
        months[date.strftime('%Y-%m')].append(event)
    files = {}
    for month, events in sorted(months.items(), reverse=True):
        events.sort(key=lambda item: (item['published_at'], item['channel'], item.get('scopes', [])), reverse=True)
        files[f'{month}.json'] = _encode({'schema_version': 1, 'month': month, 'events': events})
    counts = {channel: 0 for channel in sorted(CHANNELS)}
    for record in grouped.values():
        counts[record['channel']] += 1
    files['latest.json'] = _encode({'schema_version': 1, 'months': sorted(months, reverse=True), 'counts': counts})
    return files


def _encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


async def read_published_markers(db):
    query = (select(PublishDirty.line, PublishDirty.scope_key, func.max(PublishDirty.published_at).label("published_at"), PublishDirty.published_task_uuid)
             .join(Task, Task.task_uuid == PublishDirty.published_task_uuid)
             .where(PublishDirty.line.in_(CHANNELS), PublishDirty.published_at.is_not(None),
                    Task.task_type == TaskType.EXPORT_DATA, Task.status == TaskStatus.COMPLETED)
             .group_by(PublishDirty.line, PublishDirty.scope_key, PublishDirty.published_task_uuid))
    return (await db.execute(query)).all()
