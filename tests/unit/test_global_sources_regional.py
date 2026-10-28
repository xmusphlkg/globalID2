"""Offline tests for the Africa CDC and PAHO adapters."""

from __future__ import annotations

from src.services.global_sources import africa_cdc, paho


def _wp(item_id: int, title: str, day: str = "2026-10-20T08:00:00") -> dict:
    return {
        "id": item_id,
        "date_gmt": day,
        "modified_gmt": day,
        "link": f"https://africacdc.org/news-item/item-{item_id}/",
        "title": {"rendered": title},
    }


def test_africa_cdc_news_needs_a_disease_and_decodes_entities():
    rows = [
        _wp(1, "Africa CDC Reaffirms Support to Kenya Following Confirmation of Four Additional Ebola Cases"),
        _wp(2, "Africa CDC and Ethiopian Airlines Partner to Offer Airfare Discount"),
        _wp(3, "Africa CDC&#8217;s Ebola Response in the Democratic Republic of the Congo"),
    ]
    events = africa_cdc.normalize_payload(rows, kind="news")
    assert [e["native_id"] for e in events] == ["news-1", "news-3"]
    assert events[0]["countries"][0]["code"] == "KE"
    assert events[0]["record_type"] == "outbreak_statement"
    assert "’" in events[1]["title_en"] and "&#8217;" not in events[1]["title_en"]
    assert events[1]["summary_en"] is None  # metadata-only source


def test_africa_cdc_drops_french_duplicates():
    rows = [
        _wp(1, "Ebola: Africa CDC Convenes African Ministers of Health"),
        _wp(2, "Ebola: Africa CDC réunit les ministres africains de la Santé face à l’extension de l’épidémie"),
    ]
    assert [e["native_id"] for e in africa_cdc.normalize_payload(rows, kind="news")] == ["news-1"]


def test_africa_cdc_weekly_report_is_kept_without_a_disease():
    rows = [_wp(9, "Africa CDC Epidemic Intelligence Weekly Report, October 2026")]
    events = africa_cdc.normalize_payload(rows, kind="report")
    assert len(events) == 1
    assert events[0]["record_type"] == "weekly_threat_report"


def _dspace(handle: str, title: str, *, issued="2026-10-22", kind="Newsletters", langs=("en", "es"), subjects=()):
    return {
        "_embedded": {
            "indexableObject": {
                "handle": handle,
                "withdrawn": False,
                "lastModified": f"{issued}T12:00:00Z",
                "metadata": {
                    "dc.title": [{"value": title}],
                    "dc.date.issued": [{"value": issued}],
                    "dc.type": [{"value": kind}],
                    "dc.language.iso": [{"value": lang} for lang in langs],
                    "dc.identifier.uri": [{"value": f"https://iris.paho.org/handle/{handle}"}],
                    "dc.subject": [{"value": s} for s in subjects],
                },
            }
        }
    }


def _payload(*objects) -> dict:
    return {"_embedded": {"searchResult": {"_embedded": {"objects": list(objects)}}}}


def test_paho_keeps_only_english_surveillance_newsletters():
    payload = _payload(
        _dspace(
            "10665.2/72496",
            "Regional Update, Influenza and Other Respiratory Viruses. Epidemiological Week 38 (2 October 2026)",
            subjects=("Influenza, Human", "COVID-19"),
        ),
        _dspace("10665.2/1", "Factores asociados a fiebre amarilla en Colombia", kind="Journal articles", langs=("es",)),
        _dspace("10665.2/2", "Immunization Newsletter, v.48, n.2, August 2026"),
        _dspace("10665.2/3", "Epidemiological Alert: Measles in Mexico", langs=("es",)),
        _dspace("10665.2/4", "Epidemiological Alert: Yellow fever in Colombia (1 October 2026)"),
    )
    events = paho.normalize_payload(payload)
    assert [e["native_id"] for e in events] == ["10665.2-72496", "10665.2-4"]
    regional, alert = events
    assert regional["record_type"] == "surveillance_update"
    assert regional["url"] == "https://iris.paho.org/handle/10665.2/72496"
    assert {d["name_en"].lower() for d in regional["diseases"]} >= {"influenza", "covid-19"}
    assert alert["record_type"] == "outbreak_notice"
    assert [c["code"] for c in alert["countries"]] == ["CO"]
    assert alert["summary_en"] is None
