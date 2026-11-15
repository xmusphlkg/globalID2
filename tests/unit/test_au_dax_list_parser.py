from __future__ import annotations

import json

from src.data.crawlers.au import AustraliaNINDSSCrawler


def test_location_query_uses_calendar_month_and_powerbi_quarter_spacing() -> None:
    from src.data.crawlers.au import _month_name, _quarter_for_month

    crawler = AustraliaNINDSSCrawler()
    crawler._config = {"datasetId": "dataset", "reportId": "report", "modelId": 1}
    payload = crawler._build_location_payload(
        "2026", _quarter_for_month(12), _month_name(12), "Anthrax",
    )
    assert _month_name(1) == "January"
    command = payload["queries"][0]["Query"]["Commands"][0]["SemanticQueryDataShapeCommand"]
    periods = command["Query"]["Where"][2]["Condition"]["In"]["Values"][0]
    assert [item["Literal"]["Value"] for item in periods] == ["'2026'", "'Quarter  4'", "'December'"]


class _Response:
    @property
    def content(self):
        return json.dumps(self.json()).encode()

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
