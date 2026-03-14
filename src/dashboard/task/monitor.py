"""Lightweight task monitor for realtime task tracking in Streamlit."""
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import desc, func, or_, select

from src.core.database import get_db
from src.domain import AIConversation, Report, ReportSection, ReportSectionRun, Task, TaskStatus, TaskType, TaskWorkbook

from .async_helper import run_async
from .ai_details import render_ai_conversation, render_quality_scores


ACTIVE_TASK_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
}


def _txt(en: str, zh: str) -> str:
    return zh if st.session_state.get("lang") == "zh" else en


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _enum_label(value: Any) -> str:
    raw = _enum_value(value)
    return raw.replace("_", " ").title()


def _status_badge(status: str) -> str:
    return {
        "pending": "🟡",
        "queued": "🟠",
        "running": "🔵",
        "retrying": "🟣",
        "completed": "🟢",
        "failed": "🔴",
        "cancelled": "⚫",
    }.get(status, "⚪")


def _format_timestamp(value: Optional[datetime]) -> str:
    if not value:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_duration(task: Dict[str, Any]) -> str:
    duration = task.get("actual_duration")
    if duration is not None:
        return f"{duration}s"

    started_at = task.get("started_at")
    if not started_at:
        return "-"

    started = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    return f"{max(0, int((now_utc - started).total_seconds()))}s"


def _default_task_uuid(tasks: List[Dict[str, Any]]) -> Optional[str]:
    if not tasks:
        return None
    active = next((task for task in tasks if task["status"] in {s.value for s in ACTIVE_TASK_STATUSES}), None)
    return (active or tasks[0])["task_uuid"]


async def _fetch_monitor_payload(
    statuses: List[str],
    task_types: List[str],
    limit: int,
    search_text: str,
) -> Dict[str, Any]:
    async with get_db() as db:
        status_rows = await db.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status)
        )
        type_rows = await db.execute(
            select(Task.task_type, func.count(Task.id)).group_by(Task.task_type)
        )

        query = select(Task).order_by(desc(Task.created_at)).limit(limit)
        if statuses:
            query = query.where(Task.status.in_([TaskStatus(status) for status in statuses]))
        if task_types:
            query = query.where(Task.task_type.in_([TaskType(task_type) for task_type in task_types]))
        if search_text:
            like = f"%{search_text.strip()}%"
            query = query.where(
                or_(
                    Task.task_name.ilike(like),
                    Task.task_uuid.ilike(like),
                    Task.description.ilike(like),
                )
            )

        tasks = list((await db.execute(query)).scalars().all())
        task_ids = [task.id for task in tasks]

        workbook_summary: Dict[int, Dict[str, Any]] = {}
        if task_ids:
            workbook_rows = await db.execute(
                select(
                    TaskWorkbook.task_id,
                    func.count(TaskWorkbook.id).label("entry_count"),
                    func.max(TaskWorkbook.created_at).label("last_entry_at"),
                )
                .where(TaskWorkbook.task_id.in_(task_ids))
                .group_by(TaskWorkbook.task_id)
            )
            for row in workbook_rows:
                workbook_summary[row.task_id] = {
                    "entry_count": row.entry_count,
                    "last_entry_at": row.last_entry_at,
                }

        report_ids = {
            task.report_id or (task.output_data or {}).get("report_id")
            for task in tasks
            if task.report_id or (task.output_data or {}).get("report_id")
        }
        report_summary: Dict[int, Dict[str, Any]] = {}
        if report_ids:
            report_rows = await db.execute(
                select(
                    Report.id,
                    Report.status,
                    func.count(ReportSection.id).label("section_count"),
                )
                .outerjoin(ReportSection, ReportSection.report_id == Report.id)
                .where(Report.id.in_(report_ids))
                .group_by(Report.id, Report.status)
            )
            for row in report_rows:
                report_summary[row.id] = {
                    "status": _enum_value(row.status),
                    "section_count": row.section_count,
                }

        serialized_tasks = []
        for task in tasks:
            inferred_report_id = task.report_id or (task.output_data or {}).get("report_id")
            workbook = workbook_summary.get(task.id, {})
            report = report_summary.get(inferred_report_id, {}) if inferred_report_id else {}
            serialized_tasks.append(
                {
                    "id": task.id,
                    "task_uuid": task.task_uuid,
                    "task_name": task.task_name,
                    "description": task.description,
                    "status": _enum_value(task.status),
                    "task_type": _enum_value(task.task_type),
                    "priority": _enum_value(task.priority),
                    "progress": task.progress,
                    "created_at": task.created_at,
                    "started_at": task.started_at,
                    "completed_at": task.completed_at,
                    "actual_duration": task.actual_duration,
                    "last_error": task.last_error,
                    "input_data": task.input_data or {},
                    "output_data": task.output_data or {},
                    "country_code": (task.input_data or {}).get("country"),
                    "workbook_count": workbook.get("entry_count", 0),
                    "last_workbook_at": workbook.get("last_entry_at"),
                    "report_id": inferred_report_id,
                    "report_status": report.get("status"),
                    "report_section_count": report.get("section_count", 0),
                }
            )

        stats_by_status = {_enum_value(row[0]): row[1] for row in status_rows}
        stats_by_type = {_enum_value(row[0]): row[1] for row in type_rows}

        return {
            "tasks": serialized_tasks,
            "stats": {
                "by_status": stats_by_status,
                "by_type": stats_by_type,
                "total": sum(stats_by_status.values()),
            },
        }


