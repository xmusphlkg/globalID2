"""Catalogue-derived fallback disease briefs.

These helpers are used only when no reviewed knowledge-base brief exists. The
text is intentionally framed as surveillance interpretation, not clinical
guidance, and avoids pretending to be an official source.
"""
from __future__ import annotations

from typing import Any, Iterable


NON_PUBLIC_DISEASE_IDS = frozenset({"D999"})
CATALOGUE_FALLBACK_BRIEF_TIER = "catalogue_fallback"
NON_PUBLIC_DESCRIPTION_PHRASES = (
    "aggregate total",
    "deprecated duplicate",
)


def build_catalogue_disease_brief(disease: Any, language: str = "en") -> dict[str, str]:
    """Build a useful, clearly-labelled fallback brief from local catalogue fields."""
    language = "zh" if language == "zh" else "en"
    name_en = _value(disease, "name_en") or _value(disease, "standard_name_en") or _value(disease, "name") or _value(disease, "disease_id") or "This disease"
    name_zh = _value(disease, "name_zh") or _value(disease, "standard_name_zh") or _metadata_value(disease, "standard_name_zh") or name_en
    display_name = name_zh if language == "zh" else name_en
    category = _value(disease, "category") or "infectious disease"
    description = _value(disease, "description") or ""
    transmission_field = _value(disease, "transmission") or ""
    exposure = _infer_exposure_context(name_en, description, transmission_field, language)
    brief_context = _brief_context_sentence(name_en, description, transmission_field, language)

    if language == "zh":
        category_text = _category_zh(category)
        description_text = f"本地目录描述为：{description}。" if description else "本地目录暂未提供更详细的病原学描述。"
        definition = (
            f"{display_name}是一种纳入 GlobalID 传染病监测目录的{category_text}，"
            f"其本地目录描述为：{description}"
            if description
            else f"{display_name}是一种纳入 GlobalID 传染病监测目录的{category_text}。"
        )
        clinical_features = (
            f"{display_name}的临床特征应理解为监测和群体层面解释，而不是个体诊疗说明。"
            "病例上升可能来自真实传播变化，也可能来自检测强度、报告制度、病例定义或数据补录变化。"
        )
        epidemiology = (
            f"{display_name}的流行病学背景应结合地区分布、季节性、暴发事件和数据覆盖范围一起阅读。"
            f"{brief_context}"
        )
        surveillance_note = (
            "本地目录级后备简介的用途是帮助阅读病例数、死亡数、发病率和国家/时间差异；"
            "解读时应同时查看报告频率、病例定义、缺失值和来源发布时间。"
        )
        return {
            "brief": (
                f"{definition}。{epidemiology} {surveillance_note}"
            ),
            "definition": definition,
            "clinical_features": clinical_features,
            "epidemiology": epidemiology,
            "clinical_summary": clinical_features,
            "transmission": exposure,
            "prevention": (
                "在缺少已审核官方 brief 时，本站只提供监测层面的预防解读：关注官方通报、及时报告异常增幅、比较地区和季节性变化，并以当地卫生机构建议为准。"
            ),
            "surveillance_note": surveillance_note,
            "risk_groups": (
                "重点人群不从目录字段自动断言；正式发布前应由 WHO、国家机构或其他可追溯来源支持。当前页面可先用于识别哪些地区、时间段或数据源出现异常负担。"
            ),
            "disclaimer": "该简介来自本地疾病目录与监测解释模板，尚未替代官方疾病介绍；不构成医疗建议。",
        }

    category_text = str(category).lower() if category else "infectious disease"
    description_text = f"The local catalogue describes it as: {description}." if description else "The local catalogue does not yet provide a detailed etiologic description."
    definition = (
        f"{display_name} is a {category_text} entry in the GlobalID infectious disease surveillance catalogue. "
        f"{description_text}"
    )
    clinical_features = (
        f"{display_name} is interpreted as a public-health monitoring entity rather than an individual clinical guidance topic. "
        "Clinical interpretation should stay at the population level, with attention to severity patterns, case definitions, and reporting practice."
    )
    epidemiology = (
        f"The epidemiology of {display_name} should be read alongside geography, seasonality, outbreak activity, reporting cadence, and coverage limitations. "
        f"{brief_context}"
    )
    surveillance_note = (
        "This fallback brief is designed to support surveillance interpretation: reported cases, deaths, rates, country coverage, and time trends should be read alongside reporting cadence, case definitions, and missingness."
    )
    return {
        "brief": (
            f"{definition} {epidemiology} {surveillance_note}"
        ),
        "definition": definition,
        "clinical_features": clinical_features,
        "epidemiology": epidemiology,
        "clinical_summary": clinical_features,
        "transmission": exposure,
        "prevention": (
            "Until a reviewed source-backed brief is published, prevention context is limited to surveillance interpretation: monitor official bulletins, flag unusual increases, compare geography and seasonality, and follow local public-health authority guidance."
        ),
        "surveillance_note": surveillance_note,
        "risk_groups": (
            "Risk groups are not asserted from catalogue fields alone. They should be populated from WHO, national-agency, or other traceable sources; for now, use this page to identify regions, periods, or sources with unusual reported burden."
        ),
        "disclaimer": "This fallback brief comes from the local disease catalogue and a surveillance interpretation template; it is not medical advice.",
    }


