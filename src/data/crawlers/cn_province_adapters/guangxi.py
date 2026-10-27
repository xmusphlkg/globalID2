from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-GX", "Guangxi, China", "广西壮族自治区", "450000",
    "https://wsjkw.gxzf.gov.cn/xxgk_49493/fdzdgk/ggws/fdcrbyqgb/yqyb/", "attachment_auto",
    archive_index_urls=(
        "https://wsjkw.gxzf.gov.cn/xxgk_49493/fdzdgk/ggws/fdcrbyqgb/yqyb/index_1.shtml",
    ),
)
