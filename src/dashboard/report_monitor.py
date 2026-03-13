import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.domain import Report, ReportStatus, ReportSection, ReportSectionRun, AIConversation, Country
from src.dashboard.task.ai_details import render_ai_conversation, render_quality_scores


async def _fetch_reports(
    country_id: Optional[int] = None,
    status_filter: Optional[list[str]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return plain dicts to avoid detached-instance errors after session close."""
    async with get_db() as db:
        query = (
            select(Report)
            .options(selectinload(Report.country))
            .order_by(desc(Report.created_at))
            .limit(limit)
        )
        if country_id:
            query = query.where(Report.country_id == country_id)
        if status_filter:
            query = query.where(Report.status.in_(status_filter))
        result = await db.execute(query)
        reports = result.scalars().all()
        # Serialize inside session while ORM objects are still bound
        return [
            {
                "id": r.id,
                "report_uuid": r.report_uuid,
                "title": r.title,
                "country_id": r.country_id,
                "country_name": r.country.name if r.country else str(r.country_id),
                "report_type": str(r.report_type).replace("ReportType.", ""),
                "status": str(r.status).replace("ReportStatus.", ""),
                "period_start": r.period_start,
                "period_end": r.period_end,
                "quality_score": r.quality_score,
                "created_at": r.created_at,
            }
            for r in reports
        ]


async def _fetch_report_details(report_id: int):
    async with get_db() as db:
        report = await db.get(Report, report_id)
        if not report:
            return None, [], [], {}

        sections_q = (
            select(ReportSection)
            .where(ReportSection.report_id == report_id)
            .order_by(ReportSection.section_order)
        )
        sections = (await db.execute(sections_q)).scalars().all()

        runs_q = (
            select(ReportSectionRun)
            .where(ReportSectionRun.report_id == report_id)
            .order_by(desc(ReportSectionRun.created_at))
        )
        runs = (await db.execute(runs_q)).scalars().all()

        run_ids = [r.id for r in runs]
        conv_map = {}
        if run_ids:
            conv_q = (
                select(AIConversation)
                .where(AIConversation.run_id.in_(run_ids))
                .order_by(AIConversation.timestamp)
            )
            convs = (await db.execute(conv_q)).scalars().all()
            for conv in convs:
                conv_map.setdefault(conv.run_id, []).append(conv)

        return report, sections, runs, conv_map


def _serialize_conversations(conv_map, run_id: int):
    entries = conv_map.get(run_id, [])
    serialized = []
    for conv in entries:
        serialized.append(
            {
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
            }
        )
    return serialized


def render_report_monitor(t, sel_country_id: Optional[int] = None):
    """Standalone Report Monitor page for AI-generated reports."""
    from src.dashboard.task.async_helper import run_async

    st.title("📊 Report Monitor")

    # If a specific report has been selected from Task Center, focus it first
    preselected_report_id = st.session_state.get("report_monitor_selected_report_id")

    # Filters
    with st.sidebar:
        st.subheader("Filters")
        status_options = [
            ("All", None),
            ("Pending/Generating", [ReportStatus.PENDING, ReportStatus.GENERATING, ReportStatus.REVIEWING]),
            ("Approved", [ReportStatus.APPROVED]),
            ("Completed (non-approved)", [ReportStatus.COMPLETED]),
            ("Failed", [ReportStatus.FAILED]),
        ]
        status_labels = [opt[0] for opt in status_options]
        sel_status_label = st.selectbox("Status", status_labels)
        status_filter = dict(status_options)[sel_status_label]

        limit = st.slider("Max reports", min_value=10, max_value=200, value=50, step=10)

        auto_refresh = st.checkbox("Auto refresh (5s)", value=False)
        if auto_refresh:
            st.experimental_rerun()

    # Fetch reports
    reports = run_async(_fetch_reports(sel_country_id, status_filter, limit=limit))

    if not reports:
        st.info("No reports found for current filters.")
        return

    # Left: table of reports, Right: details
    col_list, col_detail = st.columns([2, 3])

    # Build a uuid -> report-dict mapping for lookup
    uuid_to_report = {r["report_uuid"]: r for r in reports}

    with col_list:
        st.subheader("Reports")
        table_data = [
            {
                "UUID": r["report_uuid"][:8] + "…",
                "Country": r["country_name"],
                "Title": r["title"],
                "Type": r["report_type"],
                "Status": r["status"],
                "Period": f"{r['period_start'].date()} → {r['period_end'].date()}",
                "Quality": f"{r['quality_score']:.2f}" if r["quality_score"] is not None else "N/A",
                "Created": r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "",
            }
            for r in reports
        ]
        df = pd.DataFrame(table_data)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Determine default selection by UUID
        report_uuids = [r["report_uuid"] for r in reports]
        preselected_uuid = None
        if preselected_report_id:
            for r in reports:
                if r["id"] == preselected_report_id:
                    preselected_uuid = r["report_uuid"]
                    break
        default_index = report_uuids.index(preselected_uuid) if preselected_uuid in report_uuids else 0

        sel_uuid = st.selectbox(
            "Select report",
            options=report_uuids,
            index=default_index,
            format_func=lambda u: f"{u[:8]}… | {uuid_to_report[u]['country_name']} | {uuid_to_report[u]['title'][:40]}",
        )
        sel_id = uuid_to_report[sel_uuid]["id"] if sel_uuid else None

    with col_detail:
        if not sel_id:
            st.info("Select a report to inspect details.")
            return

        report, sections, runs, conv_map = run_async(_fetch_report_details(sel_id))
        if not report:
            st.error("Report not found")
            return

        sel_report_meta = uuid_to_report.get(sel_uuid, {})
        country_name = sel_report_meta.get("country_name", str(report.country_id))
        st.subheader(f"📄 {report.title}")
        st.caption(f"UUID: `{sel_report_meta.get('report_uuid', '')}` · Country: **{country_name}**")

        # Summary metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Status", str(report.status).replace("ReportStatus.", ""))
        m2.metric("Sections", len(sections))
        m3.metric("Quality", f"{report.quality_score:.1%}" if report.quality_score else "N/A")
        m4.metric("Generation Time", f"{report.generation_time:.1f}s" if report.generation_time else "N/A")

        if report.token_usage:
            st.markdown("#### Token Usage")
            st.json(report.token_usage)

        st.markdown("---")
        st.markdown("### Sections & Runs")

        # Build latest run per (disease_name, section_type)
        latest_run_by_key = {}
        for run in runs:
            key = (run.disease_name, run.section_type)
            if key not in latest_run_by_key:
                latest_run_by_key[key] = run

        # Combine sections and pending runs
        items = []
        processed_keys = set()

        for section in sections:
            # Try to find run by section_id
            run = None
            for r in runs:
                if r.section_id == section.id:
                    run = r
                    break

            if not run:
                parts = section.title.split(" - ")
                if len(parts) >= 2:
                    key = (parts[0], parts[1])
                    run = latest_run_by_key.get(key)

            items.append({"type": "section", "section": section, "run": run})

            if run:
                processed_keys.add((run.disease_name, run.section_type))

        # Add pending runs
        for key, run in latest_run_by_key.items():
            if key not in processed_keys:
                items.append({"type": "run", "section": None, "run": run})

        for idx, item in enumerate(items, 1):
            section = item["section"]
            run = item["run"]

            if section:
                title = section.title
                status = getattr(run, "status", "COMPLETED") if run else "COMPLETED"
                model_used = getattr(run, "model", None) or section.ai_model or "Unknown"
                quality_scores = run.quality_scores if run else {}
                is_verified = section.is_verified
                content = section.content
                data_sources = section.data_sources
            else:
                # orphan run
                title = f"{run.disease_name or 'Unknown'} - {run.section_type}"
                status = run.status
                model_used = run.model or "Pending..."
                quality_scores = run.quality_scores or {}
                is_verified = False
                content = None
                data_sources = []

            status_str = str(status).replace("ReportSectionRunStatus.", "").replace("ReportStatus.", "")
            status_icon = "⚪"
            if "RUNNING" in status_str:
                status_icon = "🔵"
            elif "QUEUED" in status_str:
                status_icon = "🟡"
            elif "COMPLETED" in status_str:
                status_icon = "🟢"
            elif "FAILED" in status_str:
                status_icon = "🔴"

            with st.expander(f"{status_icon} {idx}. {title} | {status_str} | {model_used}", expanded=False):
                c1, c2, c3 = st.columns(3)
                c1.metric("Type", getattr(run, "section_type", "N/A") if run else "N/A")
                c2.metric("Verified", "Yes" if is_verified else "No")
                c3.metric("Model", model_used)

                tabs = st.tabs(["Conversation", "Content", "Quality", "Data"])

                with tabs[0]:
                    if run:
                        conv = _serialize_conversations(conv_map, run.id)
                        if conv:
                            render_ai_conversation(conv, title)
                        else:
                            st.info("No AI conversation history yet.")
                    else:
                        st.info("No run information available.")

                with tabs[1]:
                    if content:
                        st.markdown(content)
                    else:
                        st.info("No content generated yet.")

                with tabs[2]:
                    if quality_scores:
                        render_quality_scores(quality_scores)
                    else:
                        st.info("No quality scores available.")

                with tabs[3]:
                    if data_sources:
                        for src in data_sources:
                            st.json(src)
                    else:
                        st.info("No data source metadata recorded.")

