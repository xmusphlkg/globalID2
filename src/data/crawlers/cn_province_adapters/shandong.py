"""Shandong CDC Bureau's public epidemic-information listing.

The official page renders this public JSON/HTML endpoint. Keep the published
site, unit and epidemic-column identifiers fixed while paging the result.
"""
import json
from urllib.parse import urlencode

from .base import ProvinceSourceConfig


def _listing_url(page: int | str) -> str:
    query = {
        "parseType": "bulidstatic", "unitType": "ajax-xxgk",
        "webId": "ZMtE9Ry7mLFIB3cmB0F7I", "tplSetId": "jt7VdsZUO0sJ7QikkLVvK",
        "pageType": "column", "tagId": "组配分类list", "editType": "null",
        "pageId": "W70qh5Rz6KcBlq1mH8pqT",
        "paramJson": json.dumps({
            "pageNo": page, "pageSize": 20,
            "search": json.dumps({"xxgkId": "SDJB-25-03", "xxgkType": "xxgk_combination", "className": ""}),
        }),
    }
    return "http://jkj.shandong.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit?" + urlencode(query)


ADAPTER = ProvinceSourceConfig(
    "CN-SD", "Shandong, China", "山东省", "370000",
    _listing_url(1), "table_or_attachment",
    pagination_url_template=_listing_url("__PAGE__").replace("%22__PAGE__%22", "{page}"),
    index_json_html_path=("data", "html"),
    max_index_pages=3,
    report_title_contains=("山东省",),
)
