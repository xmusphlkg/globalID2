"""Enhanced task management UI with advanced features."""
import streamlit as st
import pandas as pd
import json
import os
from typing import List, Optional
from datetime import datetime

from src.core.task_manager import task_manager
from src.domain import TaskStatus, TaskType, TaskPriority, Task
from .async_helper import run_async

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


def _render_task_table_with_actions(t, tasks: list, show_actions: bool = True):
    """Render task table with expandable details in each row.
    
    Args:
        t: Translation function
        tasks: List of task objects
        show_actions: Whether to show action buttons (deprecated)
    """
    if not tasks:
        st.info(t("no_tasks"))
        return
    
    # Display tasks in expandable containers
    for i, task in enumerate(tasks):
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
            duration = f"{int((datetime.now() - task.started_at).total_seconds())}s (running)"
        
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
            
            # Last Error
            if task.last_error:
                st.markdown("**Last Error**")
                st.error(task.last_error)
            
            # Workbook Logs
            try:
                workbook = run_async(task_manager.get_task_workbook(task.task_uuid))
                if workbook:
                    st.markdown("**📔 Execution Log**")
                    
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
                        key=f"log_{task.task_uuid}_{i}"
                    )
            except Exception as e:
                pass  # Silently skip if workbook unavailable
            
            # Quick Actions
            st.markdown("**Actions**")
            action_cols = st.columns(4)
            
            with action_cols[0]:
                if task.status != TaskStatus.RUNNING:
                    if st.button("▶️ Start", key=f"start_{task.task_uuid}_{i}", type="primary", use_container_width=True):
                        try:
                            run_async(task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING))
                            st.success("Started")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")
            
            with action_cols[1]:
                if task.status == TaskStatus.RUNNING:
                    if st.button("⏸️ Pause", key=f"pause_{task.task_uuid}_{i}", use_container_width=True):
                        try:
                            run_async(task_manager.update_task_status(task.task_uuid, TaskStatus.PENDING))
                            st.success("Paused")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")
            
            with action_cols[2]:
                if task.status not in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
                    if st.button("✅ Complete", key=f"complete_{task.task_uuid}_{i}", use_container_width=True):
                        try:
                            run_async(task_manager.update_task_status(task.task_uuid, TaskStatus.COMPLETED))
                            st.success("Completed")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")
            
            with action_cols[3]:
                if task.status not in [TaskStatus.CANCELLED]:
                    if st.button("❌ Cancel", key=f"cancel_{task.task_uuid}_{i}", use_container_width=True):
                        try:
                            run_async(task_manager.update_task_status(task.task_uuid, TaskStatus.CANCELLED))
                            st.success("Cancelled")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")


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
            _render_task_table_with_actions(t, pending, show_actions=True)
        else:
            st.info(t("no_pending_tasks"))
        
        st.markdown("---")
        
        # Running queue
        st.markdown(f"### ▶️ {t('running_tasks')} ({len(running)})")
        if running:
            _render_task_table_with_actions(t, running, show_actions=True)
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
            ai_tasks = run_async(task_manager.get_pending_tasks(limit=200))
            ai_tasks = [
                task for task in ai_tasks
                if 'generate' in str(task.task_type).lower() or 'review' in str(task.task_type).lower()
            ]
            _render_task_table_with_actions(t, ai_tasks)
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
                _render_task_table_with_actions(t, crawlers)
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
