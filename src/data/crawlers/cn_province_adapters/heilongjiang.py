from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-HL", "Heilongjiang, China", "黑龙江省", "230000",
    "https://jkj.hlj.gov.cn/", "html_table",
    archive_index_urls=("https://jkj.hlj.gov.cn/hljjkj/yiqing003/newsList.shtml",),
)
