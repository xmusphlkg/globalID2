from __future__ import annotations

import csv
from datetime import date
import hashlib
import json

import pytest

from src.data.crawlers import IcelandDOHCrawler
from src.data.crawlers.powerbi_public import (
    PowerBIQueryResult,
    PowerBIReportContext,
    PublicPowerBIClient,
    decode_dsr_v2,
    schema_fingerprint,
)
from src.data.processors import ISMultiFrequencyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore


class _DBResult:
    def __init__(self, rows=(), scalar_value=None):
        self._rows = list(rows)
        self._scalar_value = scalar_value

    def scalar(self):
        return self._scalar_value

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _ISImportDB:
    def __init__(
        self,
        *,
        mapping_code="annual:sti:chlamydia:cases",
        disease_id=101,
        preserved_monthly=((date(2021, 1, 1), 101),),
    ):
        self.upsert_params = None
        self.upsert_sql = ""
        self.mapping_code = mapping_code
        self.disease_id = disease_id
        self.preserved_monthly = list(preserved_monthly)

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT id FROM countries" in sql:
            return _DBResult([(7,)])
        if "information_schema.columns" in sql:
            return _DBResult(scalar_value=True)
        if "SELECT dm.local_name, dm.series_id" in sql:
            series_id = (
                "SER_IS_DOH_ANNUAL_MRSA"
                if self.disease_id == 236
                else "SER_IS_DOH_ANNUAL_CHLAMYDIA"
            )
            return _DBResult([(self.mapping_code, series_id)])
        if "SELECT dm.local_name" in sql:
            return _DBResult(
                [
                    (
                        self.mapping_code,
                        self.disease_id,
                        "D236" if self.disease_id == 236 else "D094",
                        "SRC_IS_DOH_ANNUAL",
                    )
                ]
            )
        if "SELECT timezone('UTC', time)::date" in sql:
            return _DBResult(self.preserved_monthly)
        if "INSERT INTO disease_records" in sql:
            self.upsert_sql = sql
            self.upsert_params = params
            return _DBResult()
        raise AssertionError(f"Unexpected SQL: {sql}")


def _dsr_payload(rows, schema, dictionaries=None, descriptor=None):
    return {
        "results": [
            {
                "result": {
                    "data": {
                        "descriptor": {
                            "Select": descriptor
                            or [
                                {"Value": column["N"], "Name": column["N"]}
                                for column in schema
                            ]
                        },
                        "dsr": {
                            "Version": 2,
                            "DS": [
                                {
                                    "PH": [{"DM0": [{"S": schema, **rows[0]}, *rows[1:]]}],
                                    "ValueDicts": dictionaries or {},
                                }
                            ],
                        },
                    }
                }
            }
        ]
    }


def test_public_powerbi_dsr_v2_decodes_dictionary_repeat_and_null_bitmaps():
    schema = [
        {"N": "G0", "DN": "D0"},
        {"N": "G1"},
        {"N": "G2", "DN": "D1"},
        {"N": "M0"},
    ]
    payload = _dsr_payload(
        [
            {"C": [0, 1, 0, 155]},
            {"C": [2, 1, 5], "R": 1},
            {"C": [0], "R": 3, "Ø": 4},
        ],
        schema,
        dictionaries={"D0": ["2026"], "D1": ["Klamydía", "Lekandi"]},
        descriptor=[
            {"Value": "G0", "Name": "Kynsjúkdómar.AR"},
            {"Value": "G1", "Name": "Kynsjúkdómar.MAN"},
            {"Value": "G2", "Name": "Kynsjúkdómar.SJUKDOMUR"},
            {"Value": "M0", "Name": "Sum(Kynsjúkdómar.FJOLDI)"},
        ],
    )

    assert decode_dsr_v2(payload) == [
        {
            "Kynsjúkdómar.AR": "2026",
            "Kynsjúkdómar.MAN": 1,
            "Kynsjúkdómar.SJUKDOMUR": "Klamydía",
            "Sum(Kynsjúkdómar.FJOLDI)": 155,
        },
        {
            "Kynsjúkdómar.AR": "2026",
            "Kynsjúkdómar.MAN": 2,
            "Kynsjúkdómar.SJUKDOMUR": "Lekandi",
            "Sum(Kynsjúkdómar.FJOLDI)": 5,
        },
        {
            "Kynsjúkdómar.AR": "2026",
            "Kynsjúkdómar.MAN": 2,
            "Kynsjúkdómar.SJUKDOMUR": None,
            "Sum(Kynsjúkdómar.FJOLDI)": 0,
        },
    ]


