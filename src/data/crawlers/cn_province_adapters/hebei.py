from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-HE", "Hebei, China", "河北省", "130000",
    "https://www.hebeicdc.cn/sn/index.jhtml", "table_or_attachment",
    archive_index_urls=tuple(
        f"https://www.hebeicdc.cn/sn/index_{page}.jhtml"
        for page in range(2, 5)
    ),
)
