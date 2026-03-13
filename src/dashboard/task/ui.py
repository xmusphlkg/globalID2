"""Enhanced task management UI with advanced features."""
import streamlit as st
import pandas as pd
import json
import os
from typing import List, Optional
from datetime import datetime, timezone

from src.core.task_manager import task_manager
from src.domain import (
    TaskStatus,
    TaskType,
    TaskPriority,
    Task,
    Report,
    ReportSection,
    ReportSectionRun,
    ReportSectionRunStatus,
    AIConversation,
)
from .async_helper import run_async
from .ai_details import render_ai_conversation, render_quality_scores, render_section_details_dialog

# Categories storage path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
CATEGORIES_PATH = os.path.join(ROOT, "data", "task_categories.json")


def _load_categories() -> List[str]:
    """Load task categories from JSON file."""
    try:
        if os.path.exists(CATEGORIES_PATH):
            with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return ["ai", "crawler", "data_processing"]


def _save_categories(categories: List[str]):
    """Save task categories to JSON file."""
    os.makedirs(os.path.dirname(CATEGORIES_PATH), exist_ok=True)
    with open(CATEGORIES_PATH, "w", encoding="utf-8") as f:
        json.dump(categories, f, ensure_ascii=False, indent=2)


def _render_task_table_with_actions(t, tasks: list, show_actions: bool = True, key_prefix: str = ""):
    """Render task table with expandable details in each row.
    
    Args:
        t: Translation function
        tasks: List of task objects
        show_actions: Whether to show action buttons (deprecated)
        key_prefix: Prefix for Streamlit keys to avoid conflicts
    """
    if not tasks:
        st.info(t("no_tasks"))
        return
    
    # Display tasks in expandable containers
    for task in tasks:
        # Create status badge
        status_map = {
            "pending": "🟡",
            "running": "🔵",
            "completed": "🟢",
            "failed": "🔴",
            "cancelled": "⚫"
        }
        status_str = str(task.status).replace("TaskStatus.", "").lower()
        status_badge = status_map.get(status_str, "⚪")
        
        # Calculate duration
        duration = "N/A"
        if task.actual_duration:
            duration = f"{task.actual_duration}s"
        elif task.started_at and not task.completed_at:
            # Use UTC-aware now to avoid naive/aware subtraction issues
            now_utc = datetime.now(timezone.utc)
            started = task.started_at
            # Ensure both datetimes are timezone-aware
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            duration_seconds = max(0, int((now_utc - started).total_seconds()))
            duration = f"{duration_seconds}s (running)"
        
        # Create expander with key info in title
        with st.expander(
            f"{status_badge} **{task.task_name}** | {str(task.task_type).replace('TaskType.', '')} | {task.progress}%",
            expanded=False
        ):
            # Basic info in columns
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.markdown("**UUID**")
                st.code(task.task_uuid, language=None)
                st.markdown("**Status**")
                st.text(str(task.status).replace("TaskStatus.", ""))
            
            with col2:
                st.markdown("**Priority**")
                st.text(str(task.priority).replace("TaskPriority.", ""))
                st.markdown("**Progress**")
                st.text(f"{task.progress}%")
            
            with col3:
                st.markdown("**Created**")
                st.text(task.created_at.strftime("%Y-%m-%d %H:%M:%S") if task.created_at else "N/A")
                st.markdown("**Started**")
                st.text(task.started_at.strftime("%Y-%m-%d %H:%M:%S") if task.started_at else "N/A")
            
            with col4:
                st.markdown("**Completed**")
                st.text(task.completed_at.strftime("%Y-%m-%d %H:%M:%S") if task.completed_at else "N/A")
                st.markdown("**Duration**")
                st.text(duration)
            
            # Description
            if task.description:
                st.markdown("**Description**")
                st.info(task.description)
            
            # Input/Output Data
            if task.input_data or task.output_data:
                data_col1, data_col2 = st.columns(2)
                
                with data_col1:
                    if task.input_data:
                        st.markdown("**Input Data**")
                        st.json(task.input_data, expanded=False)
                
                with data_col2:
                    if task.output_data:
                        st.markdown("**Output Data**")
                        st.json(task.output_data, expanded=False)
            
            # Always check for AI disease buttons if this is a GENERATE_REPORT task
            if str(task.task_type) == "TaskType.GENERATE_REPORT":
                report_id = None
                
                # Try to get report_id from output_data first
                if task.output_data and task.output_data.get("report_id"):
                    report_id = task.output_data["report_id"]
                
                # If no output_data yet, try to find the latest report for this task
                if not report_id:
                    try:
                        from sqlalchemy import select, desc
                        from src.core.database import get_db
                        
                        async def find_report_for_task():
                            async with get_db() as db:
                                # Get country from input_data
                                country_code = task.input_data.get("country") if task.input_data else None
                                if not country_code:
                                    return None
                                
                                # Find the latest report for this country created around this task's time
                                from src.domain import Country
                                country_query = select(Country).where(Country.code == country_code)
                                country_result = await db.execute(country_query)
                                country = country_result.scalar_one_or_none()
                                
                                if country:
                                    # Find latest report for this country
                                    report_query = (
                                        select(Report)
                                        .where(Report.country_id == country.id)
                                        .order_by(desc(Report.created_at))
                                        .limit(1)
                                    )
                                    report_result = await db.execute(report_query)
                                    report = report_result.scalar_one_or_none()
                                    return report.id if report else None
                                return None
                        
                        report_id = run_async(find_report_for_task())
                    except Exception as e:
                        pass  # Silently continue if lookup fails
                
                # Show disease buttons if we have a report_id
                if report_id:
                    st.markdown("**📊 AI Analysis for Each Disease**")
                    
                    # Fetch report sections
                    try:
                        from sqlalchemy import select
                        from src.core.database import get_db
                        
                        async def get_report_sections(rid):
                            async with get_db() as db:
                                query = select(ReportSection).where(
                                    ReportSection.report_id == rid
                                ).order_by(ReportSection.section_order)
                                result = await db.execute(query)
                                return result.scalars().all()
                        
                        sections = run_async(get_report_sections(report_id))
                        
                        if sections:
                            # Display diseases as columns of buttons
                            cols_per_row = 3
                            for i in range(0, len(sections), cols_per_row):
                                cols = st.columns(cols_per_row)
                                for j, section in enumerate(sections[i:i+cols_per_row]):
                                    with cols[j]:
                                        # Extract disease name from title (format: "Disease Name - section_type")
                                        disease_name = section.title.split(" - ")[0] if " - " in section.title else section.title
                                        
                                        if st.button(f"🤖 {disease_name}", key=f"{key_prefix}disease_{section.id}", use_container_width=True):
                                            st.session_state["show_ai_details"] = True
                                            st.session_state["selected_report_id"] = report_id
                                            st.session_state["selected_section_id"] = section.id
                                            st.rerun()
                        else:
                            st.info("No diseases analyzed yet...")
                    
                        # Link to global Report Monitor for this report
                        if st.button("🔍 Open in Report Monitor", key=f"{key_prefix}monitor_{task.task_uuid}"):
                            st.session_state["report_monitor_selected_report_id"] = report_id
                            # Switch nav to Report Monitor if available
                            st.session_state["nav_radio"] = "Report Monitor"
                            st.rerun()
                    except Exception as e:
                        st.warning(f"Could not load disease sections: {str(e)[:50]}")
            
            # Last Error
            if task.last_error:
                st.markdown("**Last Error**")
                st.error(task.last_error)
            
            # Workbook Logs
            st.markdown("**📔 Execution Log**")
            try:
                workbook = run_async(task_manager.get_task_workbook(task.task_uuid))
                if workbook and len(workbook) > 0:
                    # Build log text
                    log_lines = []
                    for entry in workbook:
                        entry_time = entry.created_at.strftime("%H:%M:%S")
                        entry_icon = {"info": "ℹ️", "success": "✅", "error": "❌", "warning": "⚠️"}.get(entry.entry_type, "📝")
                        log_lines.append(f"{entry_time} {entry_icon} {entry.title}")
                        if entry.content:
                            # Indent content
                            for line in entry.content.split('\n'):
                                log_lines.append(f"  {line}")
                        log_lines.append("")  # Empty line separator
                    
                    # Display in scrollable text area
                    log_text = "\n".join(log_lines)
                    st.text_area(
                        "Log Details",
                        value=log_text,
                        height=200,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"{key_prefix}log_{task.task_uuid}"
                    )
                else:
                    st.info("No execution logs yet")
            except Exception as e:
                st.warning(f"Could not load logs: {str(e)[:100]}")


