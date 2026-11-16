"""Public admission and attribution for reviewed annual source contracts.

These allow-lists authorize only reviewed source systems. They do not change
captured observations, grant new rights, or enable independent ingestion feeds.
"""

from __future__ import annotations

from src.core.remaining_country_sources import WHO_COUNTRIES, SPF_TERRITORIES

REVIEWED_ANNUAL_COUNTRIES = frozenset((*WHO_COUNTRIES, *SPF_TERRITORIES, "CL", "MY"))
WHO_TERMS_URL = "https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions"
SPF_TERMS_URL = "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"
REVIEW_DATE = "2026-11-16"


def _who_details(code: str, name: str, name_zh: str) -> dict:
    title = f"{name} WHO GHO Annual Reported Infections"
    return {
        "source_id": f"SRC_{code}_WHO_GHO", "label": title,
        "url": "https://www.who.int/data/gho", "type": "official_odata", "cadence": "annual",
        "publisher": "World Health Organization",
        "license": "WHO dataset terms for public health use", "license_url": WHO_TERMS_URL,
        "attribution_required": True,
        "required_attribution": f"World Health Organization (WHO), {title}; underlying data reported by {name}. Original indicator titles and observed years accompany each source series. Accessed {REVIEW_DATE}.",
        "attribution_template": f"WHO, {{original_indicator_title}}, {{observed_year}}, accessed {{access_date}}. Underlying data reported by {name}. Original data: {{source_url}}.",
        "description": (
            f"WHO, {title}; country-reported annual indicators, accessed {REVIEW_DATE}. "
            "Original indicator titles, observed years, values and missing states are retained with each source series. "
            "Only the 18 reviewed count indicators are eligible; actual categories and available years vary by country. "
            "Annual data may lag and do not establish complete national surveillance coverage. "
            "WHO dataset terms permit public-health use with attribution; restrictions include commercial promotion, "
            "misrepresentation and implied endorsement. This independent presentation does not imply WHO endorsement. "
            f"Terms: {WHO_TERMS_URL}"
        ),
        "description_zh": (
            f"来源：WHO，{name_zh} GHO 传染病年度报告指标；访问日期：{REVIEW_DATE}。"
            "各来源序列保留原始指标名称、观测年份、数值及缺失状态。仅限18个已审核计数指标，各国实际类别和年份不同；"
            "年度数据可能滞后，不表示完整国家监测覆盖。按WHO数据集公共卫生使用条款署名，受商业推广、误导表达和暗示背书等限制。"
            f"本独立展示不代表WHO背书。条款：{WHO_TERMS_URL}"
        ),
        "review_status": "public_health_use_terms_and_semantics_reviewed",
        "original_dataset_modification_authorized": False,
    }


def _spf_details(code: str, name: str, name_zh: str) -> dict:
    title = f"{name} SPF ODISSE Annual Notifications"
    return {
        "source_id": f"SRC_{code}_SPF_ODISSE", "label": title,
        "url": "https://odisse.santepubliquefrance.fr/", "type": "official_open_data", "cadence": "annual",
        "publisher": "Santé publique France", "license": "Licence ouverte 2.0", "license_url": SPF_TERMS_URL,
        "attribution_required": True,
        "required_attribution": f"Santé publique France, {title}; original dataset titles and observed years accompany each source series. Extracted {REVIEW_DATE}. Licence ouverte 2.0.",
        "attribution_template": f"Santé publique France, {{original_dataset_title}}, {name}, {{observed_year}}, extracted {{access_date}}. Licence ouverte 2.0. Original data: {{source_url}}.",
        "description": (
            f"Santé publique France, {title}, extracted {REVIEW_DATE}. Licence ouverte 2.0, declared by the five source dataset metadata records. "
            "Original dataset titles, observed years and URLs accompany source series. Only source-reported all-age/all-type totals enter territory curves; "
            "age and serogroup components remain separate. TB notification-location observations through 2018 and residence-location observations "
            "from 2019 have separate definitions: the representative curve may use only the residence series, with older data retained in source details. "
            "Missing territory-years are unknown. Territory observations are not added to France national totals. No publisher endorsement is implied."
        ),
        "description_zh": (
            f"来源：Santé publique France，{name_zh} ODISSE年度疾病报告；提取日期：{REVIEW_DATE}。五套来源元数据声明Licence ouverte 2.0。"
            "来源序列保留原始数据集标题、观测年份及链接。地区曲线仅使用来源明确报告的全龄/全型总计，年龄和血清群分项分别保留。"
            "结核2018年及以前报告地与2019年起居住地是不同定义：代表曲线可能仅使用居住地序列，旧数据保留于来源明细。"
            "缺失地区年份保持未知，地区数据不自动计入法国全国总量。本展示不代表发布机构背书。"
        ),
        "review_status": "open_licence_metadata_and_semantics_reviewed",
    }


