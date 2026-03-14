import os
import time
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
    from sqlalchemy import func
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
        report_ids = [r.id for r in reports]
        # Get section counts per report
        section_counts = {}
        if report_ids:
            count_q = (
                select(ReportSection.report_id, func.count(ReportSection.id).label("cnt"))
                .where(ReportSection.report_id.in_(report_ids))
                .group_by(ReportSection.report_id)
            )
            for row in (await db.execute(count_q)).fetchall():
                section_counts[row.report_id] = row.cnt
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
                "section_count": section_counts.get(r.id, 0),
            }
            for r in reports
        ]


async def _fetch_report_details(report_id: int):
    """Return serialized data to avoid detached-instance issues after session close."""
    async with get_db() as db:
        report = await db.get(Report, report_id)
        if not report:
            return None, [], [], {}

        sections_q = (
            select(ReportSection)
            .where(ReportSection.report_id == report_id)
            .order_by(ReportSection.section_order)
        )
        sections_raw = (await db.execute(sections_q)).scalars().all()

        runs_q = (
            select(ReportSectionRun)
            .where(ReportSectionRun.report_id == report_id)
            .order_by(desc(ReportSectionRun.created_at))
        )
        runs_raw = (await db.execute(runs_q)).scalars().all()

        run_ids = [r.id for r in runs_raw]
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

        # Serialize inside session while ORM objects are still bound
        report_dict = {
            "id": report.id,
            "title": report.title,
            "status": str(report.status).replace("ReportStatus.", ""),
            "quality_score": report.quality_score,
            "generation_time": report.generation_time,
            "token_usage": report.token_usage,
            "country_id": report.country_id,
        }
        sections = [
            {
                "id": s.id,
                "title": s.title,
                "content": s.content,
                "section_type": s.section_type,
                "data_sources": s.data_sources or [],
            }
            for s in sections_raw
        ]
        runs = [
            {
                "id": r.id,
                "section_id": r.section_id,
                "disease_name": r.disease_name,
                "section_type": r.section_type,
                "status": str(r.status).replace("ReportSectionRunStatus.", ""),
                "model": r.model,
                "quality_scores": r.quality_scores or {},
            }
            for r in runs_raw
        ]
        conv_map_serialized = {}
        for run_id_key, conv_list in conv_map.items():
            conv_map_serialized[run_id_key] = [
                {
                    "agent": c.agent,
                    "role": c.role,
                    "timestamp": c.timestamp.isoformat() if c.timestamp else "",
                    "prompt": c.prompt,
                    "system_prompt": c.system_prompt,
                    "response": c.response,
                    "model": c.model,
                    "provider": c.provider,
                    "tokens": c.tokens or {},
                    "duration": c.duration,
                    "temperature": c.temperature,
                }
                for c in conv_list
            ]

        return report_dict, sections, runs, conv_map_serialized


def _serialize_conversations(conv_map, run_id: int):
    """conv_map values are already serialized dicts."""
    return conv_map.get(run_id, [])