async def _fetch_task_detail(task_uuid: str) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one_or_none()
        if not task:
            return None

        workbook_entries = list(
            (
                await db.execute(
                    select(TaskWorkbook)
                    .where(TaskWorkbook.task_id == task.id)
                    .order_by(desc(TaskWorkbook.created_at))
                    .limit(200)
                )
            ).scalars().all()
        )

        report_id = task.report_id or (task.output_data or {}).get("report_id")
        report_summary = None
        if report_id:
            report = await db.get(Report, report_id)
            if report:
                section_count = (
                    await db.execute(
                        select(func.count(ReportSection.id)).where(ReportSection.report_id == report_id)
                    )
                ).scalar_one()
                run_rows = await db.execute(
                    select(ReportSectionRun.status, func.count(ReportSectionRun.id))
                    .where(ReportSectionRun.report_id == report_id)
                    .group_by(ReportSectionRun.status)
                )
                report_summary = {
                    "id": report.id,
                    "title": report.title,
                    "status": _enum_value(report.status),
                    "quality_score": report.quality_score,
                    "generation_time": report.generation_time,
                    "section_count": section_count,
                    "run_counts": {_enum_value(row[0]): row[1] for row in run_rows},
                    "markdown_path": report.markdown_path,
                    "html_path": report.html_path,
                    "pdf_path": report.pdf_path,
                }

        return {
            "task": {
                "task_uuid": task.task_uuid,
                "task_name": task.task_name,
                "description": task.description,
                "status": _enum_value(task.status),
                "task_type": _enum_value(task.task_type),
                "priority": _enum_value(task.priority),
                "progress": task.progress,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "actual_duration": task.actual_duration,
                "last_error": task.last_error,
                "input_data": task.input_data or {},
                "output_data": task.output_data or {},
            },
            "workbook": [
                {
                    "created_at": entry.created_at,
                    "entry_type": entry.entry_type,
                    "title": entry.title,
                    "content": entry.content,
                    "model_used": entry.model_used,
                    "tokens_used": entry.tokens_used,
                    "duration": entry.duration,
                    "error_message": entry.error_message,
                }
                for entry in workbook_entries
            ],
            "report": report_summary,
        }


