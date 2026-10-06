from __future__ import annotations

from src.data.crawlers.au import AustraliaNINDSSCrawler


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {
            "results": [
                {
                    "result": {
                        "data": {
                            "dsr": {
                                "DS": [
                                    {
                                        "PH": [
                                            {
                                                "DM0": [
                                                    {"S": [{"N": "G0"}], "C": ["Anthrax"]},
                                                    {"C": ["Cholera"]},
                                                    {"G0": "Dengue"},
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            ]
        }


class _Session:
    def post(self, *_args, **_kwargs) -> _Response:
        return _Response()


def test_dax_list_accepts_current_power_bi_cell_array_encoding() -> None:
    crawler = AustraliaNINDSSCrawler()
    crawler._config = {
        "accessToken": "token",
        "apiUrl": "https://example.test/query",
        "reportId": "report",
        "datasetId": "dataset",
        "modelId": 1,
    }
    crawler._http = _Session()

    assert crawler.get_all_diseases() == ["Anthrax", "Cholera", "Dengue"]