def _maybe_auto_refresh(report: Dict[str, Any], interval_seconds: int = 5) -> None:
    """When report is generating/pending/reviewing or user enabled auto-refresh, sleep and rerun for live progress."""
    status = (report.get("status") or "").upper()
    auto_refresh_on = st.session_state.get("report_monitor_auto_refresh", False)
    if status in ("GENERATING", "PENDING", "REVIEWING") or auto_refresh_on:
        if status in ("GENERATING", "PENDING", "REVIEWING"):
            st.caption(f"⏳ Auto-refreshing every {interval_seconds}s while report is {status}…")
        else:
            st.caption(f"⏳ Auto-refreshing every {interval_seconds}s…")
        time.sleep(interval_seconds)
        st.rerun()


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

        if "report_monitor_auto_refresh" not in st.session_state:
            st.session_state["report_monitor_auto_refresh"] = False
        st.session_state["report_monitor_auto_refresh"] = st.checkbox(
            "Auto refresh (5s)", value=st.session_state["report_monitor_auto_refresh"]
        )

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
                "Sections": r.get("section_count", 0),
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

        # Determine default selection: prefer preselected, else first report with sections
        report_uuids = [r["report_uuid"] for r in reports]
        preselected_uuid = None
        if preselected_report_id:
            for r in reports:
                if r["id"] == preselected_report_id:
                    preselected_uuid = r["report_uuid"]
                    break
        if preselected_uuid and preselected_uuid in report_uuids:
            default_index = report_uuids.index(preselected_uuid)
        else:
            # Prefer first report that has sections (has data to show)
            idx_with_sections = next((i for i, r in enumerate(reports) if r.get("section_count", 0) > 0), 0)
            default_index = idx_with_sections

        sel_uuid = st.selectbox(
            "Select report",
            options=report_uuids,
            index=default_index,
            format_func=lambda u: f"{u[:8]}… | {uuid_to_report[u]['country_name']} | {uuid_to_report[u].get('section_count', 0)} sections | {uuid_to_report[u]['title'][:30]}",
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
        country_name = sel_report_meta.get("country_name", str(report.get("country_id", "")))
        st.subheader(f"📄 {report['title']}")
        st.caption(f"UUID: `{sel_report_meta.get('report_uuid', '')}` · Country: **{country_name}**")

        # Summary metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Status", report.get("status", ""))
        m2.metric("Sections", len(sections))
        m3.metric("Quality", f"{report['quality_score']:.1%}" if report.get("quality_score") is not None else "N/A")
        m4.metric("Generation Time", f"{report['generation_time']:.1f}s" if report.get("generation_time") else "N/A")

        if report.get("token_usage"):
            st.markdown("#### Token Usage")
            st.json(report["token_usage"])

        st.markdown("---")
        st.markdown("### Sections & Runs")

        # Build latest run per (disease_name, section_type) first (needed for both completed and in-progress)
        latest_run_by_key = {}
        for run in runs:
            key = (run["disease_name"], run["section_type"])
            if key not in latest_run_by_key:
                latest_run_by_key[key] = run

        # When generating: show runs (QUEUED/RUNNING) even if no sections yet; only warn when no data at all
        if not sections and not runs:
            st.warning(
                "**This report has no sections or runs yet.** Generation may not have started or may have failed. "
                "Try selecting a different report or refresh in a moment."
            )
            _maybe_auto_refresh(report)
            return

        if not sections and runs:
            st.info("**Generation in progress.** Showing run status below. Page will auto-refresh every 5s while generating.")

        # Group items by disease
        disease_groups: Dict[str, list] = {}

        for section in sections:
            parts = section["title"].split(" - ")
            disease_name = parts[0] if len(parts) >= 2 else "Unknown"
            run = None
            for r in runs:
                if r["section_id"] == section["id"]:
                    run = r
                    break
            if not run and len(parts) >= 2:
                run = latest_run_by_key.get((parts[0], parts[1]))
            disease_groups.setdefault(disease_name, []).append({"type": "section", "section": section, "run": run})

        # Add orphan runs (no matching section)
        processed = set()
        for group in disease_groups.values():
            for item in group:
                s = item.get("section")
                if s:
                    parts = s["title"].split(" - ")
                    if len(parts) >= 2:
                        processed.add((parts[0], parts[1]))
        for (disease_name, section_type), run in latest_run_by_key.items():
            if (disease_name, section_type) not in processed:
                disease_groups.setdefault(disease_name, []).append({"type": "run", "section": None, "run": run})

        # Render one expander per disease
        for idx, (disease_name, group_items) in enumerate(sorted(disease_groups.items()), 1):
            statuses = []
            for item in group_items:
                r = item.get("run")
                if r:
                    s = str(r.get("status", "")).replace("ReportSectionRunStatus.", "")
                    if s and s not in statuses:
                        statuses.append(s)
            status_str = ", ".join(statuses) if statuses else "—"
            group_icon = "🟢" if all("COMPLETED" in s for s in statuses) else ("🔵" if any("RUNNING" in s for s in statuses) else ("🟡" if any("QUEUED" in s for s in statuses) else "⚪"))

            with st.expander(f"{group_icon} **{disease_name}** ({len(group_items)} sections) · {status_str}", expanded=False):
                for item in group_items:
                    section = item["section"]
                    run = item["run"]
                    if section:
                        title = section["title"]
                        section_type = section.get("section_type", "")
                        status = run.get("status", "COMPLETED") if run else "COMPLETED"
                        model_used = run.get("model") or "Unknown"
                        quality_scores = run.get("quality_scores", {}) if run else {}
                        data_sources = section.get("data_sources", [])
                        content = section.get("content", "")
                    else:
                        title = f"{run.get('disease_name') or 'Unknown'} - {run.get('section_type')}"
                        section_type = run.get("section_type", "")
                        status = run.get("status", "")
                        model_used = run.get("model") or "Pending..."
                        quality_scores = run.get("quality_scores", {})
                        content = None
                        data_sources = []

                    status_s = str(status).replace("ReportSectionRunStatus.", "")
                    icon = "🟢" if "COMPLETED" in status_s else ("🔵" if "RUNNING" in status_s else ("🟡" if "QUEUED" in status_s else "⚪"))

                    with st.expander(f"{icon} {section_type} | {status_s} | {model_used}", expanded=False):
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Type", section_type)
                        c2.metric("Model", model_used)
                        c3.metric("Quality", f"{quality_scores.get('overall', 0):.2f}" if quality_scores.get("overall") is not None else "N/A")

                        tabs = st.tabs(["Conversation", "Content", "Quality", "Data"])
                        with tabs[0]:
                            if run:
                                conv = _serialize_conversations(conv_map, run["id"])
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

        # When report is generating, auto-refresh so user sees sections/runs in real time
        _maybe_auto_refresh(report)

