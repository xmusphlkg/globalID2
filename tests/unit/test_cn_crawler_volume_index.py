from __future__ import annotations

from src.data.crawlers.cn import ChinaCDCCrawler


def test_parse_cdc_weekly_volume_extracts_monthly_reports() -> None:
    payload = {
        "data": {
            "volumeArticles": {
                "issue": [
                    {
                        "titleEn": (
                            "Reported Cases and Deaths of National Notifiable "
                            "Infectious Diseases — China, May 2026*"
                        ),
                        "doi": "10.46234/ccdcw2026.155",
                        "id": "article-id",
                        "articleNo": "report2026-5",
                        "issue": "30",
                    },
                    {
                        "titleEn": "An unrelated surveillance article",
                        "doi": "10.46234/ccdcw2026.999",
                    },
                ]
            }
        }
    }

    results = ChinaCDCCrawler().parse_cdc_weekly_volume(payload)

    assert len(results) == 1
    assert results[0].year_month == "2026 May"
    assert results[0].url == (
        "https://weekly.chinacdc.cn/en/article/doi/10.46234/ccdcw2026.155"
    )
    assert results[0].metadata["doi"] == "10.46234/ccdcw2026.155"
    assert results[0].raw_data["article_no"] == "report2026-5"


def test_parse_cdc_weekly_volume_ignores_incomplete_articles() -> None:
    payload = {
        "titleEn": (
            "Reported Cases and Deaths of National Notifiable Infectious "
            "Diseases — China, May 2026"
        )
    }

    assert ChinaCDCCrawler().parse_cdc_weekly_volume(payload) == []
