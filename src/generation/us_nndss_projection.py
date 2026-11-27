"""Reviewed US-resident NNDSS current-week curves from the CDC weekly tables."""

from collections import defaultdict

from src.services.disease_series_policy import is_case_count_series

from .site_incidence import aggregate_incidence

# Source labels from CDC dataset x9gk-5huc. Spelling and capitalization of
# the published total and of Paratyphi changed between annual editions.
US_NNDSS_WEEKLY_LABELS = {
    "D021": ("Dengue virus infections, Dengue",),
    "D091": ("Botulism, Foodborne", "Botulism, Infant", "Botulism, Other (wound & unspecified)"),
    "D095": ("Coccidioidomycosis", "Coccidioidomycosis, total", "Coccidioidomycosis, Total"),
    "D125": ("Vancomycin-intermediate Staphylococcus aureus",),
    "D128": ("Arboviral diseases, West Nile virus disease",),
    "D167": ("Syphilis, Congenital",),
    "D218": ("Arboviral diseases, Eastern equine encephalitis virus disease",),
    "D219": ("Arboviral diseases, Western equine encephalitis virus disease",),
    "D225": ("Streptococcal toxic shock syndrome",),
    "D234": ("Salmonella Paratyphi infection", "SalmonellaParatyphi infection"),
    "D248": ("Arboviral diseases, Powassan virus disease",),
}


# Retain the CDC flags of the sibling categories in source downloads too.
# Only the weekly labels above participate in the reviewed public projection.
US_NNDSS_SOURCE_LABELS = {
    **US_NNDSS_WEEKLY_LABELS,
    "D021": (*US_NNDSS_WEEKLY_LABELS["D021"], "Dengue virus infections, Severe dengue",
             "Dengue virus infections, Dengue-like illness"),
    "D095": (*US_NNDSS_WEEKLY_LABELS["D095"], "Coccidioidomycosis, Confirmed",
             "Coccidioidomycosis, Probable"),
}


def project_us_nndss_weekly(legacy_records: list[dict], series_records: list[dict]) -> list[dict]:
    """Use reviewed CDC definitions and explicit no-case flags in chart copies."""
    from .site_series_projection import (
        _align_explicit_source_week,
        _attach_legacy_supplemental_metrics,
        _collapse_selected_series_records,
        _overlay_legacy_coverage_gaps,
        _partition_compatible_legacy_records,
        _projection_context,
        _source_series_details,
        apply_series_first_projection,
    )

    sources = defaultdict(list)
    legacy = defaultdict(list)
    for row in series_records:
        sources[row["disease_id"]].append(row)
    for row in legacy_records:
        legacy[row["disease_id"]].append(row)

    projected = []
    for disease_id in sorted(sources.keys() | legacy.keys()):
        rows = sources[disease_id]
        selected = [
            row for row in rows
            if row.get("source_system") == "SRC_US_NNDSS"
            and row.get("geography_key") == "country:US:national"
            and row.get("dimension_key") == "all"
            and row.get("temporal_granularity") == "weekly"
            and row.get("source_label") in US_NNDSS_WEEKLY_LABELS[disease_id]
            and is_case_count_series(row)
        ]
        numeric_rows = [row for row in rows if row.get("cases") is not None]
        if not selected:
            independent = [row for row in numeric_rows if row.get("source_system") != "SRC_US_NNDSS"]
            historical = [row for row in legacy[disease_id] if row["date"] < "2022-01-08"]
            if disease_id == "D091":
                historical = []
            projected.extend(apply_series_first_projection(historical, independent))
            continue

        selected_codes = sorted({row["series_code"] for row in selected})
        raw_sources = _source_series_details(rows)
        notes = {
            "D091": (
                "Foodborne, infant and other botulism are summed when all three are reported.",
                "同一周食源性、婴儿型和其他型肉毒中毒均有报告时计算合计。",
            ),
            "D095": (
                "The curve uses the CDC published weekly total, including the unsplit series in 2022.",
                "曲线使用 CDC 发布的每周合计，2022 年使用当时未分型的周报告数。",
            ),
        }
        if disease_id in {"D091", "D095", "D234"}:
            policy = "temporal_handoff" if len(selected_codes) > 1 else "single_series"
            if disease_id == "D091":
                policy = "sum_disjoint"
            en, zh = notes.get(disease_id, (
                "The CDC weekly Paratyphi series is joined across changes to the source label.",
                "副伤寒周报告按来源标签历次拼写衔接。",
            ))
            context = {
                "data_layer": "series_registry", "projection_policy": policy, "loss_risk": None,
                "selected_series_codes": selected_codes,
                "period_granularity": "weekly", "note_en": en, "note_zh": zh,
            }
        else:
            # Let the regular selector rank original numeric observations. The
            # extra dash rows must not make one category outrank another.
            eligible = [row for row in numeric_rows if row.get("dimension_key") == "all"
                        and is_case_count_series(row)]
            chosen, context = _projection_context(eligible or selected)
            if not chosen.intersection(selected_codes):
                independent = [row for row in numeric_rows if row.get("source_system") != "SRC_US_NNDSS"]
                historical = [row for row in legacy[disease_id] if row["date"] < "2022-01-08"]
                projected.extend(apply_series_first_projection(historical, independent))
                continue
            chart_codes = set(chosen)
            selected = [row for row in eligible if row["series_code"] in chart_codes] + [
                row for row in selected if row["series_code"] in chart_codes and row.get("cases") is None
            ]
            if disease_id == "D021" and len(chosen) == 1:
                context["note_en"] += " The selected CDC Dengue category excludes the separate severe dengue and dengue-like illness categories."
                context["note_zh"] += " 所选 CDC 登革热分项以外另有重症登革热和登革热样疾病分项。"
        context["source_series"] = raw_sources
        context["available_series_count"] = len({row["series_code"] for row in rows})
        context["note_en"] += " A dash in an empty CDC current-week cell means zero reported cases."
        context["note_zh"] += " CDC 当周数值为空且标记为短横线时按零例报告计算。"
        chart_rows = []
        weeks = defaultdict(dict)
        for row in selected:
            row = _align_explicit_source_week(row, "weekly")
            if row.get("cases") is None:
                if row.get("current_week_flag") != "-" or row.get("source_value_raw") not in (None, "", "-"):
                    continue
                rate, rate_source = aggregate_incidence(0, [row])
                row = {**row, "cases": 0, "incidence_rate": rate, "incidence_rate_source": rate_source}
            chart_rows.append(row)
            weeks[row["date"]][row["source_label"]] = row
        if disease_id == "D091":
            chart_rows = [row for row in chart_rows if len(weeks[row["date"]]) == len(US_NNDSS_WEEKLY_LABELS["D091"])]
        points = _collapse_selected_series_records(chart_rows, context)
        if disease_id == "D091":
            for point in points:
                components = weeks[point["date"]].values()
                point["incidence_rate"], point["incidence_rate_source"] = aggregate_incidence(
                    point["cases"], components
                )
        compatible, _, _ = _partition_compatible_legacy_records(context, legacy[disease_id])
        _attach_legacy_supplemental_metrics(points, context, compatible)
        # Flat rows may come from a single component or an unavailable CDC
        # cell. Only retain explicitly marked historical coverage before the
        # reviewed CDC weekly dataset started with MMWR week 1 of 2022.
        historical = [row for row in compatible if row["date"] < "2022-01-08"]
        if disease_id == "D091":
            historical = []
        projected.extend(_overlay_legacy_coverage_gaps(points, context, historical))
    return sorted(projected, key=lambda row: (row["date"], row["disease_id"]))