async def _fetch_report_trace(report_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        report = await db.get(Report, report_id)
        if not report:
            return None

        sections_raw = list(
            (
                await db.execute(
                    select(ReportSection)
                    .where(ReportSection.report_id == report_id)
                    .order_by(ReportSection.section_order)
                )
            ).scalars().all()
        )

        runs_raw = list(
            (
                await db.execute(
                    select(ReportSectionRun)
                    .where(ReportSectionRun.report_id == report_id)
                    .order_by(desc(ReportSectionRun.created_at))
                )
            ).scalars().all()
        )

        run_ids = [run.id for run in runs_raw]
        conversations_by_run: Dict[int, List[Dict[str, Any]]] = {}
        if run_ids:
            conversations = list(
                (
                    await db.execute(
                        select(AIConversation)
                        .where(AIConversation.run_id.in_(run_ids))
                        .order_by(AIConversation.timestamp)
                    )
                ).scalars().all()
            )
            for conversation in conversations:
                conversations_by_run.setdefault(conversation.run_id, []).append(
                    {
                        "agent": conversation.agent,
                        "role": conversation.role,
                        "timestamp": conversation.timestamp.isoformat() if conversation.timestamp else "",
                        "prompt": conversation.prompt,
                        "system_prompt": conversation.system_prompt,
                        "response": conversation.response,
                        "model": conversation.model,
                        "provider": conversation.provider,
                        "tokens": conversation.tokens or {},
                        "duration": conversation.duration or 0,
                        "temperature": conversation.temperature,
                    }
                )

        return {
            "report": {
                "id": report.id,
                "title": report.title,
                "status": _enum_value(report.status),
                "quality_score": report.quality_score,
                "generation_time": report.generation_time,
                "token_usage": report.token_usage or {},
            },
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "section_type": section.section_type,
                    "section_order": section.section_order,
                    "content": section.content,
                    "data_sources": section.data_sources or [],
                    "ai_model": section.ai_model,
                    "generation_time": section.generation_time,
                    "token_count": section.token_count,
                    "is_verified": section.is_verified,
                }
                for section in sections_raw
            ],
            "runs": [
                {
                    "id": run.id,
                    "section_id": run.section_id,
                    "section_type": run.section_type,
                    "disease_name": run.disease_name,
                    "status": _enum_value(run.status),
                    "provider": run.provider,
                    "model": run.model,
                    "token_usage": run.token_usage or {},
                    "quality_scores": run.quality_scores or {},
                    "error_message": run.error_message,
                }
                for run in runs_raw
            ],
            "conversations_by_run": conversations_by_run,
        }


