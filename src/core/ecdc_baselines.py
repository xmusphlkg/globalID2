"""Reviewed EU/EEA and United Kingdom metadata for ECDC annual baselines."""

from __future__ import annotations


# The Atlas uses ISO alpha-2 for every reviewed country except Greece (``EL``)
# and the historical United Kingdom series (``UK``). Canonical GIDS output
# always uses the dictionary key below.
ECDC_BASELINE_COUNTRIES: dict[str, dict[str, str]] = {
    "AT": {"name": "Austria", "name_local": "Österreich", "language": "de-AT", "timezone": "Europe/Vienna", "name_zh": "奥地利"},
    "BE": {"name": "Belgium", "name_local": "België / Belgique / Belgien", "language": "nl-BE", "timezone": "Europe/Brussels", "name_zh": "比利时"},
    "BG": {"name": "Bulgaria", "name_local": "България", "language": "bg-BG", "timezone": "Europe/Sofia", "name_zh": "保加利亚"},
    "HR": {"name": "Croatia", "name_local": "Hrvatska", "language": "hr-HR", "timezone": "Europe/Zagreb", "name_zh": "克罗地亚"},
    "CY": {"name": "Cyprus", "name_local": "Κύπρος / Kıbrıs", "language": "el-CY", "timezone": "Asia/Nicosia", "name_zh": "塞浦路斯"},
    "CZ": {"name": "Czechia", "name_local": "Česko", "language": "cs-CZ", "timezone": "Europe/Prague", "name_zh": "捷克"},
    "DK": {"name": "Denmark", "name_local": "Danmark", "language": "da-DK", "timezone": "Europe/Copenhagen", "name_zh": "丹麦"},
    "EE": {"name": "Estonia", "name_local": "Eesti", "language": "et-EE", "timezone": "Europe/Tallinn", "name_zh": "爱沙尼亚"},
    "FI": {"name": "Finland", "name_local": "Suomi", "language": "fi-FI", "timezone": "Europe/Helsinki", "name_zh": "芬兰"},
    "FR": {"name": "France", "name_local": "France", "language": "fr-FR", "timezone": "Europe/Paris", "name_zh": "法国"},
    "GB": {"name": "United Kingdom", "name_local": "United Kingdom", "language": "en-GB", "timezone": "Europe/London", "name_zh": "英国", "source_geo_code": "UK"},
    "DE": {"name": "Germany", "name_local": "Deutschland", "language": "de-DE", "timezone": "Europe/Berlin", "name_zh": "德国"},
    "GR": {"name": "Greece", "name_local": "Ελλάδα", "language": "el-GR", "timezone": "Europe/Athens", "name_zh": "希腊", "source_geo_code": "EL"},
    "HU": {"name": "Hungary", "name_local": "Magyarország", "language": "hu-HU", "timezone": "Europe/Budapest", "name_zh": "匈牙利"},
    "IS": {"name": "Iceland", "name_local": "Ísland", "language": "is-IS", "timezone": "Atlantic/Reykjavik", "name_zh": "冰岛"},
    "IE": {"name": "Ireland", "name_local": "Éire / Ireland", "language": "en-IE", "timezone": "Europe/Dublin", "name_zh": "爱尔兰"},
    "IT": {"name": "Italy", "name_local": "Italia", "language": "it-IT", "timezone": "Europe/Rome", "name_zh": "意大利"},
    "LV": {"name": "Latvia", "name_local": "Latvija", "language": "lv-LV", "timezone": "Europe/Riga", "name_zh": "拉脱维亚"},
    "LI": {"name": "Liechtenstein", "name_local": "Liechtenstein", "language": "de-LI", "timezone": "Europe/Vaduz", "name_zh": "列支敦士登"},
    "LT": {"name": "Lithuania", "name_local": "Lietuva", "language": "lt-LT", "timezone": "Europe/Vilnius", "name_zh": "立陶宛"},
    "LU": {"name": "Luxembourg", "name_local": "Lëtzebuerg", "language": "lb-LU", "timezone": "Europe/Luxembourg", "name_zh": "卢森堡"},
    "MT": {"name": "Malta", "name_local": "Malta", "language": "mt-MT", "timezone": "Europe/Malta", "name_zh": "马耳他"},
    "NL": {"name": "Netherlands", "name_local": "Nederland", "language": "nl-NL", "timezone": "Europe/Amsterdam", "name_zh": "荷兰"},
    "NO": {"name": "Norway", "name_local": "Norge", "language": "nb-NO", "timezone": "Europe/Oslo", "name_zh": "挪威"},
    "PL": {"name": "Poland", "name_local": "Polska", "language": "pl-PL", "timezone": "Europe/Warsaw", "name_zh": "波兰"},
    "PT": {"name": "Portugal", "name_local": "Portugal", "language": "pt-PT", "timezone": "Europe/Lisbon", "name_zh": "葡萄牙"},
    "RO": {"name": "Romania", "name_local": "România", "language": "ro-RO", "timezone": "Europe/Bucharest", "name_zh": "罗马尼亚"},
    "SK": {"name": "Slovakia", "name_local": "Slovensko", "language": "sk-SK", "timezone": "Europe/Bratislava", "name_zh": "斯洛伐克"},
    "SI": {"name": "Slovenia", "name_local": "Slovenija", "language": "sl-SI", "timezone": "Europe/Ljubljana", "name_zh": "斯洛文尼亚"},
    "ES": {"name": "Spain", "name_local": "España", "language": "es-ES", "timezone": "Europe/Madrid", "name_zh": "西班牙"},
    "SE": {"name": "Sweden", "name_local": "Sverige", "language": "sv-SE", "timezone": "Europe/Stockholm", "name_zh": "瑞典"},
}

ECDC_BASELINE_COUNTRY_CODES = frozenset(ECDC_BASELINE_COUNTRIES)


def source_geo_code(country_code: str) -> str:
    """Return the Atlas geography code for a canonical GIDS country code."""

    code = str(country_code or "").strip().upper()
    return ECDC_BASELINE_COUNTRIES.get(code, {}).get("source_geo_code", code)


__all__ = [
    "ECDC_BASELINE_COUNTRIES",
    "ECDC_BASELINE_COUNTRY_CODES",
    "source_geo_code",
]
