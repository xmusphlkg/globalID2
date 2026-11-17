from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-AH", "Anhui, China", "安徽省", "340000",
    "https://www.ahlx.gov.cn/Jczwgk/showList/1/125024000/page_1.html", "docx_attachment",
    archive_index_urls=tuple(
        f"https://www.ahlx.gov.cn/Jczwgk/showList/1/125024000/page_{page}.html"
        for page in range(2, 5)
    ),
)
