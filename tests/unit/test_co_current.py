"""Current INS SIVIGILA source identity and bounded-page contracts."""
from __future__ import annotations

import csv
import io
import json

import pytest

from src.data.crawlers.co import ColombiaSIVIGILAContractError, FIELDS
from src.data.crawlers.co_current import (
    DATASET_ID, DEFAULT_SCOPE, HISTORY_START_YEAR, LANDING_URL,
    ONTOLOGY_SOURCE_ID, parse_metadata, parse_page,
)
from src.data.processors.co_current import ColombiaINSSivigilaCurrentUpdater


def metadata(**overrides):
    document = {
        "id": DATASET_ID,
        "attribution": "Instituto Nacional de Salud - INS, Bogotá D.C.",
        "licenseId": "CC_40_BY",
        "rowsUpdatedAt": 1793753774,
        "viewLastModified": 1793753811,
        "columns": [{"fieldName": field} for field in FIELDS[:-1]],
    }
    document.update(overrides)
    return json.dumps(document).encode()


def page(**overrides):
    row = {
        "cod_eve": "210", "nombre_evento": "DENGUE", "semana": "1",
        "ano": "2023", "cod_dpto_o": "5", "cod_mun_o": "5000",
        "departamento_ocurrencia": "ANTIOQUIA",
        "municipio_ocurrencia": "MUNICIPIO DESCONOCIDO",
        "conteo": "7", "socrata_id": "row-1",
    }
    row.update(overrides)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerow(row)
    return stream.getvalue().encode()


def test_current_dataset_identity_and_2022_exclusion():
    assert HISTORY_START_YEAR == 2023
    assert DEFAULT_SCOPE == "co_ins_sivigila_current"
    assert ColombiaINSSivigilaCurrentUpdater().ontology_source_id == ONTOLOGY_SOURCE_ID
    assert ColombiaINSSivigilaCurrentUpdater().full_history_start_year == 2023
    assert parse_metadata(metadata())["dataset_id"] == DATASET_ID
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_metadata(metadata(id="4hyg-wa9d"))
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_metadata(metadata(attribution="another publisher"))
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_metadata(metadata(licenseId="CC_40_BY_SA"))
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_page(page(ano="2022"), year=2022, offset=0, expected_rows=1,
                   source_url="https://www.datos.gov.co/resource/wns8-p38i.csv",
                   dataset_version=parse_metadata(metadata()))


def test_current_page_keeps_source_native_geography_and_license():
    rows, audit = parse_page(
        page(), year=2023, offset=0, expected_rows=1,
        source_url="https://www.datos.gov.co/resource/wns8-p38i.csv",
        dataset_version=parse_metadata(metadata()),
    )
    row = rows[0]
    assert row["GeographyKey"] == "source:SRC_CO_INS_SIVIGILA_CURRENT:unknown-location:05"
    assert row["SourceLandingPageURL"] == LANDING_URL
    assert row["DefinitionVersion"] == "co-ins-sivigila-current-municipality-week-v1"
    assert row["License"] == "CC BY 4.0; attribution to INS"
    assert row["PublicReleaseEnabled"] == "false"
    assert row["Cases"] == "7"
    assert audit["source_dataset_id"] == DATASET_ID
    with pytest.raises(ColombiaSIVIGILAContractError):
        parse_page(page(), year=2023, offset=0, expected_rows=1,
                   source_url="https://www.datos.gov.co/resource/wns8-p38i.csv",
                   dataset_version={"dataset_id": "4hyg-wa9d"})


def test_current_updater_rejects_historical_scope():
    with pytest.raises(ValueError):
        ColombiaINSSivigilaCurrentUpdater.normalize_source("co_ins_sivigila_historical")
