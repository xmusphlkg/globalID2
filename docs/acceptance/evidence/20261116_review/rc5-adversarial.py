"""Offline contract and scheduling counterexamples; no database or email calls."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path.cwd()))
from pydantic import TypeAdapter, ValidationError
from src.domain import TaskStatus
from src.services.automation_service import AutomationJobConfig, AutomationJobState, AutomationService, _ingestion_job_health
from src.services import automation_service as module
from dashboard.api.schemas.sources import AutomationJobOut

job = AutomationJobConfig(job_id='cn-bj-weekly', name='Beijing', country_code='CN-BJ', source='cn_province_monthly_report', interval_minutes=10080, timezone='UTC')
now = datetime(2026, 11, 16, tzinfo=timezone.utc)
task = SimpleNamespace(status=TaskStatus.COMPLETED, completed_at=now, metadata_={'province_ingestion': {'status': 'parsed', 'source_audit': {'latest_report_month': '2026-05', 'latest_discovered_month': '2026-05'}}})
health = _ingestion_job_health(job, task, task, now=now)
print({'scenario': 'stale_source_schema', 'health': health})
try:
    TypeAdapter(AutomationJobOut.model_fields['health_status'].annotation).validate_python(health['health_status'])
except ValidationError as error:
    print(error)

async def paused_between_job_read_and_state_refresh():
    service = AutomationService()
    service._states[job.job_id] = AutomationJobState(next_run_at=datetime.now(timezone.utc) - timedelta(days=1))
    service._config = lambda: SimpleNamespace(timezone='UTC')
    service.load_jobs = AsyncMock(return_value=[replace(job)])
    reads = 0

    async def refresh():
        nonlocal reads
        reads += 1
        # Simulate a different API process persisting the pause after load_jobs
        # returned its enabled=True snapshot, before the final state refresh.
        if reads == 2:
            service._states[job.job_id].next_run_at = None

    service._refresh_persisted_states = refresh
    enqueue = AsyncMock(return_value=SimpleNamespace(created=True, task=SimpleNamespace(task_uuid='offline-task')))
    with patch.object(module.schedule_state_repository, 'reschedule', AsyncMock()), patch.object(module.schedule_state_repository, 'save_result', AsyncMock(return_value=None)), patch.object(module.crawl_task_service, 'enqueue_crawl_task', enqueue):
        result = await service.trigger_job(job.job_id, manual=False)
    print({'scenario': 'pause_after_job_read_before_final_refresh', 'persisted_next_run': None, 'result': result, 'enqueue_calls': enqueue.await_count})

asyncio.run(paused_between_job_read_and_state_refresh())

async def notification_partial_optout_commit_boundary():
    from contextlib import asynccontextmanager
    from src.services import mapping_notification_service as notifications

    skipped = SimpleNamespace(id=7, metadata_={'category_id': 7}, status='pending', last_error=None)
    retained = SimpleNamespace(id=8, metadata_={'category_id': 8}, status='pending', last_error=None,
                               attempts=0, provider='mock', recipients=['test@example.invalid'])
    snapshots = []

    def result(items):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items), scalar_one_or_none=lambda: None)

    async def commit():
        snapshots.append({'skipped': skipped.status, 'retained': retained.status})

    db = SimpleNamespace(execute=AsyncMock(side_effect=[result([]), result([]), result([]), result([skipped, retained]), result([7]), result([retained])]), commit=commit)

    @asynccontextmanager
    async def database():
        yield db

    service = notifications.MappingNotificationService()
    service._digest_content = AsyncMock(return_value=('subject', 'text', 'html'))
    service.transport = SimpleNamespace(send=AsyncMock(return_value=notifications.DeliveryResult(True, False, 'mock', {}, None)))
    with patch.object(notifications, 'get_database', database), patch.dict('os.environ', {'MAPPING_EMAIL_DIGEST_ENABLED': 'true'}):
        await service.process_once(force=True)
    print({'scenario': 'notification_partial_optout_commit_boundary', 'commit_snapshots': snapshots, 'mock_sends': service.transport.send.await_count})

asyncio.run(notification_partial_optout_commit_boundary())
