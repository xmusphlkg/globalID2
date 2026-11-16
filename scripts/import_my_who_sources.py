#!/usr/bin/env python3
"""Import Malaysia WHO histories through staged ingestion with a foreground lease."""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy import select
from src.core.database import get_db,dispose_database
from src.core.task_manager import task_manager
from src.domain import Country,CrawlRun,Task
from src.domain.collection import RunStage
from src.domain.task import TaskStatus,TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.foreground import foreground_task_lease,ForegroundTaskLeaseLostError

SOURCES=('who_flunet_weekly','who_gho_annual')


async def run_source(source,*,start_year=None):
    async with get_db() as db:
        country_id=(await db.execute(select(Country.id).where(Country.code=='MY'))).scalar_one()
        active=(await db.execute(select(Task).where(Task.country_id==country_id,Task.task_type==TaskType.CRAWL_DATA,Task.status.in_([TaskStatus.RUNNING,TaskStatus.PENDING,TaskStatus.QUEUED,TaskStatus.RETRYING])))).scalars().all()
        if any((t.input_data or {}).get('source')==source for t in active):
            raise ValueError('This Malaysia WHO source already has an active task')
    options={} if start_year is None else {'start_year':int(start_year)}
    task=await task_manager.create_task(task_type=TaskType.CRAWL_DATA,task_name=f'Malaysia WHO historical import ({source})',country_id=country_id,status=TaskStatus.RUNNING,input_data={'country_code':'MY','source':source,'manual_trigger':True,'mapping_notifications_enabled':False,**options},tags=['my-who','foreground','full-source-history','no-notifications'])
    await task_manager.update_task_status(task.task_uuid,TaskStatus.RUNNING)
    print(json.dumps({'task_uuid':task.task_uuid,'source':source,'status':'running'}),flush=True)
    lease=None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result=await CrawlService().execute(task,country_code='MY',source=source,force=False,process=True,save_raw=True,fill_missing=False,include_current_month=False)
        async with get_db() as db:
            stage=(await db.execute(select(RunStage).where(RunStage.task_uuid==task.task_uuid,RunStage.stage=='organize').order_by(RunStage.attempt.desc()))).scalars().first()
            output={'source':source,'task_uuid':task.task_uuid,'status':'completed',**asdict(result),'source_audit':(stage.metrics or {}).get('source_audit',{})}
            saved=await lease.owned_task(db);saved.status=TaskStatus.COMPLETED;saved.progress=100;saved.completed_at=datetime.now(timezone.utc);saved.output_data=output
        print(json.dumps(output,ensure_ascii=False),flush=True)
        return output
    except ForegroundTaskLeaseLostError:
        raise
    except BaseException as exc:
        if lease is not None:
            async with get_db() as db:
                saved=await lease.owned_task(db);saved.status=TaskStatus.FAILED;saved.last_error=str(exc);saved.completed_at=datetime.now(timezone.utc)
                for run in (await db.execute(select(CrawlRun).where(CrawlRun.raw_dir.like(f'%/{task.task_uuid}/%'),CrawlRun.status=='running'))).scalars():
                    run.status='failed';run.finished_at=saved.completed_at;run.error_message=str(exc)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',choices=SOURCES,nargs='+',default=list(SOURCES))
    parser.add_argument('--start-year',type=int)
    parser.add_argument('--result-json',type=Path,default=ROOT/'data/current/my/who_import_result.json')
    args=parser.parse_args()
    async def execute():
        try:
            results=[]
            for source in args.sources:results.append(await run_source(source,start_year=args.start_year))
            args.result_json.parent.mkdir(parents=True,exist_ok=True);args.result_json.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
        finally:await dispose_database()
    asyncio.run(execute())


if __name__=='__main__':main()
