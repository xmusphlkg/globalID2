from .base import ProvinceSourceConfig

ADAPTER = ProvinceSourceConfig(
    "CN-JX", "Jiangxi, China", "江西省", "360000",
    "https://www.jxcdc.cn/f/getArticleByCategory", "html_table",
    index_post_data=(("categoryId", "680a2925e9824b12b967b10530f8adc3"), ("pageSize", "20")),
    index_json_article_url_template="https://www.jxcdc.cn/f/newsDetail?id={id}",
    report_title_contains=("江西省",),
)
