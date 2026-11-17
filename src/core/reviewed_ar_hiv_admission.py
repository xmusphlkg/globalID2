"""Source-scoped public admission for Argentina's annual HIV notifications."""

from src.core.ar_hiv_source import (
    CATALOGUE_URL, CSV_URL, DEFINITION_VERSION, LICENSE_URL, SOURCE_ID,
    SOURCE_NAME, SOURCE_SCOPE,
)


def reviewed_ar_hiv_admission_config(code: str) -> dict:
    """Admit only direct Ministry of Health totals; retain BEN as holding data."""
    if code != "AR":
        return {}
    details = {
        "source_id": SOURCE_ID, "label": SOURCE_NAME,
        "url": CATALOGUE_URL, "machine_url": CSV_URL,
        "type": "official_csv", "cadence": "annual",
        "publisher": "Ministerio de Salud de la Nación Argentina",
        "license": "CC BY 4.0 (official resource metadata)",
        "license_url": LICENSE_URL, "attribution_required": True,
        "required_attribution": (
            "Ministerio de Salud de la Nación Argentina, Casos notificados de VIH "
            "por año y jurisdicción, 2014–2023, national ARG / 200, Ambos sexos. "
            f"CC BY 4.0. Original resource: {CSV_URL}. Accessed 2026-11-16. "
            "National source totals selected without summing provincial or sex strata."
        ),
        "description": (
            "Argentina Ministry of Health annual HIV notifications, 2014–2023. "
            "Each value is the source's explicit national ARG / 200 total for Ambos sexos; "
            "province and sex rows are not added. Notifications are not estimates of "
            "incident infections, prevalence or AIDS cases. Annual dates denote calendar "
            "years, not individual event dates; unknown values stay unknown. "
            "CC BY 4.0 is declared by the official resource metadata. The catalogue's "
            "older package description differs from the selected 2014–2023 resource. "
            "No 2024–2026 observations are supplied by this resource. "
            "BEN cumulative notifications remain a separate source and do not enter this curve."
        ),
        "description_zh": (
            "来源：阿根廷国家卫生部，2014–2023 年 HIV 年度通知数。每个数值直接取自来源明确给出的 "
            "ARG／200 全国、Ambos sexos 双性别合计行，不累加省份或性别分项。通知数不等于新感染估计、"
            "患病存量或 AIDS 病例；年度日期表示自然年，不是个案发生日期，未知值保持未知。"
            "官方资源元数据声明 CC BY 4.0；目录包较早的描述与所选 2014–2023 年原件范围不同。"
            "该资源尚无 2024–2026 年观测。BEN 累计通知保持独立来源，不混入本曲线。"
        ),
        "description_fr": (
            "Notifications annuelles de VIH du ministère argentin de la Santé, 2014–2023. "
            "Chaque valeur est le total national explicite ARG / 200 pour Ambos sexos ; "
            "les provinces et les sexes ne sont pas additionnés. Ces notifications ne "
            "mesurent ni les nouvelles infections estimées, ni la prévalence, ni les cas "
            "de sida. Les dates désignent des années civiles, pas des dates individuelles ; "
            "les valeurs inconnues restent inconnues. Les métadonnées officielles de la "
            "ressource déclarent CC BY 4.0. L'ancienne description du catalogue diffère "
            "de la ressource 2014–2023 sélectionnée, qui ne fournit aucune observation "
            "pour 2024–2026. Les cumuls BEN restent une source séparée, exclue de cette courbe."
        ),
        "review_status": "cc_by_4_0_and_national_annual_semantics_reviewed",
    }
    return {
        "public_release_enabled": True, "public_legacy_enabled": False,
        "data_source_url": CATALOGUE_URL, "data_source_type": "official_csv",
        "public_source_systems": [SOURCE_ID],
        "public_source_scopes": [SOURCE_SCOPE],
        "public_source_details": {SOURCE_SCOPE: details},
        "public_source_notes": details["description"],
        "crawler_config": {
            "sources": [SOURCE_SCOPE, "ar_ben_weekly"], "cadence": "mixed",
            "source_update_cadence": "source_specific_revisable",
            "reuse_status": "source_specific_terms", "source_policies": {
                SOURCE_SCOPE: {
                    "source_id": SOURCE_ID, "cadence": "annual",
                    "source_update_cadence": "annual_revisable",
                    "public_release_enabled": True, "reuse_status": "cc_by_4_0",
                    "source_license_verified": True, "full_history_start_year": 2014,
                    "supports_start_year": True, "supports_fill_missing": False,
                    "definition_version": DEFINITION_VERSION,
                    "official_channel_url": CATALOGUE_URL,
                },
                "ar_ben_weekly": {
                    "source_id": "SRC_AR_BEN", "cadence": "weekly",
                    "public_release_enabled": False,
                    "public_release_hold_reason": "Cumulative source snapshots are not single-week incident counts.",
                },
            },
        },
        "parser_config": {"primary": SOURCE_SCOPE},
        "notes": details["description"],
    }
