from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-SH", "Shanghai, China", "上海市", "310000",
    "https://wsjkw.sh.gov.cn/yqxx/index.html", "html_table",
    archive_index_urls=tuple(
        f"https://wsjkw.sh.gov.cn/yqxx/index_{page}.html"
        for page in range(2, 11)
    ),
    max_index_pages=10,
)