def build_catalogue_disease_brief_payload(
    disease: Any,
    language: str = "en",
    *,
    fallback_reason: str = "no_public_sources",
) -> dict[str, Any]:
    """Build a persistable low-confidence fallback brief payload."""
    normalized_language = _normalize_language(language)
    fallback = build_catalogue_disease_brief(disease, normalized_language)
    reason = (fallback_reason or "no_public_sources").strip() or "no_public_sources"

    return {
        "disease_id": _value(disease, "disease_id"),
        "language": normalized_language,
        "brief": fallback.get("brief"),
        "definition": fallback.get("definition"),
        "clinical_features": fallback.get("clinical_features"),
        "epidemiology": fallback.get("epidemiology"),
        "clinical_summary": fallback.get("clinical_summary") or fallback.get("clinical_features"),
        "transmission": fallback.get("transmission"),
        "prevention": fallback.get("prevention"),
        "surveillance_note": fallback.get("surveillance_note"),
        "risk_groups": fallback.get("risk_groups"),
        "source_ids": [],
        "source_attribution": [],
        "disclaimer": fallback.get("disclaimer"),
        "model": "catalogue-surveillance-fallback-v1",
        "status": "requires_review",
        "source_confidence": "low",
        "quality_score": 0.45,
        "review_notes": f"Requires review because catalogue fallback was used for {reason.replace('_', ' ')}.",
        "metadata": {
            "generator": "CatalogueDiseaseBrief",
            "brief_tier": CATALOGUE_FALLBACK_BRIEF_TIER,
            "fallback_reason": reason,
            "version": 1,
        },
    }


def public_disease_page_exclusion_reason(disease: Any) -> str | None:
    """Explain why a disease should not receive a public-facing disease page."""
    disease_id = _value(disease, "disease_id").upper()
    category = _value(disease, "category").lower()
    name_en = _value(disease, "name_en").lower() or _value(disease, "standard_name_en").lower()
    description = _value(disease, "description").lower()

    if disease_id in NON_PUBLIC_DISEASE_IDS:
        return "non_public_disease_id"
    if category == "summary":
        return "summary_category"
    if name_en in {"total", "summary"}:
        return "summary_name"
    if any(phrase in description for phrase in NON_PUBLIC_DESCRIPTION_PHRASES):
        return "deprecated_or_aggregate_description"
    return None


def should_generate_public_disease_page(disease: Any) -> bool:
    """Return True when a disease should appear in public site/page exports."""
    return public_disease_page_exclusion_reason(disease) is None


