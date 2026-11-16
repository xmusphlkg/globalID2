from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-XJ", "Xinjiang, China", "新疆维吾尔自治区", "650000",
    "https://jkj.xinjiang.gov.cn/xjjbyfkzj/c115745/zfxxgk_list.shtml", "table_or_attachment",
    archive_index_urls=("https://wjw.xinjiang.gov.cn/hfpc/jbjcypj/nav_list.shtml",),
    report_title_contains=("新疆维吾尔自治区",),
)
