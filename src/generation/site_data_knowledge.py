"""Knowledge and country-brief projections for static site data exports.

This module intentionally contains only in-memory projections. Database reads,
filesystem writes, and export orchestration remain outside this boundary.
"""

from src.core.country_library import get_country_display_name
from src.generation.site_data_about import (
    ABOUT_COUNTRY_NAMES_ZH,
    ABOUT_SOURCE_LABELS_ZH,
)
from src.knowledge.catalogue import (
    knowledge_brief_block_reason,
    knowledge_brief_publication_tier,
    resolve_disease_knowledge_status,
)
from src.knowledge.citations import normalize_knowledge_citation_group
from src.knowledge.profile_schema import resolve_knowledge_profile_schema
from src.knowledge.quality import (
    KNOWLEDGE_TEXT_FIELDS,
    assess_knowledge_brief,
    strip_unavailable_knowledge_sentences,
)

AUTHORITATIVE_KNOWLEDGE_SOURCE_TYPES = frozenset({"who", "who_don"})
AUTHORITATIVE_KNOWLEDGE_URL_MARKERS = ("who.int",)


def build_disease_knowledge_fields(
    disease: dict, brief_by_language: dict[str, dict] | None
) -> dict:
    """Build a field-aware, gracefully degradable public knowledge payload."""
    brief_by_language = brief_by_language or {}
    profile_schema = resolve_knowledge_profile_schema(disease)

    def localized_brief(language: str) -> dict:
        raw = {**(brief_by_language.get(language) or {}), "language": language}
        raw["metadata"] = {
            **(raw.get("metadata") or {}),
            "profile_schema": profile_schema.to_dict(),
        }
        return raw

    raw_en = localized_brief("en")
    raw_zh = localized_brief("zh")

    # A refresh can produce a different source set for each language. Preserve
    # the union and normalize both briefs against one stable citation order.
    merged_sources: list[dict] = []
    seen_source_keys: set[str] = set()
    for brief in (raw_en, raw_zh):
        for source in brief.get("source_attribution") or []:
            if not isinstance(source, dict):
                continue
            key = str(
                source.get("source_id")
                or source.get("id")
                or source.get("resolved_url")
                or source.get("url")
                or source.get("title")
                or ""
            ).strip()
            if not key or key in seen_source_keys:
                continue
            seen_source_keys.add(key)
            merged_sources.append(source)
    if merged_sources:
        raw_en["source_attribution"] = merged_sources
        raw_zh["source_attribution"] = merged_sources

    en, zh = normalize_knowledge_citation_group([raw_en, raw_zh])
    localized_briefs = {"en": en, "zh": zh}
    assessments = {
        language: assess_knowledge_brief(brief, language)
        for language, brief in localized_briefs.items()
    }
    language_tiers = {
        language: knowledge_brief_publication_tier(
            brief_by_language.get(language) or {}
        )
        for language in ("en", "zh")
    }
    language_is_public = {
        language: language_tiers[language] == "published" for language in ("en", "zh")
    }

    raw_knowledge_sources = (
        en.get("source_attribution") or zh.get("source_attribution") or []
    )
    original_status = resolve_disease_knowledge_status(brief_by_language.values())
    profile_languages = [
        language
        for language in ("en", "zh")
        if language_is_public[language] and assessments[language].profile_available
    ]
    available_languages = [
        language
        for language in ("en", "zh")
        if language_is_public[language] and assessments[language].available_fields
    ]
    knowledge_profile_available = bool(profile_languages)
    knowledge_status = "published" if knowledge_profile_available else "blocked"
    knowledge_tier = "published" if knowledge_profile_available else "blocked"
    block_reason = next(
        (
            knowledge_brief_block_reason(brief)
            for brief in brief_by_language.values()
            if knowledge_brief_block_reason(brief)
        ),
        None,
    )
    updated_values = [
        str(brief.get("updated_at"))
        for brief in localized_briefs.values()
        if brief.get("updated_at")
    ]
    knowledge_updated_at = max(updated_values) if updated_values else None
    knowledge_sources = raw_knowledge_sources if knowledge_profile_available else []
    has_authoritative_sources = knowledge_profile_available and (
        any(_is_authoritative_knowledge_source(source) for source in knowledge_sources)
        or any(
            str(brief.get("source_confidence") or "") == "high"
            for brief in brief_by_language.values()
            if isinstance(brief, dict)
        )
    )
    if knowledge_profile_available:
        profile_reason = (
            "partial_profile"
            if any(
                assessments[language].display_mode == "partial"
                for language in profile_languages
            )
            or len(profile_languages) < 2
            else None
        )
    elif not brief_by_language:
        profile_reason = "no_published_brief"
    elif original_status == "published":
        profile_reason = "insufficient_evidence"
    elif block_reason:
        profile_reason = block_reason
    else:
        profile_reason = "requires_review"

    def public_text(language: str, field: str, *aliases: str) -> str | None:
        if not language_is_public[language]:
            return None
        brief = localized_briefs[language]
        for candidate in (field, *aliases):
            result = assessments[language].fields.get(candidate)
            value = strip_unavailable_knowledge_sentences(
                brief.get(candidate), language
            )
            if result and result.available and value:
                return value
        return None

    completeness_values = [
        assessments[language].completeness for language in profile_languages
    ]
    knowledge_completeness = (
        round(sum(completeness_values) / len(completeness_values), 3)
        if completeness_values
        else 0.0
    )
    if not knowledge_profile_available:
        display_mode = "blocked"
    elif len(profile_languages) == 2 and all(
        assessments[language].display_mode == "full" for language in profile_languages
    ):
        display_mode = "full"
    else:
        display_mode = "partial"

    field_status = {
        field: {
            language: assessments[language].fields[field].status
            for language in ("en", "zh")
        }
        for field in KNOWLEDGE_TEXT_FIELDS
    }
    repair_sections = [
        field
        for field in ("brief", *profile_schema.required_fields)
        if any(
            not assessments[language].fields[field].available
            for language in ("en", "zh")
        )
    ]

    payload = {
        "disease_id": disease["disease_id"],
        "name_en": disease.get("name_en"),
        "name_zh": disease.get("name_zh"),
        "category": disease.get("category"),
        "description": disease.get("description"),
        "official_intro_en": public_text("en", "brief", "definition"),
        "official_intro_zh": public_text("zh", "brief", "definition"),
        "official_summary_en": public_text("en", "brief", "definition"),
        "official_summary_zh": public_text("zh", "brief", "definition"),
        "official_definition_en": public_text("en", "definition"),
        "official_definition_zh": public_text("zh", "definition"),
        "clinical_features_en": public_text("en", "clinical_features"),
        "clinical_features_zh": public_text("zh", "clinical_features"),
        "epidemiology_en": public_text("en", "epidemiology"),
        "epidemiology_zh": public_text("zh", "epidemiology"),
        "clinical_summary_en": public_text("en", "clinical_features"),
        "clinical_summary_zh": public_text("zh", "clinical_features"),
        "transmission_en": public_text("en", "transmission"),
        "transmission_zh": public_text("zh", "transmission"),
        "prevention_en": public_text("en", "prevention"),
        "prevention_zh": public_text("zh", "prevention"),
        "surveillance_note_en": public_text("en", "surveillance_note"),
        "surveillance_note_zh": public_text("zh", "surveillance_note"),
        "risk_groups_en": public_text("en", "risk_groups"),
        "risk_groups_zh": public_text("zh", "risk_groups"),
        "knowledge_sources": knowledge_sources,
        "knowledge_source_count": len(knowledge_sources),
        "knowledge_updated_at": knowledge_updated_at,
        "knowledge_status": knowledge_status,
        "knowledge_tier": knowledge_tier,
        "knowledge_block_reason": block_reason
        or (profile_reason if not knowledge_profile_available else None),
        "knowledge_profile_available": knowledge_profile_available,
        "knowledge_profile_reason": profile_reason,
        "knowledge_has_authoritative_sources": has_authoritative_sources,
        "knowledge_display_mode": display_mode,
        "knowledge_completeness": knowledge_completeness,
        "knowledge_available_languages": available_languages,
        "knowledge_profile_languages": profile_languages,
        "knowledge_profile_type": profile_schema.profile_type,
        "knowledge_profile_schema": profile_schema.to_dict(),
        "knowledge_section_labels": profile_schema.labels,
        "knowledge_applicable_section_count": len(profile_schema.applicable_fields),
        "knowledge_repair_sections": repair_sections,
        "knowledge_field_status": field_status,
        "knowledge_language_quality": {
            language: assessments[language].to_dict() for language in ("en", "zh")
        },
    }
    return payload