def test_public_powerbi_dsr_v2_fails_closed_on_shifted_compact_row():
    payload = _dsr_payload(
        [{"C": [2026]}],
        [{"N": "G0"}, {"N": "M0"}],
    )

    with pytest.raises(ValueError, match="fewer values"):
        decode_dsr_v2(payload)


class _Response:
    def __init__(self, *, text="", payload=None, status_code=200):
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    def __init__(self, get_responses, post_response=None):
        self.headers = {}
        self.get_responses = list(get_responses)
        self.post_response = post_response
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response


def test_public_powerbi_discovery_resolves_current_ids_cluster_and_schema():
    resource_key = "53f7eba1-f7bf-42dd-acad-9b4fa068cbd2"
    html = """
        var resolvedClusterUri = 'https://wabi-north-europe-q-primary-redirect.analysis.windows.net/';
        var resourceDescriptor = JSON.parse('{\"k\":\"53f7eba1-f7bf-42dd-acad-9b4fa068cbd2\",\"t\":\"tenant\"}');
    """
    models = {
        "models": [
            {
                "id": 1623135,
                "dbName": "7a19e7df-9d00-48b0-823d-d90b17e47981",
                "LastRefreshTime": "2026-07-01T10:18:59.567",
            }
        ],
        "exploration": {
            "report": {"objectId": "a41f7632-3b9f-48a9-b6d5-cd29f8d24315"}
        },
    }
    schema = {
        "schemas": [
            {
                "modelId": 1623135,
                "schema": {
                    "Entities": [
                        {
                            "Name": "Kynsjúkdómar",
                            "Properties": [
                                {"Name": "AR", "DataType": 4, "Column": {}},
                                {"Name": "FJOLDI", "DataType": 4, "Column": {}},
                            ],
                        }
                    ]
                },
            }
        ]
    }
    session = _Session(
        [_Response(text=html), _Response(payload=models), _Response(payload=schema)]
    )

    context = PublicPowerBIClient(session=session).discover(
        "https://app.powerbi.com/view?r=current"
    )

    assert context.resource_key == resource_key
    assert context.api_base_url == (
        "https://wabi-north-europe-q-primary-api.analysis.windows.net"
    )
    assert context.model_id == 1623135
    assert context.dataset_id == "7a19e7df-9d00-48b0-823d-d90b17e47981"
    assert context.report_id == "a41f7632-3b9f-48a9-b6d5-cd29f8d24315"
    assert context.schema_fingerprint == schema_fingerprint(schema)
    assert "modelsAndExploration" in session.get_calls[1][0]
    assert session.get_calls[2][0].endswith(
        f"/public/reports/{resource_key}/conceptualschema"
    )


def test_public_powerbi_query_validates_schema_and_builds_national_sum():
    schema_payload = {
        "schemas": [
            {
                "schema": {
                    "Entities": [
                        {
                            "Name": "Sýkingar",
                            "Properties": [
                                {"Name": "ISO_AR"},
                                {"Name": "VIKUNUMER_ISO"},
                                {"Name": "SJUKDOMUR"},
                                {"Name": "FJOLDI"},
                            ],
                        }
                    ]
                }
            }
        ]
    }
    response_payload = _dsr_payload(
        [{"C": ["2026", 19, "RSV", 2]}],
        [{"N": "G0"}, {"N": "G1"}, {"N": "G2"}, {"N": "M0"}],
        descriptor=[
            {"Value": "G0", "Name": "Sýkingar.ISO_AR"},
            {"Value": "G1", "Name": "Sýkingar.VIKUNUMER_ISO"},
            {"Value": "G2", "Name": "Sýkingar.SJUKDOMUR"},
            {"Value": "M0", "Name": "Sum(Sýkingar.FJOLDI)"},
        ],
    )
    session = _Session([], post_response=_Response(payload=response_payload))
    context = PowerBIReportContext(
        view_url="https://app.powerbi.com/view?r=resp",
        resource_key="f829b4c8-63dd-4cd3-9e37-1b1101e8e02d",
        api_base_url="https://cluster-api.example.test",
        model_id=1621057,
        dataset_id="dataset-id",
        report_id="report-id",
        last_refresh="2026-05-11T08:23:13.403",
        schema_fingerprint=schema_fingerprint(schema_payload),
        models_payload={},
        schema_payload=schema_payload,
        landing_html="report",
    )

    result = PublicPowerBIClient(session=session).query_entity_sum(
        context,
        entity="Sýkingar",
        group_columns=("ISO_AR", "VIKUNUMER_ISO", "SJUKDOMUR"),
        value_column="FJOLDI",
    )

    assert result.rows[0]["Sum(Sýkingar.FJOLDI)"] == 2
    body = session.post_calls[0][1]["json"]
    selections = body["queries"][0]["Query"]["Commands"][0][
        "SemanticQueryDataShapeCommand"
    ]["Query"]["Select"]
    assert selections[-1]["Aggregation"]["Function"] == 0
    assert body["modelId"] == 1621057
    assert body["queries"][0]["ApplicationContext"]["DatasetId"] == "dataset-id"


