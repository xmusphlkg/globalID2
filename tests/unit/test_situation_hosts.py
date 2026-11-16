"""Host evidence, identity isolation and public export stay separate from counts."""
from datetime import date

import pytest

from src.domain import SituationAlert, SituationNotice
from src.services.situation_notices.ai import sanitize_extraction
from src.services.situation_notices.export import alert_record, latest_per_place, map_marker
from src.services.situation_notices.pipeline import alert_id, build_alerts, cluster_alerts, preserve_alert_ids

DAY = date(2026, 11, 1)
HUMAN = "Two human cases of avian influenza were reported in Kenya."
ANIMAL = "Two cases of avian influenza in wild birds were reported in Kenya."


def alert(quote=HUMAN, **values):
    return {"disease_id": "D014", "disease_label": "Avian influenza", "country_code": "KE",
            "admin_area": None, "status": "new", "cases": 2, "deaths": None,
            "quote": quote, "summary_en": quote, **values}


def clean(*alerts, text=None):
    payload, notes = sanitize_extraction({"document_type": "event_report", "alerts": list(alerts)},
        document_text=text or " ".join(item["quote"] for item in alerts), published=DAY,
        allowed_disease_ids={"D014"}, allowed_country_codes={"KE"})
    return payload["alerts"], notes


def test_missing_metadata_never_classifies_a_disease_name_as_human_or_animal():
    (row,), _ = clean(alert())
    assert (row["host_type"], row["count_unit"], row["host_species"], row["host_evidence"]) == ("unknown", "unknown", None, None)


@pytest.mark.parametrize("metadata", [
    {"host_type": "animal", "host_species": "wild birds", "host_evidence": "wild birds", "count_unit": "animals"},
    {"host_type": "animal", "host_species": "avian influenza", "host_evidence": "avian influenza", "count_unit": "animals"},
    {"host_type": "human", "host_evidence": "human patients", "count_unit": "people"},
])
def test_host_support_must_be_in_this_alert_quote_and_identify_a_host(metadata):
    (row,), notes = clean(alert(**metadata))
    assert row["host_type"] == "unknown" and row["count_unit"] == "unknown"
    assert any("host not supported" in note for note in notes)


def test_humans_and_birds_in_the_same_report_are_separate_alerts():
    rows, notes = clean(
        alert(host_type="human", host_evidence="human cases", count_unit="people"),
        alert(ANIMAL, host_type="animal", host_species="wild birds", host_evidence="wild birds", count_unit="animals"),
    )
    assert notes == [] and len(rows) == 2
    assert [(row["host_type"], row["count_unit"]) for row in rows] == [("human", "people"), ("animal", "animals")]
    notice = SituationNotice(notice_id="n1", source_id="who_don", published_date=DAY)
    stored = build_alerts(notice, {"alerts": rows})
    assert stored[0].alert_id != stored[1].alert_id
    assert stored[1].host_species == "wild birds"
    clustered = cluster_alerts([{
        "alert_id": row.alert_id, "notice_id": row.notice_id, "source_id": row.source_id,
        "published_date": DAY, "disease_id": row.disease_id, "disease_label": row.disease_label,
        "country_code": row.country_code, "status": row.status,
        "host_type": row.host_type, "host_species": row.host_species,
    } for row in stored], as_of=DAY)
    assert len(clustered) == 2


def test_unsupported_or_conflicting_units_stay_unknown():
    (row,), _ = clean(alert(host_type="human", host_evidence="human cases", count_unit="animals"))
    assert row["host_type"] == "human" and row["count_unit"] == "unknown"
    (row,), _ = clean(alert(cases=None, host_type="human", host_evidence="human cases", count_unit="people"))
    assert row["count_unit"] == "unknown"


