from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-HN", "Hunan, China", "湖南省", "430000",
    "http://wjw.hunan.gov.cn/wjw/xxgk/yqsj/", "doc_attachment",
    archive_index_urls=(
        "http://wjw.hunan.gov.cn/xxgk/yqsj/index_2.html",
        "http://wjw.hunan.gov.cn/xxgk/yqsj/index_3.html",
    ),
)
