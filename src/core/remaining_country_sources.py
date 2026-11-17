"""Country contracts for the remaining regional surveillance integrations.

WHO releases are country-reported annual indicators. They do not certify a
complete national notifiable-disease feed or replace a national primary source.
"""
from __future__ import annotations

WHO_COUNTRIES = {
    "ET": ("Ethiopia", "埃塞俄比亚", "am-ET", "Africa/Addis_Ababa"),
    "KE": ("Kenya", "肯尼亚", "en-KE", "Africa/Nairobi"),
    "UG": ("Uganda", "乌干达", "en-UG", "Africa/Kampala"),
    "GH": ("Ghana", "加纳", "en-GH", "Africa/Accra"),
    "TZ": ("Tanzania", "坦桑尼亚", "sw-TZ", "Africa/Dar_es_Salaam"),
    "PH": ("Philippines", "菲律宾", "en-PH", "Asia/Manila"),
    "VN": ("Viet Nam", "越南", "vi-VN", "Asia/Ho_Chi_Minh"),
    "LA": ("Lao PDR", "老挝", "lo-LA", "Asia/Vientiane"),
    "MM": ("Myanmar", "缅甸", "my-MM", "Asia/Yangon"),
    "BN": ("Brunei Darussalam", "文莱", "ms-BN", "Asia/Brunei"),
    "TL": ("Timor-Leste", "东帝汶", "pt-TL", "Asia/Dili"),
    "PY": ("Paraguay", "巴拉圭", "es-PY", "America/Asuncion"),
    "EC": ("Ecuador", "厄瓜多尔", "es-EC", "America/Guayaquil"),
    "CR": ("Costa Rica", "哥斯达黎加", "es-CR", "America/Costa_Rica"),
    "PA": ("Panama", "巴拿马", "es-PA", "America/Panama"),
}
SPF_TERRITORIES = {
    "GP": ("Guadeloupe", "瓜德罗普", "fr-GP", "America/Guadeloupe"),
    "MQ": ("Martinique", "马提尼克", "fr-MQ", "America/Martinique"),
    "GF": ("French Guiana", "法属圭亚那", "fr-GF", "America/Cayenne"),
}
REMAINING_COUNTRIES = {**WHO_COUNTRIES, **SPF_TERRITORIES}

EXTRA_SOURCE_CONTRACTS = {
    ("CR", "cr_moh_notifications"): {
        "source_id": "SRC_CR_MOH_NOTIFICATIONS",
        "label_en": "Costa Rica Ministry of Health Notification Reports",
        "label_zh": "哥斯达黎加卫生部疾病报告",
        "source_kind": "national_primary", "start_year": 2014,
        "module": "cr_notifications", "updater": "CostaRicaNotificationsUpdater",
        "official_url": "https://www.ministeriodesalud.go.cr/",
    },
    ("LA", "who_wpro_dengue"): {
        "source_id": "SRC_LA_WHO_WPRO_DENGUE",
        "label_en": "Lao PDR WHO WPRO Dengue Updates",
        "label_zh": "老挝 WHO 西太区登革热监测",
        "source_kind": "international_surveillance", "start_year": 2015,
        "module": "wpro_dengue", "updater": "WPRODengueUpdater",
        "official_url": "https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue",
    },
    ("VN", "who_wpro_dengue"): {
        "source_id": "SRC_VN_WHO_WPRO_DENGUE",
        "label_en": "Viet Nam WHO WPRO Dengue Updates",
        "label_zh": "越南 WHO 西太区登革热监测",
        "source_kind": "international_surveillance", "start_year": 2015,
        "module": "wpro_dengue", "updater": "WPRODengueUpdater",
        "official_url": "https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue",
    },
}


def source_contract(code: str, scope: str | None = None) -> dict:
    code = code.upper()
    name, zh, language, timezone = REMAINING_COUNTRIES[code]
    who = code in WHO_COUNTRIES
    contract = {
        "country_code": code, "name": name, "name_zh": zh,
        "language": language, "timezone": timezone,
        "scope": "who_gho_annual" if who else "spf_odisse_annual",
        "source_id": f"SRC_{code}_WHO_GHO" if who else f"SRC_{code}_SPF_ODISSE",
        "label_en": (f"{name} WHO GHO Annual Reported Infections" if who
                     else f"{name} SPF ODISSE Annual Notifications"),
        "label_zh": (f"{zh} WHO GHO 传染病年度报告指标" if who
                     else f"{zh} SPF ODISSÉ 年度疾病报告"),
        "source_kind": "international_surveillance" if who else "national_primary",
        "start_year": 1900 if who else 1995,
        "official_url": ("https://www.who.int/data/gho" if who else
                         "https://odisse.santepubliquefrance.fr/"),
        "archive_url": ("https://ghoapi.azureedge.net/api/" if who else
                        "https://odisse.santepubliquefrance.fr/api/explore/v2.1/catalog/datasets"),
    }
    if scope is not None and scope != contract["scope"]:
        extra = EXTRA_SOURCE_CONTRACTS[(code, scope)]
        contract.update(extra, scope=scope)
        contract["archive_url"] = extra["official_url"]
    return contract
