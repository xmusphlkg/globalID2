from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-BJ", "Beijing, China", "北京市", "110000",
    "https://wjw.beijing.gov.cn/bmfw_20143/jkzs/yqxx/index.html", "table_or_attachment",
    archive_index_urls=("https://wjw.beijing.gov.cn/bmfw_20143/jkzs/yqxx/index_1.html",),
    pagination_url_template="https://wjw.beijing.gov.cn/bmfw_20143/jkzs/yqxx/index_{page}.html",
    max_index_pages=3,
)
