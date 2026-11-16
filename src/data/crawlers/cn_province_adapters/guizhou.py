from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-GZ", "Guizhou, China", "贵州省", "520000",
    "https://jkj.guizhou.gov.cn/zwgk/zfxxgk/fdzdgknr/yqxx/", "table_or_attachment",
    archive_index_urls=("https://wjw.guizhou.gov.cn/zwgk/zdlyxx/ybyk/",),
)
