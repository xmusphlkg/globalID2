"""Offline, evidence-aware health of Chinese provincial collection artifacts.

This module reads local collection evidence. It neither contacts publishers nor
reads/writes the database, and never infers zero from an absent disease or month.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.data.crawlers.cn_province_adapters.registry import province_adapter_registry

ROOT = Path(__file__).resolve().parents[2]

ACTION_LABELS = {
    "review_artifact_integrity": "核对本地证据完整性",
    "resolve_conflicting_observations": "核对同月数值冲突",
    "discover_official_source": "定位稳定官方来源",
    "collect_local_evidence": "采集本地证据",
    "inspect_listing_and_pagination": "检查目录与分页",
    "recollect_or_replay_changed_configuration": "按变更配置重采或重放",
    "retry_or_adapt_failed_reports": "重试或适配失败报告",
    "find_detail_for_summary_reports": "为仅有汇总的月报寻找病种明细",
    "find_machine_readable_source_or_review_images": "寻找可解析表或核对图片",
    "refresh_discovery_and_compare_official_release": "刷新发现并核对官方发布",
    "investigate_no_parsed_month": "排查无可用解析月份",
    "investigate_no_case_detail": "排查缺失的逐病种病例明细",
    "investigate_case_detail_lag": "排查病例明细更新滞后",
    "locate_detailed_table_or_retain_sparse_coverage": "查找明细表并保留稀疏标记",
    "review_disease_coverage_change": "核对病种覆盖变化",
    "check_unseen_months_within_operational_window": "核对窗口内未发现月份",
    "verify_table_completeness": "核验来源表完整性",
    "continue_scheduled_evidence_checks": "继续定期证据巡检",
}


def _digest(value: Any) -> str:
    # Match the collection profile checksum contract.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _month(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)[:7] + "-01").isoformat()[:7]
    except (ValueError, TypeError):
        return None


def _ordinal(month: str) -> int:
    year, number = map(int, month.split("-"))
    return year * 12 + number - 1


def _from_ordinal(number: int) -> str:
    year, month = divmod(number, 12)
    return f"{year:04d}-{month + 1:02d}"


def _count(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 and int(value) == value


def _hash_state(captured: str | None, current: str) -> dict:
    return {"captured": captured, "current": current,
            "status": "unknown" if not captured else "unchanged" if captured == current else "changed"}


def parser_fingerprint(repository_root: Path = ROOT) -> str:
    base = repository_root / "src/data/crawlers"
    files = [base / "cn_provinces.py", base / "cn_province_adapters/base.py"]
    files += sorted((base / "cn_province_adapters/parsing").glob("*.py"))
    # Alias and validity changes affect interpretation even when Python code
    # is unchanged, so they invalidate the captured parser semantics too.
    files.append(repository_root / "configs/cn_province_sources.json")
    files += [repository_root / path for path in (
        "src/services/document_images.py", "src/services/cn_province_image_review.py",
        "src/services/ingestion/countries/cn_province.py",
    )]
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(repository_root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _source_profile(config: dict, shared: dict) -> dict:
    return {
        "primary_index_url": config.get("index_url") or None,
        "archive_index_urls": list(config.get("archive_index_urls") or []),
        "declared_parser": config.get("parser"),
        "pagination": {key: config.get(key) for key in (
            "pagination_url_template", "max_index_pages", "index_post_data",
            "index_json_article_url_template", "index_page_parameter", "index_json_html_path",
        )},
        "report_title_contains": list(config.get("report_title_contains") or []),
        "timezone": config.get("timezone", "Asia/Shanghai"),
        "monthly_source": shared.get("monthly_report", {}),
        "historical_sources": {
            "workbook": shared.get("history", {}),
            "datacenter": {key: shared.get("datacenter", {}).get(key) for key in (
                "source_id", "availability_url", "cadence", "projection_priority",
            )},
            "health_scope_note": "Historical source configuration only; this report does not assess historical database coverage.",
        },
        "automatic_image_parsing": True,
        "image_admission": "Original documents require independent AI model votes, a fixed majority quorum and deterministic checks; unresolved candidates remain needs_review.",
    }


def _valid_rows(report: dict, code: str, month: str) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for row in report.get("rows") or []:
        if not isinstance(row, dict):
            errors.append("invalid_row_shape")
            continue
        if (row.get("JurisdictionCode") != code or row.get("GeographyKey") != f"country:{code}:national"
                or _month(row.get("Date")) != month or row.get("SourceID") != "SRC_CN_PROV_MONTHLY_REPORT"):
            errors.append("row_scope_mismatch")
            continue
        if not row.get("SourceDiseaseCode"):
            errors.append("missing_source_disease_code")
            continue
        if not _count(row.get("Cases")) and not _count(row.get("Deaths")):
            errors.append("no_explicit_count")
            continue
        rows.append(row)
    return rows, sorted(set(errors))


def _evidence_type(report: dict, rows: list[dict]) -> str:
    if report.get("evidence_type") in {"table", "reviewed_image_table"}:
        return report["evidence_type"]
    if rows and all("explicit_narrative_value" in (r.get("Raw") or {}) for r in rows):
        return "narrative_sparse"
    return report.get("evidence_type") or "unclassified"


def _assess_province(code: str, config: dict, collection: dict, reviews: list[dict], *,
                     closed_month: str, window_start: str, stale_months: int,
                     reference_months: int, current_parser_sha: str, shared: dict,
                     artifact_errors: list[dict]) -> dict:
    months: dict[str, dict] = {}
    discovered: set[str] = set()
    future: set[str] = set()
    observed_formats: set[str] = set()
    pending_reviews = []
    approved_images: dict[str, set[str]] = defaultdict(set)
    reports = list(collection.get("reports") or [])
    for review in reviews:
        month = _month(review.get("report_month"))
        if not month:
            continue
        if review.get("status") != "approved_for_import":
            pending_reviews.append({"month": month, "status": review.get("status"), "path": review.get("_path")})
            continue
        if (review.get("province_code") != code or not review.get("validation", {}).get("all_checks_passed")
                or review.get("coverage", {}).get("uncertain_fields", 1) != 0):
            artifact_errors.append({"reason": "approved_image_review_not_validated", "month": month, "path": review.get("_path")})
            continue
        reviewed_rows, errors = _valid_rows(review, code, month)
        if errors or not reviewed_rows:
            artifact_errors.append({"reason": "approved_image_review_invalid_rows", "month": month, "errors": errors})
            continue
        approved_images[month].update(x.get("url") for x in review.get("source_images", []) if x.get("url"))
        reports.append({"date": month + "-01", "status": "parsed", "rows": reviewed_rows,
                        "url": review.get("official_report_url"), "evidence_type": "reviewed_image_table",
                        "source_table_complete": True, "review_path": review.get("_path"),
                        "content_type": "manually_reviewed_image", "diagnostics": []})

    for link in collection.get("links") or []:
        month = _month(link.get("date") or link.get("report_date"))
        if month:
            (discovered if month <= closed_month else future).add(month)
    for report in reports:
        month = _month(report.get("date") or report.get("report_date"))
        if not month:
            artifact_errors.append({"reason": "report_month_invalid", "url": report.get("url")})
            continue
        if month > closed_month:
            future.add(month)
            continue
        discovered.add(month)
        entry = months.setdefault(month, {
            "month": month, "reports": [], "values": {"cases": defaultdict(set), "deaths": defaultdict(set)},
            "evidence_types": set(), "table_present": False, "source_table_complete": False,
            "unparsed_images": [], "parsed_report_count": 0,
        })
        rows, errors = _valid_rows(report, code, month)
        if report.get("status") != "parsed":
            rows = []
        evidence = _evidence_type(report, rows)
        if report.get("content_type"):
            observed_formats.add(str(report["content_type"]))
        details = {key: report.get(key) for key in ("url", "source_url", "status", "error", "evidence_type", "content_type", "review_path") if report.get(key) is not None}
        value_diagnostics = [d for d in report.get("diagnostics") or []
                             if d.get("status") in {"summary_only", "no_disease_values"}]
        if value_diagnostics:
            details["source_value_status"] = value_diagnostics[-1]["status"]
            details["source_value_diagnostic"] = value_diagnostics[-1]
        details.update({"valid_rows": len(rows), "row_errors": errors})
        entry["reports"].append(details)
        if rows:
            entry["parsed_report_count"] += 1
            entry["evidence_types"].add(evidence)
            is_table = evidence in {"table", "reviewed_image_table"}
            entry["table_present"] |= is_table
            # Collection's generic `table` marker is not a completeness receipt.
            entry["source_table_complete"] |= is_table and not errors and report.get("source_table_complete") is True
        for row in rows:
            disease = str(row["SourceDiseaseCode"])
            if disease.endswith("__deaths"):
                if _count(row.get("Cases")):
                    entry["values"]["deaths"][disease.removesuffix("__deaths")].add(row["Cases"])
                continue
            for metric, field in (("cases", "Cases"), ("deaths", "Deaths")):
                if _count(row.get(field)):
                    entry["values"][metric][disease].add(row[field])
        for diagnostic in report.get("diagnostics") or []:
            if diagnostic.get("status") in {"report_image_unparsed", "image_pending_review", "image_capture_failed"}:
                entry["unparsed_images"].append({
                    "url": diagnostic.get("url"), "article_url": diagnostic.get("article_url") or report.get("url"),
                    "resolved_by_approved_review": diagnostic.get("url") in approved_images.get(month, set()),
                    "automatic_parser_supported": True,
                })

    ordered = []
    for month, entry in sorted(months.items()):
        values = entry.pop("values")
        conflicts = []
        for metric in ("cases", "deaths"):
            conflicts.extend({"metric": metric, "disease_code": disease, "values": sorted(counts)}
                             for disease, counts in values[metric].items() if len(counts) > 1)
            codes = sorted(disease for disease, counts in values[metric].items() if len(counts) == 1)
            entry[f"{metric}_disease_codes"] = codes
            entry[f"{metric}_disease_count"] = len(codes)
        entry["conflicting_values"] = conflicts
        if conflicts:
            entry["source_table_complete"] = False
        entry["evidence_types"] = sorted(entry["evidence_types"])
        entry["narrative_only"] = entry["evidence_types"] == ["narrative_sparse"]
        entry["table_completeness_status"] = (
            "verified_source_published_rows_only" if entry["source_table_complete"] else
            "table_coverage_unverified" if entry["table_present"] else "no_table_evidence"
        )
        entry["failed_reports"] = [r for r in entry["reports"] if r.get("status") != "parsed" or not r["valid_rows"] or r["row_errors"]]
        entry["unresolved_image_count"] = sum(not item["resolved_by_approved_review"] for item in entry["unparsed_images"])
        ordered.append(entry)

    parsed = [m["month"] for m in ordered if m["parsed_report_count"]]
    case_months = [m["month"] for m in ordered if m["cases_disease_count"]]
    death_months = [m["month"] for m in ordered if m["deaths_disease_count"]]
    table_months = [m["month"] for m in ordered if m["table_present"]]
    complete_months = [m["month"] for m in ordered if m["source_table_complete"]]
    latest_discovered = max(discovered, default=None)
    latest_parsed = max(parsed, default=None)
    latest_cases = max(case_months, default=None)
    latest_deaths = max(death_months, default=None)
    window = [_from_ordinal(n) for n in range(_ordinal(window_start), _ordinal(closed_month) + 1)]
    failed_months = [m["month"] for m in ordered if m["failed_reports"]]
    summary_only_months = [m["month"] for m in ordered if m["failed_reports"]
                           and not m["parsed_report_count"]
                           and all(r.get("source_value_status") == "summary_only" for r in m["failed_reports"])]
    no_disease_value_months = [m["month"] for m in ordered if any(
        r.get("source_value_status") in {"summary_only", "no_disease_values"} for r in m["reports"])]
    unresolved_failed = [m["month"] for m in ordered if m["failed_reports"]
                         and not m["parsed_report_count"] and m["month"] not in summary_only_months]
    sparse_months = [m["month"] for m in ordered if m["narrative_only"]]
    regression = []
    for i, entry in enumerate(ordered):
        if not entry["cases_disease_count"]:
            continue
        previous = [p for p in ordered[:i] if p["table_present"] and not p["conflicting_values"]
                    and 0 < _ordinal(entry["month"]) - _ordinal(p["month"]) <= reference_months]
        if not previous:
            continue
        verified = [p for p in previous if p["source_table_complete"]]
        reference = (verified or previous)[-1]
        for metric in ("cases", "deaths"):
            removed = sorted(set(reference[f"{metric}_disease_codes"]) - set(entry[f"{metric}_disease_codes"]))
            if removed:
                regression.append({"month": entry["month"], "reference_month": reference["month"], "metric": metric,
                                   "reference_completeness": reference["table_completeness_status"],
                                   "missing_previously_reported_diseases": removed, "action": "review_source_scope_do_not_fill_zero"})

    source_sha = _digest(config)
    source_drift = _hash_state(collection.get("source_profile_sha256"), source_sha)
    parser_drift = _hash_state(collection.get("parser_sha256"), current_parser_sha)
    discovery_diagnostics = collection.get("discovery_diagnostics") or []
    discovery_errors = [x for x in collection.get("errors") or [] if x.get("stage") == "discover"]
    discovery_limits = [x for x in discovery_diagnostics if x.get("status") not in {"fetched", "success"}]
    latest_lag = _ordinal(closed_month) - _ordinal(latest_parsed) if latest_parsed else None
    case_lag = _ordinal(closed_month) - _ordinal(latest_cases) if latest_cases else None
    death_lag = _ordinal(closed_month) - _ordinal(latest_deaths) if latest_deaths else None
    discovered_lag = _ordinal(closed_month) - _ordinal(latest_discovered) if latest_discovered else None
    actions = []
    def action(kind, priority, reason, **scope):
        actions.append({"action": kind, "priority": priority, "reason": reason, **scope})
    if artifact_errors:
        action("review_artifact_integrity", "P0", "本地证据存在范围、核验或读取错误。", errors=artifact_errors)
    if any(m["conflicting_values"] for m in ordered):
        action("resolve_conflicting_observations", "P0", "同一月份、病种和指标出现不同的明确数值。", months=[m["month"] for m in ordered if m["conflicting_values"]])
    if not config.get("index_url") or config.get("parser") == "discovery_pending":
        action("discover_official_source", "P1", "尚未配置稳定的官方月报来源。")
    elif not collection:
        action("collect_local_evidence", "P1", "没有本地采集文件，暂不能判断来源健康状态。")
    if discovery_errors or discovery_limits:
        action("inspect_listing_and_pagination", "P1", "目录发现失败，或已达到配置的分页预算。", diagnostics=discovery_errors + discovery_limits)
    if source_drift["status"] == "changed" or parser_drift["status"] == "changed":
        action("recollect_or_replay_changed_configuration", "P1", "采集时记录的来源或解析器指纹与当前配置不同。")
    if unresolved_failed:
        action("retry_or_adapt_failed_reports", "P1", "已发现报告的这些月份仍没有可用解析记录。", months=unresolved_failed)
    if summary_only_months:
        action("find_detail_for_summary_reports", "P1", "官网月报仅有汇总、排名或名单，需另找可核验病种明细。", months=summary_only_months)
    unresolved_images = [m["month"] for m in ordered if m["unresolved_image_count"]]
    if unresolved_images:
        action("find_machine_readable_source_or_review_images", "P1", "仍有图片表未解析；已批准的人工核对仅适用于指定月份及图片。", months=unresolved_images)
    if ((latest_lag is not None and latest_lag >= stale_months)
            or (discovered_lag is not None and discovered_lag >= stale_months)):
        action("refresh_discovery_and_compare_official_release", "P1", "最新发现或解析月份已达到运维时效预算，需核对官方发布及栏目迁移；该预算不是官方发布期限。",
               latest_discovered_month=latest_discovered, latest_parsed_month=latest_parsed,
               discovered_month_lag=discovered_lag, parsed_month_lag=latest_lag)
    if not latest_parsed and collection and config.get("index_url"):
        action("investigate_no_parsed_month", "P1", "已配置来源，但本地证据中没有可用的月度观察值。")
    elif latest_parsed and not latest_cases:
        action("investigate_no_case_detail", "P1", "已解析月报但未取得逐病种病例值；死亡值不能代表病例覆盖。", latest_parsed_month=latest_parsed)
    elif (case_lag is not None and case_lag >= stale_months and latest_parsed
          and latest_cases and latest_parsed > latest_cases):
        action("investigate_case_detail_lag", "P1", "近期报告只有死亡或其他非病例值，病例明细仍滞后。", latest_cases_month=latest_cases, latest_parsed_month=latest_parsed)
    if sparse_months:
        action("locate_detailed_table_or_retain_sparse_coverage", "P2", "正文计数只覆盖明确点名的病种，不能代表整表。", months=sparse_months)
    if regression:
        action("review_disease_coverage_change", "P2", "后续提取缺少近期表格中出现过的病种或指标，需核对格式与定义，不能补零。", months=sorted({x["month"] for x in regression}))
    missing = sorted(set(window) - discovered)
    if missing:
        action("check_unseen_months_within_operational_window", "P2", "本次选中链接未覆盖这些月份，尚不能据此认定官方未发布。", months=missing)
    if table_months and not complete_months:
        action("verify_table_completeness", "P2", "成功解析表格行尚不足以证明已覆盖来源表全部公布行。", latest_table_month=max(table_months))
    if not actions:
        action("continue_scheduled_evidence_checks", "P3", "本次证据未检出问题，但不能据此保证上游覆盖完整。")
    profile = _source_profile(config, shared)
    profile["observed_content_types"] = sorted(observed_formats)
    return {
        "province_code": code, "name_zh": config.get("name_zh", code), "source_profile": profile,
        "collection_started_at": collection.get("started_at"), "collection_finished_at": collection.get("finished_at"),
        "collection_in_progress": bool(collection and not collection.get("finished_at")),
        "fingerprints": {"source_configuration": source_drift, "parser": parser_drift,
                         "shared_source_contract_sha256": _digest(shared)},
        "latest_discovered_month": latest_discovered, "latest_parsed_month": latest_parsed,
        "latest_cases_month": latest_cases, "latest_deaths_month": latest_deaths,
        "latest_table_month": max(table_months, default=None),
        "latest_complete_table_month": max(complete_months, default=None),
        "latest_complete_table_scope": "verified_source_published_rows_only",
        "discovery_scope": "Captured selected report links, including retained historical URLs; not an exhaustive official-site inventory.",
        "discovered_link_count": collection.get("discovered"), "selected_link_count": collection.get("selected"),
        "discovery_errors": discovery_errors, "discovery_limits": discovery_limits,
        "freshness": {"closed_month_lag_discovered": discovered_lag, "closed_month_lag_parsed": latest_lag,
                      "closed_month_lag_cases": case_lag, "closed_month_lag_deaths": death_lag,
                      "stale_months_budget": stale_months, "parsed_stale": None if latest_lag is None else latest_lag >= stale_months,
                      "cases_stale": None if case_lag is None else case_lag >= stale_months,
                      "budget_is_official_commitment": False},
        "months": ordered, "failed_report_months": failed_months, "unresolved_failed_months": unresolved_failed,
        "summary_only_months": summary_only_months, "no_disease_value_months": no_disease_value_months,
        "narrative_only_months": sparse_months, "unresolved_image_months": unresolved_images,
        "approved_image_review_months": sorted(approved_images), "pending_image_reviews": pending_reviews,
        "open_or_future_report_months": sorted(future), "coverage_decreases": regression,
        "month_gaps": {"window_start": window_start, "window_end": closed_month,
                       "not_seen_in_selected_links": missing, "no_parsed_observations": sorted(set(window) - set(parsed)),
                       "no_case_values": sorted(set(window) - set(case_months)),
                       "no_death_values": sorted(set(window) - set(death_months)),
                       "meaning": "Evidence gaps only; missing months and diseases are unknown, not zero."},
        "next_action": sorted(actions, key=lambda x: (x["priority"], x["action"])),
    }


def build_health_report(input_dir: str | Path, *, as_of: date | None = None, stale_months: int = 3,
                        window_months: int = 12, reference_months: int = 6,
                        registry: Mapping | None = None, repository_root: Path = ROOT) -> dict:
    if min(stale_months, window_months, reference_months) < 1:
        raise ValueError("Month budgets must be positive integers")
    input_dir = Path(input_dir)
    if not (input_dir / "provinces").is_dir():
        raise ValueError("Input must contain a provinces/ collection artifact directory")
    as_of = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    current = _ordinal(as_of.isoformat()[:7])
    closed_month, window_start = _from_ordinal(current - 1), _from_ordinal(current - window_months)
    registry = registry if registry is not None else province_adapter_registry()
    shared_path = repository_root / "configs/cn_province_sources.json"
    shared = _read(shared_path) if shared_path.is_file() else {}
    parser_sha = parser_fingerprint(repository_root)
    provinces, inputs = [], {}
    for code, adapter in registry.items():
        config = asdict(adapter) if is_dataclass(adapter) else dict(adapter)
        path = input_dir / "provinces" / f"{code}.json"
        errors, collection, reviews = [], {}, []
        for artifact in [path, *sorted((input_dir / "image_review").glob(f"{code}_*.json"))]:
            if not artifact.is_file():
                continue
            try:
                payload = artifact.read_bytes()
                inputs[str(artifact.relative_to(input_dir))] = hashlib.sha256(payload).hexdigest()
                obj = json.loads(payload)
                if not isinstance(obj, dict):
                    raise ValueError("Artifact must contain a JSON object")
                if artifact == path:
                    if obj.get("province") != code:
                        raise ValueError("Collection province identity does not match its filename")
                    collection = obj
                elif obj.get("report_month") and "rows" in obj:
                    reviews.append({**obj, "_path": str(artifact.relative_to(input_dir))})
            except (OSError, ValueError, TypeError) as exc:
                errors.append({"path": str(artifact), "reason": str(exc)})
        provinces.append(_assess_province(code, config, collection, reviews, closed_month=closed_month,
                         window_start=window_start, stale_months=stale_months, reference_months=reference_months,
                         current_parser_sha=parser_sha, shared=shared, artifact_errors=errors))
    return {
        "schema_version": 1, "assessment_date": as_of.isoformat(), "timezone": "Asia/Shanghai",
        "scope": "offline_collection_evidence_only", "public_release": False,
        "operational_budgets": {"stale_months": stale_months, "window_months": window_months,
                                "reference_months": reference_months, "official_publication_commitment": False},
        "evidence_rules": [
            "Latest discovered, parsed, tabular and explicitly verified source-table months are separate.",
            "A table marker or a nonempty row list does not prove complete disease coverage.",
            "Only approved, validated image reviews supplement their exact report month; automatic image parsing remains unsupported.",
            "Unknown capture fingerprints remain unknown, not unchanged. The current parser hash cannot date an old collection.",
            "Coverage decreases and unseen months trigger investigation only; absent facts are never converted to zero.",
        ],
        "input_artifact_sha256": inputs, "input_inventory_sha256": _digest(inputs),
        "provinces": provinces,
        "summary": {"province_count": len(provinces),
                    "with_parsed_month": sum(p["latest_parsed_month"] is not None for p in provinces),
                    "with_case_month": sum(p["latest_cases_month"] is not None for p in provinces),
                    "with_death_month": sum(p["latest_deaths_month"] is not None for p in provinces),
                    "with_table_month": sum(p["latest_table_month"] is not None for p in provinces),
                    "with_verified_source_table_month": sum(p["latest_complete_table_month"] is not None for p in provinces),
                    "next_action_counts": dict(sorted(Counter(a["action"] for p in provinces for a in p["next_action"]).items()))},
    }


def render_health_markdown(report: dict) -> str:
    lines = [f"# 中国省级采集健康检查（{report['assessment_date']}）", "",
             "仅检查本地采集证据，不代表数据库或公开站点状态。发现、解析、表格和整表核验分别统计；普通表格成功解析仍需完整性核验。",
             f"新鲜度预算为 {report['operational_budgets']['stale_months']} 个已结束月份，属于运维设置，不是官方发布承诺。图片核验只补指定月份，不表示支持自动图片解析。", "",
             "|省份|最新发现|最新病例|最新死亡|最新解析|最近表格|已核验来源表|最新月病例/死亡病种|下一步|",
             "|---|---|---|---|---|---|---|---|---|"]
    for p in report["provinces"]:
        latest = next((m for m in p["months"] if m["month"] == p["latest_parsed_month"]), {})
        lines.append("|" + "|".join([p["name_zh"], *[p[k] or "未知" for k in (
            "latest_discovered_month", "latest_cases_month", "latest_deaths_month", "latest_parsed_month",
            "latest_table_month", "latest_complete_table_month")],
            f"{latest.get('cases_disease_count', 0)}/{latest.get('deaths_disease_count', 0)}",
            "；".join(ACTION_LABELS.get(a["action"], a["action"]) for a in p["next_action"][:3])]) + "|")
    for p in report["provinces"]:
        profile = p["source_profile"]
        lines += ["", f"## {p['name_zh']}（{p['province_code']}）", "",
                  f"主入口：{profile['primary_index_url'] or '未配置'}",
                  "历史入口：" + ("；".join(profile["archive_index_urls"]) or "未配置"),
                  f"声明格式：{profile['declared_parser']}；观察格式：{', '.join(profile['observed_content_types']) or '未知'}。",
                  "分页：`" + json.dumps(profile["pagination"], ensure_ascii=False, separators=(",", ":")) + "`",
                  f"配置漂移：{p['fingerprints']['source_configuration']['status']}；解析器漂移：{p['fingerprints']['parser']['status']}。",
                  "当前配置 SHA：`" + p["fingerprints"]["source_configuration"]["current"] + "`",
                  "当前解析器 SHA：`" + p["fingerprints"]["parser"]["current"] + "`",
                  "发现后未解析月份：" + (", ".join(p["unresolved_failed_months"]) or "无已记录项"),
                  "只有汇总而无病种值月份：" + (", ".join(p["summary_only_months"]) or "无已记录项"),
                  "仅正文月份：" + (", ".join(p["narrative_only_months"]) or "无已记录项"),
                  "图片待处理月份：" + (", ".join(p["unresolved_image_months"]) or "无已记录项"),
                  "本次窗口未发现月份：" + (", ".join(p["month_gaps"]["not_seen_in_selected_links"]) or "无已记录项"), "",
                  "窗口内无明确病例值月份：" + (", ".join(p["month_gaps"]["no_case_values"]) or "无已记录项"),
                  "窗口内无明确死亡值月份：" + (", ".join(p["month_gaps"]["no_death_values"]) or "无已记录项"), "",
                  "|月份|病例病种|死亡病种|证据|表完整性|失败报告数|未处理图片数|",
                  "|---|---|---|---|---|---|---|"]
        for m in p["months"]:
            lines.append(f"|{m['month']}|{m['cases_disease_count']}|{m['deaths_disease_count']}|{', '.join(m['evidence_types']) or '无可用行'}|{m['table_completeness_status']}|{len(m['failed_reports'])}|{m['unresolved_image_count']}|")
        lines += ["", "下一步：", ""]
        lines += [f"- {a['priority']} {ACTION_LABELS.get(a['action'], a['action'])}：{a['reason']}" for a in p["next_action"]]
        if p["coverage_decreases"]:
            lines += ["", "病种集合变化（仅报警，不补零）：", ""]
            lines += [f"- {c['month']} 对比 {c['reference_month']}，{c['metric']} 缺少：{', '.join(c['missing_previously_reported_diseases'])}；参考完整性：{c['reference_completeness']}。" for c in p["coverage_decreases"]]
    return "\n".join(lines) + "\n"