def knowledge_brief_publication_tier(brief: Any) -> str:
    """Classify a stored/generated brief as published, fallback, or non-published."""
    status = _value(brief, "status").lower()
    if status != "published":
        return status or "fallback"
    metadata = _brief_metadata(brief)
    if metadata.get("brief_tier") == CATALOGUE_FALLBACK_BRIEF_TIER:
        return "fallback"
    return "published"


def knowledge_brief_fallback_reason(brief: Any) -> str | None:
    metadata = _brief_metadata(brief)
    value = metadata.get("fallback_reason")
    if value in (None, ""):
        return None
    return str(value)


def resolve_disease_knowledge_status(briefs: Iterable[Any]) -> str:
    """Resolve disease-level status from a set of brief rows/payloads."""
    has_review = False
    has_fallback = False

    for brief in briefs:
        tier = knowledge_brief_publication_tier(brief)
        if tier == "published":
            return "published"
        if tier == "fallback":
            has_fallback = True
            continue
        if tier:
            has_review = True

    if has_fallback:
        return "fallback"
    if has_review:
        return "requires_review"
    return "fallback"


def _value(obj: Any, key: str) -> str:
    if isinstance(obj, dict):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    return str(value).strip() if value not in (None, "") else ""


def _metadata_value(obj: Any, key: str) -> str:
    metadata = _brief_metadata(obj)
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get(key)
    return str(value).strip() if value not in (None, "") else ""