def test_iceland_series_contract_is_complete_unique_and_projectable():
    definitions = list(
        __import__(
            "src.data.processors.is", fromlist=["SERIES_DEFINITIONS"]
        ).SERIES_DEFINITIONS
    )

    assert len(definitions) == 22
    assert len({item.disease_code for item in definitions}) == 22
    assert {item.frequency for item in definitions} == {"annual", "monthly", "weekly"}
    assert {item.measure for item in definitions} == {
        "case_notifications",
        "clinical_diagnoses",
        "laboratory_diagnoses",
        "reported_diagnoses",
    }
    assert {item.unit for item in definitions} == {"count"}
    assert len([item for item in definitions if item.source_scope == "is_doh_annual"]) == 14
    assert len([item for item in definitions if item.source_scope == "is_doh_sti"]) == 3
    assert len(
        [item for item in definitions if item.source_scope == "is_doh_respiratory"]
    ) == 5


def test_iceland_series_contract_resolves_every_registered_source_row():
    definitions = __import__(
        "src.data.processors.is", fromlist=["SERIES_DEFINITIONS"]
    ).SERIES_DEFINITIONS
    rows = []
    for definition in definitions:
        report_date = (
            "2025-01-06" if definition.frequency == "weekly" else "2025-01-01"
        )
        rows.append(
            {
                "Date": report_date,
                "RawDiseaseLabel": definition.raw_disease_label,
                "DiseaseCode": definition.disease_code,
                "Cases": "1",
                "Measure": definition.measure,
                "ReportingBasis": definition.reporting_basis,
                "Unit": definition.unit,
                "Source": definition.source_name,
                "Dimensions": "{}",
                "GeographyKey": "country:IS:national",
                "AuthoritativeRevision": "true",
            }
        )

    updater = ISMultiFrequencyUpdater()
    store = SeriesObservationStore()
    selected = store.select_registry_rows(
        rows,
        "IS",
        source_id=updater.ontology_source_id,
    )
    built = store.build_observations(
        selected.rows,
        "IS",
        source_id=updater.ontology_source_id,
    )

    assert len(selected.rows) == 22
    assert selected.skipped_unregistered == 0
    assert len(built.observations) == 22
    assert built.skipped_unmatched == 0
    assert built.skipped_ambiguous == 0
    assert built.skipped_invalid == 0


class _FakePowerBI:
    def __init__(self, rows):
        self.rows = rows

    def query_entity_sum(self, context, **kwargs):
        del context
        return PowerBIQueryResult(
            rows=self.rows[kwargs["entity"]],
            request_payload={"entity": kwargs["entity"]},
            response_payload={"rows": len(self.rows[kwargs["entity"]])},
        )


def _context():
    return PowerBIReportContext(
        view_url="https://app.powerbi.com/view?r=test",
        resource_key="00000000-0000-4000-8000-000000000000",
        api_base_url="https://cluster-api.example.test",
        model_id=123,
        dataset_id="dataset",
        report_id="report",
        last_refresh="2026-07-01T10:18:59.567",
        schema_fingerprint="sha256:test",
        models_payload={},
        schema_payload={},
        landing_html="report",
    )