def _group_report_trace(trace: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    latest_run_by_key: Dict[tuple, Dict[str, Any]] = {}
    for run in trace["runs"]:
        key = (run.get("disease_name"), run.get("section_type"))
        if key not in latest_run_by_key:
            latest_run_by_key[key] = run

    disease_groups: Dict[str, List[Dict[str, Any]]] = {}
    processed_keys = set()

    for section in trace["sections"]:
        parts = section["title"].split(" - ")
        disease_name = parts[0] if len(parts) >= 2 else _txt("Unknown", "未知")
        run = next((item for item in trace["runs"] if item.get("section_id") == section["id"]), None)
        if not run and len(parts) >= 2:
            run = latest_run_by_key.get((parts[0], parts[1]))
        disease_groups.setdefault(disease_name, []).append({"section": section, "run": run})
        if len(parts) >= 2:
            processed_keys.add((parts[0], parts[1]))
        if run:
            processed_keys.add((run.get("disease_name"), run.get("section_type")))

    for key, run in latest_run_by_key.items():
        if key in processed_keys:
            continue
        disease_name = run.get("disease_name") or _txt("Unknown", "未知")
        disease_groups.setdefault(disease_name, []).append({"section": None, "run": run})

    return disease_groups


def _render_report_trace(task_uuid: str, report_id: int, outcome_data: Dict[str, Any]) -> None:
    state_key = f"task_monitor_trace_enabled_{task_uuid}"
    if st.button(_txt("Load AI trace", "加载 AI 追踪"), key=f"trace_btn_{task_uuid}", width="content"):
        st.session_state[state_key] = True

    if not st.session_state.get(state_key):
        st.caption(
            _txt(
                "AI conversation, section outcome, data sources, and token details load on demand to keep the page responsive.",
                "AI 对话、章节结果、数据来源和 token 详情按需加载，以保持页面响应速度。",
            )
        )
        return

    trace = run_async(_fetch_report_trace(report_id))
    if not trace:
        st.warning(_txt("Report trace is unavailable.", "报告追踪信息暂不可用。"))
        return

    summary_tabs = st.tabs([
        _txt("Outcome", "结果"),
        _txt("Tokens", "Tokens"),
        _txt("Sections & AI", "章节与 AI"),
    ])

    with summary_tabs[0]:
        st.markdown(f"**{_txt('Task outcome', '任务结果')}**")
        st.json(outcome_data or {})
        report_summary = trace["report"]
        summary_df = pd.DataFrame(
            [
                {"field": _txt("Report title", "报告标题"), "value": report_summary["title"]},
                {"field": _txt("Status", "状态"), "value": _enum_label(report_summary["status"])},
                {"field": _txt("Generation time", "生成耗时"), "value": report_summary.get("generation_time") or "-"},
                {"field": _txt("Quality score", "质量分"), "value": report_summary.get("quality_score") or "-"},
            ]
        )
        st.dataframe(summary_df, width="stretch", hide_index=True)

    with summary_tabs[1]:
        token_usage = trace["report"].get("token_usage") or {}
        if token_usage:
            st.json(token_usage)
        else:
            st.info(_txt("No report-level token usage recorded.", "没有记录报告级 token 使用信息。"))

    with summary_tabs[2]:
        disease_groups = _group_report_trace(trace)
        if not disease_groups:
            st.info(_txt("No section or run trace recorded yet.", "还没有章节或运行轨迹。"))
        for disease_name, group_items in sorted(disease_groups.items()):
            statuses = [item["run"]["status"] for item in group_items if item.get("run")]
            icon = "🟢" if statuses and all(status == "completed" for status in statuses) else (
                "🔵" if any(status == "running" for status in statuses) else (
                    "🟡" if any(status == "queued" for status in statuses) else "⚪"
                )
            )
            status_label = ", ".join(sorted({_enum_label(status) for status in statuses})) if statuses else "-"
            with st.expander(f"{icon} {disease_name} · {status_label}", expanded=False):
                for item in group_items:
                    section = item.get("section")
                    run = item.get("run")
                    title = section["title"] if section else f"{run.get('disease_name') or disease_name} - {run.get('section_type') or ''}"
                    section_type = (section or {}).get("section_type") or (run or {}).get("section_type") or "-"
                    model = (run or {}).get("model") or (section or {}).get("ai_model") or "-"
                    token_total = ((run or {}).get("token_usage") or {}).get("total") or (section or {}).get("token_count") or 0
                    quality_scores = (run or {}).get("quality_scores") or {}
                    with st.expander(
                        f"{_enum_label(section_type)} | {_enum_label((run or {}).get('status') or 'completed')} | {model} | {token_total} tokens",
                        expanded=False,
                    ):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric(_txt("Type", "类型"), _enum_label(section_type))
                        c2.metric(_txt("Model", "模型"), model)
                        c3.metric(_txt("Tokens", "Tokens"), token_total)
                        c4.metric(_txt("Verified", "已校验"), "Yes" if (section or {}).get("is_verified") else "No")

                        detail_tabs = st.tabs([
                            _txt("Conversation", "对话"),
                            _txt("Outcome", "结果"),
                            _txt("Quality", "质量"),
                            _txt("Data", "数据"),
                        ])
                        with detail_tabs[0]:
                            if run:
                                render_ai_conversation(trace["conversations_by_run"].get(run["id"], []), title)
                            else:
                                st.info(_txt("No AI conversation recorded.", "没有 AI 对话记录。"))
                        with detail_tabs[1]:
                            content = (section or {}).get("content")
                            if content:
                                st.markdown(content)
                            else:
                                st.info(_txt("No final outcome recorded yet.", "还没有最终产出内容。"))
                            if run and run.get("error_message"):
                                st.error(run["error_message"])
                        with detail_tabs[2]:
                            if quality_scores:
                                render_quality_scores(quality_scores)
                            else:
                                st.info(_txt("No quality scores available.", "没有质量评分。"))
                        with detail_tabs[3]:
                            data_sources = (section or {}).get("data_sources") or []
                            if data_sources:
                                for source in data_sources:
                                    st.json(source)
                            else:
                                st.info(_txt("No data source metadata recorded.", "没有记录数据来源元数据。"))


def _render_header(standalone: bool) -> None:
    title = _txt("Task Monitor", "任务监控")
    subtitle = _txt(
        "Realtime task status, logs, and report linkage without the heavy legacy task center.",
        "面向实时状态、日志和报告关联的轻量任务监控页，替代旧版重型任务中心。",
    )
    st.header(title)
    st.caption(subtitle)
    if standalone:
        st.info(
            _txt(
                "This standalone app can auto-refresh safely without forcing the main analytics dashboard to rerun.",
                "这个独立页面可以安全自动刷新，不会拖慢主分析大盘。",
            )
        )
    else:
        st.caption(
            _txt(
                "Dedicated standalone entry: streamlit run src/dashboard/task_dashboard.py",
                "独立入口：streamlit run src/dashboard/task_dashboard.py",
            )
        )


def _render_controls() -> Dict[str, Any]:
    status_options = [status.value for status in TaskStatus]
    type_options = [task_type.value for task_type in TaskType]

    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 1, 2])
    with ctrl1:
        selected_statuses = st.multiselect(
            _txt("Statuses", "状态"),
            options=status_options,
            default=status_options,
            format_func=lambda value: value.replace("_", " ").title(),
        )
    with ctrl2:
        selected_types = st.multiselect(
            _txt("Task Types", "任务类型"),
            options=type_options,
            default=type_options,
            format_func=lambda value: value.replace("_", " ").title(),
        )
    with ctrl3:
        limit = st.selectbox(_txt("Rows", "条数"), options=[25, 50, 100, 200], index=1)
    with ctrl4:
        search_text = st.text_input(_txt("Search", "搜索"), placeholder=_txt("Task name / UUID", "任务名 / UUID"))

    auto1, auto2, auto3 = st.columns([1, 1, 1])
    with auto1:
        auto_refresh = st.checkbox(_txt("Auto refresh", "自动刷新"), value=True)
    with auto2:
        interval_seconds = st.selectbox(_txt("Interval", "间隔"), options=[3, 5, 10, 15, 30], index=1)
    with auto3:
        refresh_when_idle = st.checkbox(_txt("Refresh when idle", "空闲时也刷新"), value=False)

    if st.button(_txt("Refresh now", "立即刷新"), width="content"):
        st.rerun()

    return {
        "statuses": selected_statuses,
        "task_types": selected_types,
        "limit": limit,
        "search_text": search_text,
        "auto_refresh": auto_refresh,
        "interval_seconds": interval_seconds,
        "refresh_when_idle": refresh_when_idle,
    }