def _brief_metadata(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        metadata = obj.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        metadata = obj.get("metadata_")
        if isinstance(metadata, dict):
            return metadata
        return {}
    metadata = getattr(obj, "metadata_", None)
    return metadata if isinstance(metadata, dict) else {}


def _normalize_language(language: str) -> str:
    return "zh" if language == "zh" else "en"


def _category_zh(category: str) -> str:
    mapping = {
        "Bacterial": "细菌性疾病",
        "Viral": "病毒性疾病",
        "Parasitic": "寄生虫病",
        "Fungal": "真菌性疾病",
        "Prion": "朊粒相关疾病",
    }
    return mapping.get(str(category), "传染病监测条目")


def _infer_exposure_context(name: str, description: str, transmission: str, language: str) -> str:
    text = " ".join([name, description, transmission]).lower()
    if transmission:
        return (
            f"本地目录中的传播/暴露字段提示：{transmission}。该信息仍需与可追溯来源核对。"
            if language == "zh"
            else f"The local catalogue transmission/exposure field says: {transmission}. This should still be checked against traceable sources."
        )

    patterns = [
        (
            ("mosquito", "vector", "arboviral", "malaria", "dengue", "yellow fever", "chikungunya", "zika", "encephalitis"),
            "该条目通常需要关注媒介或虫媒传播背景；监测解读时应特别留意季节性、地理聚集和气候/媒介相关变化。",
            "This entry commonly requires vector-borne or arthropod-borne context; surveillance interpretation should pay attention to seasonality, geographic clustering, and vector-related conditions.",
        ),
        (
            ("respiratory", "influenza", "measles", "pertussis", "tuberculosis", "sars", "mers", "covid", "diphtheria", "mumps", "rubella", "varicella"),
            "该条目通常需要关注呼吸道或人与人传播背景；短期增幅、学校/机构聚集和报告延迟会显著影响趋势解读。",
            "This entry commonly requires respiratory or person-to-person transmission context; short-term increases, institutional clusters, and reporting lag can strongly affect trend interpretation.",
        ),
        (
            ("diarrhea", "diarrhoea", "cholera", "dysentery", "typhoid", "paratyphoid", "hepatitis a", "hepatitis e", "food", "water"),
            "该条目通常需要关注食源性、水源性或粪口传播背景；解读时应结合暴发、供水卫生、季节性和报告口径。",
            "This entry commonly requires foodborne, waterborne, or fecal-oral exposure context; interpretation should consider outbreaks, water/sanitation conditions, seasonality, and reporting definitions.",
        ),
        (
            ("zoonotic", "animal", "rabies", "anthrax", "brucellosis", "leptospirosis", "plague", "tularemia", "q fever"),
            "该条目通常需要关注动物、媒介或环境暴露背景；病例聚集可能与职业、动物接触、地区生态或报告发现能力有关。",
            "This entry commonly requires animal, vector, or environmental exposure context; clustering may relate to occupation, animal contact, local ecology, or case-finding capacity.",
        ),
        (
            ("sexually", "sexual", "hiv", "gonorrhea", "syphilis", "chlamydia", "blood"),
            "该条目通常需要关注性传播、血液暴露或重点监测人群背景；趋势可能受筛查覆盖率和报告制度影响。",
            "This entry commonly requires sexual, bloodborne, or sentinel-population context; trends can be affected by screening coverage and reporting practice.",
        ),
    ]
    for keywords, zh_text, en_text in patterns:
        if any(keyword in text for keyword in keywords):
            return zh_text if language == "zh" else en_text
    return (
        "本地目录尚未提供可核验的传播/暴露说明；正式 brief 发布前，本页仅从监测数据角度展示病例、死亡、地区和时间变化。"
        if language == "zh"
        else "The local catalogue does not yet provide a source-checked transmission/exposure description; until a reviewed brief is available, this page focuses on cases, deaths, geography, and time trends."
    )


def _brief_context_sentence(name: str, description: str, transmission: str, language: str) -> str:
    text = " ".join([name, description, transmission]).lower()
    if any(keyword in text for keyword in ("respiratory", "influenza", "measles", "pertussis", "tuberculosis", "sars", "mers", "covid", "diphtheria", "mumps", "rubella", "varicella")):
        return (
            "该条目通常需要结合呼吸道传播、短期聚集、学校或机构暴发、以及报告延迟来解读。"
            if language == "zh"
            else "This entry is typically interpreted with respiratory transmission, short-term clustering, school or institutional outbreaks, and reporting lag in mind."
        )
    if any(keyword in text for keyword in ("mosquito", "vector", "arboviral", "malaria", "dengue", "yellow fever", "chikungunya", "zika", "encephalitis")):
        return (
            "该条目通常需要结合媒介传播、季节性、地理聚集和环境条件来解读。"
            if language == "zh"
            else "This entry is typically interpreted with vector-borne transmission, seasonality, geographic clustering, and environmental conditions in mind."
        )
    if any(keyword in text for keyword in ("diarrhea", "diarrhoea", "cholera", "dysentery", "typhoid", "paratyphoid", "hepatitis a", "hepatitis e", "food", "water")):
        return (
            "该条目通常需要结合食源性、水源性、暴发事件和卫生条件变化来解读。"
            if language == "zh"
            else "This entry is typically interpreted with foodborne and waterborne exposure, outbreak activity, and changes in sanitation conditions in mind."
        )
    if any(keyword in text for keyword in ("zoonotic", "animal", "rabies", "anthrax", "brucellosis", "leptospirosis", "plague", "tularemia", "q fever")):
        return (
            "该条目通常需要结合动物接触、职业暴露、生态环境和暴发发现能力来解读。"
            if language == "zh"
            else "This entry is typically interpreted with animal contact, occupational exposure, ecology, and case-finding capacity in mind."
        )
    if any(keyword in text for keyword in ("sexually", "sexual", "hiv", "gonorrhea", "syphilis", "chlamydia", "blood")):
        return (
            "该条目通常需要结合性传播、血液暴露、筛查覆盖和重点监测人群来解读。"
            if language == "zh"
            else "This entry is typically interpreted with sexual or bloodborne exposure, screening coverage, and sentinel populations in mind."
        )
    return (
        "该条目侧重帮助读者把病例、死亡、发病率和国家差异放在监测背景下阅读。"
        if language == "zh"
        else "This entry is designed to help readers interpret cases, deaths, incidence, and cross-country differences in surveillance context."
    )
