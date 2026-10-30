from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-SD", "Shandong, China", "山东省", "370000",
    "https://www.weihai.gov.cn/module/xxgk/search.jsp", "narrative_only",
    index_post_data=(("standardXxgk", "1"), ("infotypeId", "WHA212104"),
                     ("jdid", "78"), ("divid", "div74499"), ("area", ""), ("currpage", "1")),
)