def test_iceland_sti_and_respiratory_normalize_month_and_iso_week(tmp_path):
    crawler = IcelandDOHCrawler(
        powerbi_client=_FakePowerBI(
            {
                "Kynsjúkdómar": [
                    {
                        "Kynsjúkdómar.AR": "2026",
                        "Kynsjúkdómar.MAN": "06",
                        "Kynsjúkdómar.SJUKDOMUR": "Klamydía",
                        "Sum(Kynsjúkdómar.FJOLDI)": 127,
                    }
                ],
                "Sýkingar": [
                    {
                        "Sýkingar.ISO_AR": "2026",
                        "Sýkingar.VIKUNUMER_ISO": 19,
                        "Sýkingar.SJUKDOMUR": "RSV",
                        "Sum(Sýkingar.FJOLDI)": 2,
                    }
                ],
            }
        ),
        raw_dir=tmp_path,
    )

    sti_rows, _ = crawler._fetch_sti(_context(), "2026-08-07T00:00:00Z")
    respiratory_rows, _ = crawler._fetch_respiratory(
        _context(), "2026-08-07T00:00:00Z"
    )

    assert sti_rows[0]["Date"] == "2026-06-01"
    assert sti_rows[0]["PeriodType"] == "month"
    assert sti_rows[0]["PeriodValue"] == "202606"
    assert sti_rows[0]["Cases"] == "127"
    assert sti_rows[0]["DiseaseCode"] == "sti:chlamydia:monthly-diagnoses"
    assert respiratory_rows[0]["Date"] == "2026-05-04"
    assert respiratory_rows[0]["Year"] == "2026"
    assert respiratory_rows[0]["ISOYear"] == "2026"
    assert respiratory_rows[0]["ISOWeek"] == "19"
    assert respiratory_rows[0]["PeriodValue"] == "202619"
    assert respiratory_rows[0]["DiseaseCode"] == (
        "respiratory:rsv:weekly-diagnoses"
    )
    assert respiratory_rows[0]["Dimensions"] == "{}"
    assert respiratory_rows[0]["SourcePageURL"] == (
        "https://island.is/en/respiratory-tract-infections"
    )


def test_iceland_iso_cross_year_keeps_calendar_and_iso_years_separate(tmp_path):
    crawler = IcelandDOHCrawler(
        powerbi_client=_FakePowerBI(
            {
                "Sýkingar": [
                    {
                        "Sýkingar.ISO_AR": "2020",
                        "Sýkingar.VIKUNUMER_ISO": 1,
                        "Sýkingar.SJUKDOMUR": "RSV",
                        "Sum(Sýkingar.FJOLDI)": 3,
                    }
                ]
            }
        ),
        raw_dir=tmp_path,
    )

    rows, _ = crawler._fetch_respiratory(_context(), "2026-08-07T00:00:00Z")

    assert rows[0]["Date"] == "2019-12-30"
    assert rows[0]["Year"] == "2019"
    assert rows[0]["Month"] == "12"
    assert rows[0]["ISOYear"] == "2020"
    assert rows[0]["ISOWeek"] == "1"