def _render_metrics(tasks: List[Dict[str, Any]], stats: Dict[str, Any]) -> int:
    active_count = len([task for task in tasks if task["status"] in {s.value for s in ACTIVE_TASK_STATUSES}])
    failed_count = len([task for task in tasks if task["status"] == TaskStatus.FAILED.value])
    completed_count = len([task for task in tasks if task["status"] == TaskStatus.COMPLETED.value])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(_txt("Filtered Tasks", "筛选后任务数"), len(tasks))
    col2.metric(_txt("Active", "活跃任务"), active_count)
    col3.metric(_txt("Completed", "已完成"), completed_count)
    col4.metric(_txt("Failed", "失败"), failed_count)

    with st.expander(_txt("Global distribution", "全局分布"), expanded=False):
        chart1, chart2 = st.columns(2)
        with chart1:
            status_counts = stats.get("by_status", {})
            if status_counts:
                st.bar_chart(pd.Series(status_counts))
        with chart2:
            type_counts = stats.get("by_type", {})
            if type_counts:
                st.bar_chart(pd.Series(type_counts))

    return active_count


def _render_active_tasks(tasks: List[Dict[str, Any]]) -> None:
    active_tasks = [task for task in tasks if task["status"] in {s.value for s in ACTIVE_TASK_STATUSES}]
    if not active_tasks:
        return

    st.subheader(_txt("Live tasks", "实时任务"))
    for task in active_tasks[:8]:
        with st.container(border=True):
            meta1, meta2 = st.columns([3, 1])
            with meta1:
                st.markdown(
                    f"{_status_badge(task['status'])} **{task['task_name']}**  "+
                    f"{_enum_label(task['task_type'])} · {task['task_uuid'][:8]}"
                )
                st.caption(
                    f"{_txt('Priority', '优先级')}: {_enum_label(task['priority'])} | "
                    f"{_txt('Duration', '耗时')}: {_format_duration(task)}"
                )
            with meta2:
                st.metric(_txt("Progress", "进度"), f"{task['progress']}%")
            st.progress(max(0, min(task["progress"], 100)) / 100)
            if task.get("last_error"):
                st.error(task["last_error"])


