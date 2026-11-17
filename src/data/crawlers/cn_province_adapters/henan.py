from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-HA", "Henan, China", "河南省", "410000",
    "https://jkj.henan.gov.cn/zfxxgk/fdgknr/hnsfdcrbyqxxbg/", "table_or_attachment",
    archive_index_urls=("https://wsjkw.henan.gov.cn/zfxxgk/yqxx/index.html",),
)
