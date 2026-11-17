"""Lightweight contract shared by all Chinese province adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProvinceSourceConfig:
    """A province-owned source definition consumed by the shared crawler."""

    code: str
    name_en: str
    name_zh: str
    adcode: str
    index_url: str
    parser: str
    timezone: str = "Asia/Shanghai"
    archive_index_urls: tuple[str, ...] = ()
    index_post_data: tuple[tuple[str, str], ...] = ()
    # Only follow pagination within the configured column, never site-wide
    # navigation. Templates are explicit, audited publisher conventions.
    pagination_url_template: str = ""
    max_index_pages: int = 6
    report_title_contains: tuple[str, ...] = ()
    # JSON listing contract: {list: [{id, title}], count: total}; requests
    # repeat the configured category with only this page field changing.
    index_json_article_url_template: str = ""
    index_page_parameter: str = "pageNo"
    index_json_html_path: tuple[str, ...] = ()
    index_xml_records: bool = False

    @property
    def parent_country_code(self) -> str:
        return "CN"

    @property
    def geography_key(self) -> str:
        return f"country:{self.code}:national"