def test_a_patients_poultry_exposure_does_not_establish_an_animal_case():
    quote = "Two human cases had contact with infected poultry in Kenya."
    (row,), notes = clean(alert(quote, host_type="animal", host_species="poultry",
                               host_evidence="infected poultry", count_unit="animals"))
    assert row["host_type"] == "unknown" and row["count_unit"] == "unknown"
    assert any("host not supported" in note for note in notes)


def test_legacy_alert_id_remains_stable_but_explicit_hosts_and_species_are_distinct():
    legacy = alert_id("n1", "D014", "KE", None)
    assert legacy == alert_id("n1", "D014", "KE", None, "unknown")
    values = {legacy, alert_id("n1", "D014", "KE", None, "human"),
              alert_id("n1", "D014", "KE", None, "animal", "wild birds"),
              alert_id("n1", "D014", "KE", None, "animal", "cattle")}
    assert len(values) == 4


def test_unique_host_enrichment_retains_the_same_link_on_repeated_rereads():
    notice = SituationNotice(notice_id="n1", source_id="who_don", published_date=DAY)
    legacy = build_alerts(notice, {"alerts": [alert()]})
    enriched, _ = clean(alert(host_type="human", host_evidence="human cases", count_unit="people"))
    first = build_alerts(notice, {"alerts": enriched})
    generated = first[0].alert_id
    preserve_alert_ids(legacy, first)
    assert first[0].alert_id == legacy[0].alert_id != generated
    second = build_alerts(notice, {"alerts": enriched})
    preserve_alert_ids(first, second)
    assert second[0].alert_id == legacy[0].alert_id


def test_host_splits_and_species_changes_do_not_reuse_an_ambiguous_or_incompatible_link():
    notice = SituationNotice(notice_id="n1", source_id="who_don", published_date=DAY)
    legacy = build_alerts(notice, {"alerts": [alert()]})
    rows, _ = clean(alert(host_type="human", host_evidence="human cases", count_unit="people"),
        alert(ANIMAL, host_type="animal", host_species="wild birds", host_evidence="wild birds", count_unit="animals"))
    split = build_alerts(notice, {"alerts": rows})
    preserve_alert_ids(legacy, split)
    assert all(row.alert_id != legacy[0].alert_id for row in split)
    changed = build_alerts(notice, {"alerts": [{**rows[1], "host_species": "cattle"}]})
    preserve_alert_ids([split[1]], changed)
    assert changed[0].alert_id != split[1].alert_id


def test_export_preserves_host_units_without_leaking_blocked_host_evidence():
    notice = SituationNotice(notice_id="n1", source_id="who_don", published_date=DAY,
                             title_en="Avian influenza", redistribution="blocked", url="https://www.who.int/event")
    row = SituationAlert(alert_id="a1", outbreak_id="OB1", disease_id="D014", disease_label="Avian influenza",
        country_code="KE", status="new", cases=2, deaths=None, host_type="animal", host_species="wild birds",
        count_unit="animals", host_evidence="wild birds", quote=ANIMAL, published_date=DAY, source_id="who_don")
    record = alert_record(row, notice, None, {})
    assert record["host_type"] == "animal" and record["count_unit"] == "animals"
    assert record["quote"] is None and record["host_evidence"] is None
    marker = map_marker(record)
    assert marker["host_species"] == "wild birds" and "host_evidence" not in marker
    human = {**record, "alert_id": "human", "host_type": "human", "host_species": None, "count_unit": "people"}
    assert len(latest_per_place([record, human])) == 2


def test_migration_preserves_old_rows_as_unknown(tmp_path):
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[2] / "migrations/versions/0032_situation_host_metadata.py"
    spec = importlib.util.spec_from_file_location("host_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE situation_alerts (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO situation_alerts VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            row = connection.execute(sa.text("SELECT host_type, host_species, count_unit, host_evidence FROM situation_alerts")).one()
            assert tuple(row) == ("unknown", None, "unknown", None)
            module.downgrade()
        assert [column["name"] for column in sa.inspect(connection).get_columns("situation_alerts")] == ["id"]