def test_iceland_raw_archive_manifest_hashes_all_powerbi_artifacts(tmp_path):
    crawler = IcelandDOHCrawler(raw_dir=tmp_path, save_raw=True)
    query = PowerBIQueryResult(
        rows=[],
        request_payload={"request": "payload"},
        response_payload={"response": "payload"},
    )
    manifest_path = crawler._archive_scope(
        scope="is_doh_sti",
        context=_context(),
        queries=[("Kynsjúkdómar", query)],
        retrieved_at="2026-08-07T12:34:56.123456Z",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_scope"] == "is_doh_sti"
    assert manifest["schema_fingerprint"] == "sha256:test"
    assert {item["file"] for item in manifest["artifacts"]} == {
        "report.html",
        "models-and-exploration.json",
        "conceptual-schema.json",
        "query-01-kynsj-kd-mar-request.json",
        "query-01-kynsj-kd-mar-response.json",
    }
    for artifact in manifest["artifacts"]:
        raw = (manifest_path.parent / artifact["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == artifact["sha256"]


def test_iceland_updater_loads_zero_without_treating_it_as_missing(tmp_path):
    csv_path = tmp_path / "iceland.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=__import__(
            "src.data.crawlers.is", fromlist=["CSV_FIELDNAMES"]
        ).CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow(
            {
                "": "1",
                "Disease": "kíghósti",
                "DiseaseCode": "respiratory:pertussis:weekly-diagnoses",
                "SourceSeriesCode": "respiratory:pertussis:weekly-diagnoses",
                "Date": "2026-05-04",
                "Year": "2026",
                "Month": "5",
                "ISOYear": "2026",
                "ISOWeek": "19",
                "PeriodType": "iso_week",
                "PeriodValue": "202619",
                "Cases": "0",
                "SourceScope": "is_doh_respiratory",
                "SourceId": "SRC_IS_DOH_RESPIRATORY",
                "Source": "Iceland Directorate of Health Respiratory Dashboard",
                "GeographyKey": "country:IS:national",
                "Dimensions": "{}",
                "Frequency": "weekly",
                "Measure": "case_notifications",
                "ReportingBasis": "registry_and_laboratory_diagnoses",
                "Unit": "count",
            }
        )

    updater = ISMultiFrequencyUpdater(output_csv=csv_path)
    rows = updater._load_rows(csv_path)

    assert len(rows) == 1
    assert rows[0]["Cases"] == "0"
    assert rows[0]["Date"] == "2026-05-04"
    assert rows[0]["Dimensions"] == "{}"
    assert updater.ontology_source_id[
        "Iceland Directorate of Health Respiratory Dashboard"
    ] == "SRC_IS_DOH_RESPIRATORY"


@pytest.mark.asyncio
async def test_iceland_annual_legacy_projection_preserves_existing_monthly_fact():
    updater = ISMultiFrequencyUpdater()
    db = _ISImportDB()
    rows = [
        {
            "Date": report_date,
            "RawDiseaseLabel": "Klamydíusýking",
            "DiseaseCode": "annual:sti:chlamydia:cases",
            "Cases": cases,
            "SourceScope": "is_doh_annual",
            "SourceId": "SRC_IS_DOH_ANNUAL",
            "Source": "Iceland Directorate of Health Annual Dashboard",
        }
        for report_date, cases in (("2021-01-01", "500"), ("2022-01-01", "600"))
    ]

    result = await updater.import_rows(
        db,
        rows,
        db_latest_date=None,
        source_latest_date=date(2022, 1, 1),
        force=True,
    )

    assert result.inserted_or_updated == 1
    assert result.skipped_unmapped == 0
    assert result.skipped_incompatible_projection == 1
    assert len(db.upsert_params) == 1
    assert db.upsert_params[0]["time"].date() == date(2022, 1, 1)
    metadata = json.loads(db.upsert_params[0]["metadata"])
    assert metadata["source_series_code"] == "SER_IS_DOH_ANNUAL_CHLAMYDIA"
    assert metadata["source_native_series_code"] == "annual:sti:chlamydia:cases"


@pytest.mark.asyncio
async def test_current_annual_mrsa_2019_overwrites_historical_annual_projection():
    updater = ISMultiFrequencyUpdater()
    db = _ISImportDB(
        mapping_code="annual:antimicrobial-resistance:mrsa:cases",
        disease_id=236,
        preserved_monthly=(),
    )

    result = await updater.import_rows(
        db,
        [
            {
                "Date": "2019-01-01",
                "RawDiseaseLabel": "Meticillín ónæmur staph. aureus (MÓSA)",
                "DiseaseCode": "annual:antimicrobial-resistance:mrsa:cases",
                "Cases": "42",
                "SourceScope": "is_doh_annual",
                "SourceId": "SRC_IS_DOH_ANNUAL",
                "Source": "Iceland Directorate of Health Annual Dashboard",
            }
        ],
        db_latest_date=None,
        source_latest_date=date(2019, 1, 1),
        force=True,
    )

    assert result.inserted_or_updated == 1
    assert result.skipped_incompatible_projection == 0
    assert db.upsert_params[0]["disease_id"] == 236
    assert db.upsert_params[0]["cases"] == 42
    assert "ON CONFLICT" in db.upsert_sql
    assert "cases = EXCLUDED.cases" in db.upsert_sql
