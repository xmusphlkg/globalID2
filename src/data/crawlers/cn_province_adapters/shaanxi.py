from .base import ProvinceSourceConfig

INDEX = "https://sxwjw.shaanxi.gov.cn/zfxxgk/fdzdgknr/zdgz/ggwsxx/"

ADAPTER = ProvinceSourceConfig(
    "CN-SN", "Shaanxi, China", "陕西省", "610000",
    INDEX, "table_or_attachment",
    # The commission publishes monthly reports and their original image tables.
    # Pagination is emitted by createPage(), so requests need an explicit template.
    pagination_url_template=INDEX + "index_{page}.html",
    max_index_pages=10,
    report_title_contains=("陕西省",),
)