def _render_task_detail(t, task_uuid: str):
    """Render detailed task view with edit capabilities.
    
    Args:
        t: Translation function
        task_uuid: Task UUID to display
    """
    try:
        task = run_async(task_manager.get_task_by_uuid(task_uuid))
        if not task:
            st.error(t("task_not_found"))
            return
        
        # Header with close button
        col1, col2 = st.columns([4, 1])
        with col1:
            st.header(f"📋 {task.task_name}")
        with col2:
            if st.button("✖️ " + t("close"), key="close_detail"):
                st.session_state["show_task_detail"] = False
                st.rerun()
        
        # Task info
        st.markdown(f"**UUID:** `{task.task_uuid}`")
        st.markdown(f"**Status:** {task.status} | **Priority:** {task.priority} | **Progress:** {task.progress}%")
        
        # Tabs for different sections
        tabs = st.tabs([t("basic_info"), t("input_data"), t("workbook"), t("actions")])
        
        # Basic Info tab
        with tabs[0]:
            col1, col2 = st.columns(2)
            with col1:
                st.metric(t("task_type"), str(task.task_type))
                st.metric(t("created_at"), task.created_at.strftime("%Y-%m-%d %H:%M") if task.created_at else "N/A")
                st.metric(t("started_at"), task.started_at.strftime("%Y-%m-%d %H:%M") if task.started_at else "N/A")
            with col2:
                st.metric(t("priority"), str(task.priority))
                st.metric(t("completed_at"), task.completed_at.strftime("%Y-%m-%d %H:%M") if task.completed_at else "N/A")
                st.metric(t("duration"), f"{task.actual_duration}s" if task.actual_duration else "N/A")
            
            if task.description:
                st.text_area(t("description"), value=task.description, disabled=True, height=100)
            
            if task.last_error:
                st.error(f"**{t('last_error')}:** {task.last_error}")
        
        # Input Data tab
        with tabs[1]:
            st.subheader(t("input_data"))
            if task.input_data:
                # Display current input data
                st.json(task.input_data)
                
                # Edit form
                with st.expander(t("edit_input_data"), expanded=False):
                    new_input = st.text_area(
                        t("json_input"),
                        value=json.dumps(task.input_data, indent=2, ensure_ascii=False),
                        height=200
                    )
                    if st.button(t("update_input")):
                        try:
                            parsed_input = json.loads(new_input)
                            task.input_data = parsed_input
                            # TODO: Update in database
                            st.success(t("task_action_success"))
                        except json.JSONDecodeError as e:
                            st.error(f"Invalid JSON: {e}")
            else:
                st.info(t("no_input_data"))
        
        # Workbook tab
        with tabs[2]:
            st.subheader(t("workbook"))
            workbook = run_async(task_manager.get_task_workbook(task_uuid))
            
            if workbook:
                for idx, entry in enumerate(workbook):
                    with st.expander(f"{idx+1}. {entry.title} ({entry.entry_type})", expanded=False):
                        st.markdown(f"**Type:** {entry.entry_type}")
                        st.markdown(f"**Created:** {entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
                        
                        if entry.content:
                            st.text_area("Content", value=entry.content, height=150, disabled=True, key=f"wb_content_{idx}")
                        
                        if entry.prompt:
                            st.text_area("Prompt", value=entry.prompt, height=100, disabled=True, key=f"wb_prompt_{idx}")
                        
                        if entry.response:
                            st.text_area("Response", value=entry.response, height=100, disabled=True, key=f"wb_response_{idx}")
                        
                        if entry.model_used:
                            cols = st.columns(3)
                            cols[0].metric("Model", entry.model_used)
                            cols[1].metric("Tokens", entry.tokens_used or 0)
                            cols[2].metric("Cost", f"${entry.cost:.4f}" if entry.cost else "$0")
            else:
                st.info(t("no_workbook_entries"))
        
        # Actions tab
        with tabs[3]:
            st.subheader(t("task_actions"))
            
            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("▶️ " + t("start_task"), key="detail_start", type="primary"):
                    try:
                        run_async(task_manager.update_task_status(task_uuid, TaskStatus.RUNNING))
                        st.success(t("task_action_success"))
                        st.rerun()
                    except Exception as e:
                        st.error(f"{t('task_action_failed')}: {e}")
            
            with col2:
                if st.button("✅ " + t("complete_task"), key="detail_complete"):
                    try:
                        run_async(task_manager.update_task_status(task_uuid, TaskStatus.COMPLETED))
                        st.success(t("task_action_success"))
                        st.rerun()
                    except Exception as e:
                        st.error(f"{t('task_action_failed')}: {e}")
            
            with col3:
                if st.button("❌ " + t("cancel_task"), key="detail_cancel"):
                    try:
                        run_async(task_manager.update_task_status(task_uuid, TaskStatus.CANCELLED))
                        st.success(t("task_action_success"))
                        st.rerun()
                    except Exception as e:
                        st.error(f"{t('task_action_failed')}: {e}")
    
    except Exception as e:
        st.error(f"{t('connection_failed')}: {e}")


def _render_ai_report_details(t, report_id: int):
    """
    Render AI report generation details with section-by-section breakdown.
    
    Args:
        t: Translation function  
        report_id: Report ID to display
    """
    from sqlalchemy import select, desc
    from src.core.database import get_db
    
    async def get_report_with_sections(rid):
        async with get_db() as db:
            # Get report
            report = await db.get(Report, rid)
            if not report:
                return None, [], [], {}
            
            # Get sections
            query = select(ReportSection).where(ReportSection.report_id == rid).order_by(ReportSection.section_order)
            result = await db.execute(query)
            sections = result.scalars().all()

            # Get latest runs for this report
            runs_query = (
                select(ReportSectionRun)
                .where(ReportSectionRun.report_id == rid)
                .order_by(desc(ReportSectionRun.created_at))
            )
            runs = (await db.execute(runs_query)).scalars().all()

            run_ids = [r.id for r in runs]
            conv_map = {}
            if run_ids:
                conv_query = (
                    select(AIConversation)
                    .where(AIConversation.run_id.in_(run_ids))
                    .order_by(AIConversation.timestamp)
                )
                convs = (await db.execute(conv_query)).scalars().all()
                for conv in convs:
                    conv_map.setdefault(conv.run_id, []).append(conv)
            
            return report, sections, runs, conv_map
    
    report, sections, runs, conv_map = run_async(get_report_with_sections(report_id))
    
    if not report:
        st.error("Report not found")
        return
    
    # Header with close button
    col1, col2 = st.columns([4, 1])
    with col1:
        st.header(f"📊 AI Report Details: {report.title}")
    with col2:
        if st.button("✖️ Close", key="close_ai_details"):
            st.session_state["show_ai_details"] = False
            st.session_state.pop("selected_section_id", None)
            st.rerun()
    
    # Report summary
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Status", str(report.status).replace("ReportStatus.", ""))
    col2.metric("Sections", len(sections))
    col3.metric("Quality Score", f"{report.quality_score:.1%}" if report.quality_score else "N/A")
    col4.metric("Generation Time", f"{report.generation_time:.1f}s" if report.generation_time else "N/A")
    
    # Token usage summary
    if report.token_usage:
        st.markdown("### 🎯 Token Usage Summary")
        st.json(report.token_usage)
    
    st.markdown("---")
    st.markdown("### 📑 Diseases & AI Generation Details")
    
    # Check if specific section was selected
    selected_section_id = st.session_state.get("selected_section_id", None)

    # Prepare logic for displaying items (both completed sections and queued runs)
    # 1. Group latest run by key (disease_name, section_type)
    latest_run_by_key = {}
    for run in runs:
        # runs are ordered by created_at desc, so first one is latest
        key = (run.disease_name, run.section_type)
        if key not in latest_run_by_key:
            latest_run_by_key[key] = run

    # 2. Build display list
    display_items = []
    processed_keys = set()

    # Add existing sections first
    for section in sections:
        # Extract disease name and type from title if needed, or use section type
        # Ideally we use the run associated with this section
        run = None
        # Try to find run linked by section_id
        for r in runs:
            if r.section_id == section.id:
                run = r
                break
        
        # If not found by ID, try by key text matching from title
        # Title format usually: "{Disease} - {Type}"
        if not run:
            parts = section.title.split(" - ")
            if len(parts) >= 2:
                disease_name = parts[0]
                sec_type = parts[1]
                run = latest_run_by_key.get((disease_name, sec_type))
        
        display_items.append({
            "type": "section",
            "obj": section,
            "run": run,
            "sort_key": section.section_order
        })
        
        if run:
            processed_keys.add((run.disease_name, run.section_type))
        # Also mark key if derived from title
        parts = section.title.split(" - ")
        if len(parts) >= 2:
            processed_keys.add((parts[0], parts[1]))

    # Add pending/queued items
    pending_items = []
    for key, run in latest_run_by_key.items():
        if key not in processed_keys:
            # Check if this run is linked to ANY section (maybe we missed it)
            if run.section_id:
                continue # Already handled via section loop (theoretically)
            
            pending_items.append({
                "type": "run",
                "obj": run,
                "run": run,
                "sort_key": 9999 + (run.id or 0) # Append at end
            })
    
    # Sort pending items by disease name
    pending_items.sort(key=lambda x: (x['run'].disease_name or "", x['run'].section_type or ""))

    all_items = display_items + pending_items

    def serialize_conversations(run_id: int):
        entries = conv_map.get(run_id, [])
        serialized = []
        for conv in entries:
            serialized.append({
                "agent": conv.agent,
                "role": conv.role,
                "timestamp": conv.timestamp.isoformat() if conv.timestamp else "",
                "prompt": conv.prompt,
                "system_prompt": conv.system_prompt,
                "response": conv.response,
                "model": conv.model,
                "provider": conv.provider,
                "tokens": conv.tokens or {},
                "duration": conv.duration,
                "temperature": conv.temperature,
            })
        return serialized
    
    # Display each item
    for idx, item in enumerate(all_items, 1):
        is_section = (item["type"] == "section")
        obj = item["obj"]
        run = item["run"]
        
        if is_section:
            section = obj
            title = section.title
            status = getattr(run, "status", "COMPLETED") if run else "COMPLETED"
            token_total = (run.token_usage or {}).get("total", 0) if run else 0
            model_used = getattr(run, "model", None) or section.ai_model or "Unknown"
            is_verified = section.is_verified
            gen_time = section.generation_time
            content = section.content
            data_sources = section.data_sources
            section_id = section.id
        else:
            # It's a run (queued/running)
            run = obj
            title = f"{run.disease_name or 'Unknown'} - {run.section_type}"
            status = run.status
            token_total = 0
            model_used = "Pending..."
            is_verified = False
            gen_time = None
            content = None
            data_sources = []
            section_id = None
            
        status_str = str(status).replace("ReportSectionRunStatus.", "").replace("ReportStatus.", "")
        
        # Status icon
        status_icon = "⚪"
        if "RUNNING" in status_str:
            status_icon = "🔵"
        elif "QUEUED" in status_str:
            status_icon = "🟡"
        elif "COMPLETED" in status_str:
            status_icon = "🟢"
        elif "FAILED" in status_str:
            status_icon = "🔴"

        # Auto-expand if this section was selected
        is_expanded = (section_id and selected_section_id and section_id == selected_section_id)
        
        with st.expander(
            f"{status_icon} **{idx}. {title}** | {status_str} | {model_used}",
            expanded=is_expanded
        ):
            # Section metadata
            col1, col2, col3 = st.columns(3)
            col1.metric("Type", run.section_type if run else "N/A")
            col2.metric("Time", f"{gen_time:.1f}s" if gen_time else "N/A")
            col3.metric("Verified", "✅ Yes" if is_verified else "❌ No")
            
            # Tabs for section details
            section_tabs = st.tabs(["🤖 AI Conversation", "📄 Content", "⭐ Quality", "📊 Data"])
            
            # AI Conversation tab
            with section_tabs[0]:
                if run:
                    ai_conversation = serialize_conversations(run.id)
                    if ai_conversation:
                        render_ai_conversation(ai_conversation, title)
                    else:
                        st.info("No AI conversation history recorded yet")
                else:
                    st.info("No run data available")
            
            # Content tab
            with section_tabs[1]:
                st.markdown("#### Generated Content")
                if content:
                    st.markdown(content)
                else:
                    st.warning("Content not generated yet")
            
            # Quality tab
            with section_tabs[2]:
                quality_scores = run.quality_scores if run else {}
                if quality_scores:
                    render_quality_scores(quality_scores)
                else:
                    st.info("No quality scores available")
            
            # Data tab
            with section_tabs[3]:
                if data_sources:
                    st.markdown("#### Data Sources")
                    for i, source in enumerate(data_sources, 1):
                        st.json(source)
                else:
                    st.info("No data sources recorded")


def _render_queue_view(t):
    """Render task queue overview with status distribution."""
    st.subheader(t("task_queue"))
    
    try:
        # Get tasks by status
        pending = run_async(task_manager.get_pending_tasks(limit=100))
        running = run_async(task_manager.get_running_tasks())
        
        # Display counts
        col1, col2, col3 = st.columns(3)
        col1.metric("⏳ " + t("pending_tasks"), len(pending))
        col2.metric("▶️ " + t("running_tasks"), len(running))
        col3.metric("📊 " + t("total_in_queue"), len(pending) + len(running))
        
        # Queue visualization
        st.markdown("---")
        
        # Pending queue
        st.markdown(f"### ⏳ {t('pending_tasks')} ({len(pending)})")
        if pending:
            _render_task_table_with_actions(t, pending, show_actions=True, key_prefix="queue_pending_")
        else:
            st.info(t("no_pending_tasks"))
        
        st.markdown("---")
        
        # Running queue
        st.markdown(f"### ▶️ {t('running_tasks')} ({len(running)})")
        if running:
            _render_task_table_with_actions(t, running, show_actions=True, key_prefix="queue_running_")
        else:
            st.info(t("no_running_tasks"))
    
    except Exception as e:
        st.error(f"{t('connection_failed')}: {e}")


def render_task_center(t, sel_country_id: Optional[int]):
    """Main task center rendering function.
    
    Args:
        t: Translation function
        sel_country_id: Selected country ID (optional)
    """
    # Check if showing AI report details
    if st.session_state.get("show_ai_details") and st.session_state.get("selected_report_id"):
        _render_ai_report_details(t, st.session_state["selected_report_id"])
        return
    
    # Check if showing task detail
    if st.session_state.get("show_task_detail") and st.session_state.get("selected_task_uuid"):
        _render_task_detail(t, st.session_state["selected_task_uuid"])
        return
    
    st.header(t("task_management_title"))
    
    # Main tabs
    tabs = st.tabs([
        t("overview"), 
        t("queue_view"),
        t("ai_tasks"), 
        t("crawler_tasks"), 
        t("categories")
    ])
    
    # Overview tab
    with tabs[0]:
        st.subheader(t("overview"))
        
        try:
            # Use async wrapper to avoid event loop issues
            stats = run_async(task_manager.get_task_statistics())
            if stats is None:
                stats = {"total": 0, "by_status": {}, "by_type": {}}
        except Exception as e:
            st.error(f"Database connection failed: {str(e)}")
            stats = {"total": 0, "by_status": {}, "by_type": {}}
        
        # Metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(t("total_tasks"), stats.get('total', 0))
        col2.metric(t("pending_tasks"), stats.get('by_status', {}).get('pending', 0))
        col3.metric(t("running_tasks"), stats.get('by_status', {}).get('running', 0))
        col4.metric(t("completed_tasks"), stats.get('by_status', {}).get('completed', 0))
        
        # Charts
        col1, col2 = st.columns(2)
        with col1:
            st.write("**" + t("by_status") + ":**")
            if stats.get("by_status"):
                st.bar_chart(stats["by_status"])
            else:
                st.info(t("no_data"))
        
        with col2:
            st.write("**" + t("by_type") + ":**")
            if stats.get("by_type"):
                st.bar_chart(stats["by_type"])
            else:
                st.info(t("no_data"))
    
    # Queue View tab
    with tabs[1]:
        _render_queue_view(t)
    
    # AI Tasks tab
    with tabs[2]:
        st.subheader(t("ai_tasks"))
        try:
            # 获取所有AI任务（不只是pending）
            from src.domain import TaskType
            from sqlalchemy import select, or_, desc
            from src.core.database import get_db
            
            async def get_all_ai_tasks():
                async with get_db() as db:
                    query = (
                        select(Task)
                        .where(
                            or_(
                                Task.task_type == TaskType.GENERATE_REPORT,
                                Task.task_type == TaskType.GENERATE_SECTION,
                                Task.task_type == TaskType.REVIEW_SECTION,
                            )
                        )
                        .order_by(desc(Task.created_at))
                        .limit(100)
                    )
                    result = await db.execute(query)
                    return result.scalars().all()
            
            ai_tasks = run_async(get_all_ai_tasks())
            
            if ai_tasks:
                st.info(f"📊 Total {len(ai_tasks)} AI task(s)")
                _render_task_table_with_actions(t, ai_tasks, key_prefix="ai_")
            else:
                st.info("No AI tasks")
        except Exception as e:
            st.error(f"{t('connection_failed')}: {e}")
    
    # Crawler Tasks tab
    with tabs[3]:
        st.subheader(t("crawler_tasks"))
        try:
            # 获取所有爬虫任务（不只是pending）
            from src.domain import TaskType
            from sqlalchemy import select, or_, desc
            from src.core.database import get_db
            
            async def get_all_crawl_tasks():
                async with get_db() as db:
                    query = (
                        select(Task)
                        .where(Task.task_type == TaskType.CRAWL_DATA)
                        .order_by(desc(Task.created_at))
                        .limit(100)
                    )
                    result = await db.execute(query)
                    return result.scalars().all()
            
            crawlers = run_async(get_all_crawl_tasks())
            
            if crawlers:
                st.info(f"📊 Total {len(crawlers)} crawl task(s)")
                _render_task_table_with_actions(t, crawlers, key_prefix="crawler_")
            else:
                st.info("No crawl tasks")
        except Exception as e:
            st.error(f"{t('connection_failed')}: {e}")
    
    # Categories tab
    with tabs[4]:
        st.subheader(t("categories"))
        categories = _load_categories()
        
        st.write("**" + t("current_categories") + ":**")
        st.write(categories)
        
        st.markdown("---")
        col1, col2 = st.columns(2)
        
        with col1:
            with st.form(key="add_category"):
                new_cat = st.text_input(t("create_category"))
                if st.form_submit_button(t("create")):
                    if new_cat and new_cat not in categories:
                        categories.append(new_cat)
                        _save_categories(categories)
                        st.success(f"{t('category_created')}: {new_cat}")
                        st.rerun()
                    else:
                        st.warning(t("category_exists") if new_cat in categories else t("enter_category_name"))
        
        with col2:
            to_remove = st.multiselect(t("remove_category"), categories)
            if st.button(t("delete"), key="delete_categories"):
                if to_remove:
                    categories = [c for c in categories if c not in to_remove]
                    _save_categories(categories)
                    st.success(t("category_deleted"))
                    st.rerun()
                else:
                    st.warning(t("select_category_to_remove"))
