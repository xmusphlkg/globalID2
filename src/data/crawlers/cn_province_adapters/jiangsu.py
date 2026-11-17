from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-JS", "Jiangsu, China", "江苏省", "320000",
    "https://wjw.jiangsu.gov.cn/col/col49513/index.html?number=", "doc_attachment",
    index_xml_records=True,
)