def _is_authoritative_knowledge_source(source: object) -> bool:
    """Infer whether a source should unlock the public disease profile."""
    if not isinstance(source, dict):
        return False

    source_type = str(source.get("source_type") or "").strip().lower()
    if source_type in AUTHORITATIVE_KNOWLEDGE_SOURCE_TYPES:
        return True

    source_names = {
        str(source.get(field) or "").strip().lower()
        for field in ("source_name", "title", "label")
        if str(source.get(field) or "").strip()
    }
    if any(
        name == "who" or name.startswith("who ") or "world health organization" in name
        for name in source_names
    ):
        return True

    source_url = (
        str(source.get("url") or source.get("source_url") or "").strip().lower()
    )
    return any(marker in source_url for marker in AUTHORITATIVE_KNOWLEDGE_URL_MARKERS)


def apply_disease_knowledge_fields(
    disease: dict, brief_by_language: dict[str, dict] | None
) -> dict:
    """Backward-compatible alias for the knowledge payload builder."""
    return build_disease_knowledge_fields(disease, brief_by_language)


def apply_country_brief_fields(
    country_data: dict, brief_by_language: dict[str, dict] | None
) -> dict:
    """Attach country page interpretive text, falling back to generated source context."""
    brief_by_language = brief_by_language or {}
    en = brief_by_language.get("en") or {}
    zh = brief_by_language.get("zh") or {}
    source_info = country_data.get("source_info") or {}
    source_labels = [
        src.get("label") for src in source_info.get("sources") or [] if src.get("label")
    ]
    source_label_en = (
        ", ".join(source_labels)
        or source_info.get("primary_label")
        or "official surveillance sources"
    )
    country_code = str(country_data.get("country_code") or "").upper()
    country_name_zh = (
        country_data.get("country_name_zh")
        or ABOUT_COUNTRY_NAMES_ZH.get(country_code)
        or get_country_display_name(country_code, "zh")
        or country_data.get("country_name")
        or country_code
    )
    source_labels_zh = [
        ABOUT_SOURCE_LABELS_ZH.get((country_code, src.get("scope")), src.get("label"))
        for src in source_info.get("sources") or []
        if src.get("label")
    ]
    source_label_zh = (
        ", ".join(label for label in source_labels_zh if label) or source_label_en
    )
    country_name = (
        country_data.get("country_name_en")
        or country_data.get("country_name")
        or country_data.get("country_code")
    )
    date_range = country_data.get("date_range") or {}
    frequency_meta = country_data.get("frequency_meta") or {}
    frequencies = frequency_meta.get("source_frequencies") or []
    frequency = " / ".join(frequencies) or frequency_meta.get(
        "source_frequency"
    ) or "UNKNOWN"

    country_data["brief_en"] = en.get("brief") or (
        f"{country_name} page consolidates infectious disease surveillance records from {source_label_en}. "
        "It combines source metadata, time-series charts, and downloadable machine-readable datasets."
    )
    country_data["brief_zh"] = zh.get("brief") or (
        f"{country_name_zh}页面整合来自{source_label_zh} 的传染病监测记录，包含来源信息、时间序列图表和可下载数据。"
    )
    country_data["surveillance_system_en"] = en.get("surveillance_system") or (
        f"The dataset is built from configured official feeds for {country_name}; current primary sources include {source_label_en}."
    )
    country_data["surveillance_system_zh"] = zh.get("surveillance_system") or (
        f"该数据集来自{country_name_zh}已配置的官方数据源；当前主要来源包括{source_label_zh}。"
    )
    country_data["interpretation_en"] = en.get("coverage_interpretation") or (
        f"Coverage currently spans {date_range.get('start') or 'N/A'} to {date_range.get('end') or 'N/A'} "
        f"across {country_data.get('disease_count') or 0} tracked diseases."
    )
    country_data["interpretation_zh"] = zh.get("coverage_interpretation") or (
        f"当前覆盖区间为 {date_range.get('start') or 'N/A'} 至 {date_range.get('end') or 'N/A'}，"
        f"覆盖 {country_data.get('disease_count') or 0} 种追踪疾病。"
    )
    country_data["reporting_cadence_en"] = en.get("reporting_cadence") or (
        f"Observed source periods are {frequency}; charts preserve source-period counts without frequency normalization."
    )
    country_data["reporting_cadence_zh"] = zh.get("reporting_cadence") or (
        f"已观测到的来源期间为 {frequency}；图表保留来源期间总量，不进行频率归一化。"
    )
    country_data["limitations_en"] = en.get("data_limitations") or (
        "Counts reflect reported surveillance records and may be affected by case definitions, reporting lag, source cadence, and missing population denominators."
    )
    country_data["limitations_zh"] = zh.get("data_limitations") or (
        "病例数反映已报告的监测记录，可能受病例定义、报告延迟、来源频率和人口分母缺失影响。"
    )
    country_data["source_summary_en"] = en.get("source_summary") or source_label_en
    country_data["source_summary_zh"] = zh.get("source_summary") or source_label_zh
    country_data["country_brief_status"] = "published" if en or zh else "fallback"
    country_data["country_brief_updated_at"] = en.get("updated_at") or zh.get(
        "updated_at"
    )
    return country_data
