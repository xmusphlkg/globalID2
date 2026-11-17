#!/usr/bin/env python3
"""Audit archived province images/PDFs; never import observations.

Default: prepare a source manifest only. --live creates an auditable parent task and uses the
normal durable dispatcher with a worker dedicated to native-file activities.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))



class ImageReviewTimeout(TimeoutError):
    def __init__(self, task_uuid: str):
        super().__init__('Model-center image review timed out')
        self.task_uuid = task_uuid


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--province', required=True)
    parser.add_argument('--month', required=True)
    parser.add_argument('--document', action='append', required=True, type=Path)
    parser.add_argument('--source-url', action='append', default=[], help='One original source URL per document, in order')
    parser.add_argument('--article-file', type=Path, help='Captured official article text, including title and report period')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--live', action='store_true', help='Use model-center routes through the durable native-file worker')
    parser.add_argument('--retry-from', type=Path, help='Re-vote archived originals into a new output; never reuse prior votes')
    parser.add_argument('--panel-size', type=int, choices=(3, 5, 7), default=3, help='Fixed expert seats; approval requires an absolute majority')
    parser.add_argument('--timeout', type=float, default=600)
    return parser


async def _live_review(documents, args):
    from sqlalchemy import select
    from src.ai.model_center import get_runtime_routes
    from src.ai.runtime.context import task_context
    from src.ai.runtime.dispatcher import AIWorkDispatcher
    from src.core.database import get_db
    from src.domain import Task, TaskStatus, TaskType
    from src.services.cn_province_image_review import review_image_report

    task = Task(task_uuid=str(uuid4()), task_name=f'{args.province} {args.month} original-image expert-vote audit',
                task_type=TaskType.AGENT_WORKFLOW, status=TaskStatus.RUNNING, priority='low',
                started_at=datetime.now(timezone.utc),
                input_data={'operation': 'cn_province_image_audit', 'province': args.province, 'month': args.month},
                metadata_={'review_only': True})
    async with get_db() as db:
        db.add(task)
    # Capability-specific worker. Actual selection, admission, leases, retries,
    # model diversity and provider transport all remain in the common runtime.
    async def native_routes():
        return [{**route, 'capabilities': ['native_file']} for route in await get_runtime_routes()
                if 'native_file' in route.get('capabilities', [])]
    dispatcher = AIWorkDispatcher(route_loader=native_routes)
    stop = asyncio.Event()
    worker = asyncio.create_task(dispatcher.run(stop))
    result = None
    try:
        async with asyncio.timeout(args.timeout), task_context(task):
            result = await review_image_report(documents=documents, panel_size=args.panel_size,
                province_code=args.province, report_month=args.month,
                article_text=args.article_file.read_text() if args.article_file else '')
        result['task_uuid'] = task.task_uuid
        return result
    except TimeoutError as exc:
        raise ImageReviewTimeout(task.task_uuid) from exc
    finally:
        stop.set()
        await worker
        async with get_db() as db:
            saved = await db.scalar(select(Task).where(Task.task_uuid == task.task_uuid).with_for_update())
            saved.status = TaskStatus.COMPLETED if result is not None else TaskStatus.CANCELLED
            saved.completed_at = datetime.now(timezone.utc)
            saved.output_data = {'status': result['status'] if result else 'cancelled', 'audit_path': str(args.output)}
            saved.metadata_ = {**(saved.metadata_ or {}), 'review_only': True}


async def run(args):
    from src.data.crawlers.cn_provinces import province_configs
    from src.services.cn_province_image_review import PROTOCOL
    if args.province not in province_configs() or date.fromisoformat(args.month + '-01') >= datetime.now(ZoneInfo('Asia/Shanghai')).date().replace(day=1):
        raise ValueError('A configured province and completed report month are required')
    if args.source_url and len(args.source_url) != len(args.document):
        raise ValueError('Supply exactly one --source-url per --document')
    if args.timeout <= 0:
        raise ValueError('timeout must be positive')
    if args.output.exists():
        raise ValueError('Review output already exists; choose a new path to preserve its audit')
    documents = []
    for index, path in enumerate(args.document):
        data = path.read_bytes()
        media = mimetypes.guess_type(path)[0] or ''
        document = {'data': data, 'media_type': media, 'sha256': hashlib.sha256(data).hexdigest(),
                    'source_url': args.source_url[index] if args.source_url else path.resolve().as_uri()}
        documents.append(document)
    previous_digest = None
    if args.retry_from:
        if not args.live:
            raise ValueError('--retry-from requires --live')
        previous_bytes = args.retry_from.read_bytes()
        previous = json.loads(previous_bytes)
        audit = previous.get('audit') or {}
        old_documents = audit.get('documents') or []
        expected = [{key: doc[key] for key in ('sha256', 'source_url', 'media_type')} for doc in documents]
        observed = [{key: doc.get(key) for key in ('sha256', 'source_url', 'media_type')} for doc in old_documents]
        if (previous.get('status') != 'needs_review' or audit.get('protocol') != PROTOCOL
                or audit.get('province_code') != args.province or audit.get('report_month') != args.month
                or audit.get('panel_size') != args.panel_size or observed != expected):
            raise ValueError('Previous audit scope, panel or original documents differ; refusing retry')
        if audit.get('article_sha256') is not None:
            article = args.article_file.read_text().encode('utf-8') if args.article_file else b''
            if hashlib.sha256(article).hexdigest() != audit['article_sha256']:
                raise ValueError('Captured article text differs from the previous audit')
        previous_digest = hashlib.sha256(previous_bytes).hexdigest()
    article_text = args.article_file.read_text() if args.article_file else ''
    result = {'status': 'needs_review', 'rows': [], 'audit': {'protocol': PROTOCOL,
              'province_code': args.province, 'report_month': args.month, 'status': 'needs_review',
              'article_sha256': hashlib.sha256(article_text.encode('utf-8')).hexdigest(),
              'panel_size': args.panel_size, 'quorum': args.panel_size // 2 + 1,
              'documents': [{key: doc[key] for key in ('media_type', 'sha256', 'source_url')} for doc in documents],
              'reason': 'expert_image_votes_not_run', 'next_action': 'run_live_vote_or_review_originals_manually'}}
    if args.live:
        try:
            result = await _live_review(documents, args)
        except TimeoutError as exc:
            result['audit']['reason'] = 'model_center_review_timed_out'
            result['audit']['next_action'] = 'retry_or_review_originals_manually'
            if getattr(exc, 'task_uuid', None):
                result['task_uuid'] = exc.task_uuid
    if previous_digest:
        result['retry_of_audit_sha256'] = previous_digest
        result['retry_of_task_uuid'] = previous.get('task_uuid')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'rows': len(result['rows']), 'output': str(args.output),
                      'task_uuid': result.get('task_uuid')}, ensure_ascii=False))
    return 0 if result['status'] == 'validated' else 2


async def main():
    try:
        return await run(build_parser().parse_args())
    finally:
        from src.core.database import dispose_database
        await dispose_database()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