def _render_task_table(tasks: List[Dict[str, Any]]) -> Optional[str]:
    st.subheader(_txt("Task list", "任务列表"))
    if not tasks:
        st.info(_txt("No tasks match the current filters.", "当前筛选条件下没有任务。"))
        return None

    table_df = pd.DataFrame(
        [
            {
                _txt("Task", "任务"): task["task_name"],
                "UUID": task["task_uuid"][:8],
                _txt("Type", "类型"): _enum_label(task["task_type"]),
                _txt("Status", "状态"): f"{_status_badge(task['status'])} {_enum_label(task['status'])}",
                _txt("Priority", "优先级"): _enum_label(task["priority"]),
                _txt("Progress", "进度"): f"{task['progress']}%",
                _txt("Country", "国家"): task.get("country_code") or "-",
                _txt("Report", "报告"): task.get("report_id") or "-",
                _txt("Logs", "日志数"): task.get("workbook_count", 0),
                _txt("Created", "创建时间"): _format_timestamp(task.get("created_at")),
            }
            for task in tasks
        ]
    )
    st.dataframe(table_df, width="stretch", hide_index=True)

    task_uuids = [task["task_uuid"] for task in tasks]
    selected_uuid = st.session_state.get("task_monitor_selected_uuid")
    if selected_uuid not in task_uuids:
        selected_uuid = _default_task_uuid(tasks)

    selected_uuid = st.selectbox(
        _txt("Inspect task", "查看任务"),
        options=task_uuids,
        index=task_uuids.index(selected_uuid) if selected_uuid in task_uuids else 0,
        format_func=lambda value: next(
            f"{task['task_name']} | {value[:8]} | {_enum_label(task['status'])} | {task['progress']}%"
            for task in tasks
            if task["task_uuid"] == value
        ),
        key="task_monitor_selected_uuid",
    )
    return selected_uuid