def reviewed_annual_admission_config(code: str) -> dict:
    """Return a fresh bootstrap overlay, leaving unreviewed feeds disabled."""
    details: dict[str, dict]
    if code in WHO_COUNTRIES:
        name, zh, *_ = WHO_COUNTRIES[code]
        details = {"who_gho_annual": _who_details(code, name, zh)}
    elif code in SPF_TERRITORIES:
        name, zh, *_ = SPF_TERRITORIES[code]
        details = {"spf_odisse_annual": _spf_details(code, name, zh)}
    elif code == "MY":
        details = {
            "moh_std_annual": {
                "source_id": "SRC_MY_MOH_STD", "label": "Malaysia MOH Annual STD Notifications",
                "url": "https://data.gov.my/data-catalogue/std_state", "type": "official_parquet", "cadence": "annual",
                "publisher": "Ministry of Health Malaysia", "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/", "attribution_required": True,
                "required_attribution": f"Ministry of Health Malaysia, Annual STD Notifications (std_state), 2017–2024, accessed {REVIEW_DATE}. CC BY 4.0. National rows selected; source values preserved.",
                "description": f"Ministry of Health Malaysia, Annual STD Notifications (std_state), accessed {REVIEW_DATE}. CC BY 4.0. National annual report-date counts for AIDS, chancroid, gonorrhea, HIV and syphilis, 2017–2024. Only explicit Malaysia rows are selected; state rows are not added. HIV and AIDS stay separate, rates do not replace counts, and missing values are unknown. Broader national disease coverage remains incomplete.",
                "description_zh": f"来源：马来西亚卫生部，std_state年度性传播疾病报告；访问日期：{REVIEW_DATE}。CC BY 4.0。2017–2024年AIDS、软下疳、淋病、HIV和梅毒的全国年度报告日期计数，仅选取明确标为Malaysia的全国行，不累加州数据。HIV与AIDS分别保留，发病率不替代计数，缺失值保持未知。尚未覆盖完整国家传染病监测。",
                "review_status": "cc_by_4_0_and_national_annual_semantics_reviewed",
            },
            "who_gho_annual": _who_details("MY", "Malaysia", "马来西亚"),
        }
    elif code == "CL":
        details = {"cl_eno_historical": {
            "source_id": "SRC_CL_ENO", "label": "Chile MINSAL Historical ENO Notifications",
            "url": "https://datos.gob.cl/dataset/notificaciones-eno", "type": "official_csv", "cadence": "annual",
            "publisher": "Ministerio de Salud de Chile", "license": "CC0 (official CKAN catalogue declaration)",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "attribution_required": False,
            "required_attribution": f"Ministerio de Salud de Chile, Notificaciones ENO, accessed {REVIEW_DATE}; national confirmed annual aggregates derived from the captured CSV, 2007–2024.",
            "description": f"Ministerio de Salud de Chile, Notificaciones ENO, accessed {REVIEW_DATE}. The official CKAN catalogue declares CC0. Only reviewed confirmed national annual notification aggregates are presented; individual case rows are not released. The current CSV covers 2007–2024 and a changed disease inventory, while the catalogue text and methodology PDF still describe 2007–2022 and older exclusions. This upstream discrepancy is retained in the source audit; the older PDF is not asserted to describe the current CSV. Notification year is not onset year. Source-system periods and classifications remain separate; absent disease-years are unknown.",
            "description_zh": f"来源：智利卫生部，Notificaciones ENO；访问日期：{REVIEW_DATE}。官方CKAN目录声明CC0。仅展示已审核的全国确诊年度通知汇总，不公开个案记录。当前CSV覆盖2007–2024年并含变化后的病种目录，而目录文字和方法PDF仍描述2007–2022年及旧排除范围；该上游差异保留审计，不将旧PDF视为当前CSV的完整说明。通知年份不等于发病年份，源系统时期和分类分别保留，缺失病种年份保持未知。",
            "review_status": "cc0_catalogue_and_confirmed_national_aggregate_reviewed",
        }}
    else:
        return {}
    policies = {
        scope: {
            "public_release_enabled": True, "license": item["license"],
            "license_url": item["license_url"], "publisher": item["publisher"],
            "required_attribution": item["required_attribution"], "review_status": item["review_status"],
        }
        for scope, item in details.items()
    }
    # A country flag must never admit a separate, unreviewed source system.
    return {
        "public_release_enabled": True, "public_legacy_enabled": False,
        "public_source_systems": [item["source_id"] for item in details.values()],
        "public_source_scopes": list(details), "public_source_details": details,
        "public_source_notes": " ".join(item["description"] for item in details.values()),
        "crawler_config": {"source_policies": policies},
    }