def _render_detail(task_uuid: Optional[str]) -> None:
    if not task_uuid:
        return

    detail = run_async(_fetch_task_detail(task_uuid))
    if not detail:
        st.warning(_txt("Task details are unavailable.", "任务详情暂不可用。"))
        return

    task = detail["task"]
    st.subheader(_txt("Task detail", "任务详情"))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(_txt("Status", "状态"), _enum_label(task["status"]))
    col2.metric(_txt("Type", "类型"), _enum_label(task["task_type"]))
    col3.metric(_txt("Priority", "优先级"), _enum_label(task["priority"]))
    col4.metric(_txt("Progress", "进度"), f"{task['progress']}%")

    if task.get("description"):
        st.caption(task["description"])
    if task.get("last_error"):
        st.error(task["last_error"])

    tabs = st.tabs([
        _txt("Overview", "概览"),
        _txt("Logs", "日志"),
        _txt("Payload", "输入输出"),
        _txt("Report", "关联报告"),
    ])

    with tabs[0]:
        overview_df = pd.DataFrame(
            [
                {"field": _txt("Created", "创建时间"), "value": _format_timestamp(task.get("created_at"))},
                {"field": _txt("Started", "开始时间"), "value": _format_timestamp(task.get("started_at"))},
                {"field": _txt("Completed", "完成时间"), "value": _format_timestamp(task.get("completed_at"))},
                {"field": _txt("Duration", "耗时"), "value": _format_duration(task)},
                {"field": "UUID", "value": task["task_uuid"]},
            ]
        )
        st.dataframe(overview_df, width="stretch", hide_index=True)

    with tabs[1]:
        workbook = detail["workbook"]
        if not workbook:
            st.info(_txt("No workbook entries yet.", "还没有执行日志。"))
        else:
            log_df = pd.DataFrame(
                [
                    {
                        _txt("Time", "时间"): _format_timestamp(entry["created_at"]),
                        _txt("Type", "类型"): entry["entry_type"],
                        _txt("Title", "标题"): entry["title"],
                        _txt("Model", "模型"): entry["model_used"] or "-",
                        _txt("Tokens", "Tokens"): entry["tokens_used"] or 0,
                        _txt("Duration", "耗时"): entry["duration"] or "-",
                        _txt("Content", "内容"): entry["content"],
                    }
                    for entry in workbook
                ]
            )
            st.dataframe(log_df, width="stretch", hide_index=True, height=360)

    with tabs[2]:
        io1, io2 = st.columns(2)
        with io1:
            st.markdown(f"**{_txt('Input data', '输入数据')}**")
            st.json(task.get("input_data") or {})
        with io2:
            st.markdown(f"**{_txt('Output data', '输出数据')}**")
            st.json(task.get("output_data") or {})

    with tabs[3]:
        report = detail.get("report")
        if not report:
            st.info(_txt("No linked report.", "没有关联报告。"))
        else:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric(_txt("Report ID", "报告 ID"), report["id"])
            r2.metric(_txt("Status", "状态"), _enum_label(report["status"]))
            r3.metric(_txt("Sections", "章节数"), report["section_count"])
            quality = report.get("quality_score")
            r4.metric(_txt("Quality", "质量分"), f"{quality:.2f}" if quality is not None else "-")
            st.caption(report["title"])
            if report.get("run_counts"):
                st.bar_chart(pd.Series(report["run_counts"]))
            file_df = pd.DataFrame(
                [
                    {"file": "markdown", "path": report.get("markdown_path") or "-"},
                    {"file": "html", "path": report.get("html_path") or "-"},
                    {"file": "pdf", "path": report.get("pdf_path") or "-"},
                ]
            )
            st.dataframe(file_df, width="stretch", hide_index=True)
            st.markdown("---")
            _render_report_trace(
                task_uuid=task["task_uuid"],
                report_id=report["id"],
                outcome_data=task.get("output_data") or {},
            )


def render_task_monitor(t, sel_country_id: Optional[int] = None, standalone: bool = False) -> None:
    """Render the lightweight realtime task monitor."""
    del t, sel_country_id

    _render_header(standalone)
    controls = _render_controls()
    payload = run_async(
        _fetch_monitor_payload(
            statuses=controls["statuses"],
            task_types=controls["task_types"],
            limit=controls["limit"],
            search_text=controls["search_text"],
        )
    )
    tasks = payload["tasks"]
    active_count = _render_metrics(tasks, payload["stats"])
    _render_active_tasks(tasks)
    selected_uuid = _render_task_table(tasks)
    _render_detail(selected_uuid)

    should_refresh = controls["auto_refresh"] and (
        active_count > 0 or controls["refresh_when_idle"]
    )
    if should_refresh:
        st.caption(
            _txt(
                f"Auto-refreshing every {controls['interval_seconds']} seconds.",
                f"每 {controls['interval_seconds']} 秒自动刷新一次。",
            )
        )
        time.sleep(controls["interval_seconds"])
        st.rerun()