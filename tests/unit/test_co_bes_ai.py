"""BES model output must preserve source-year cells and independent agreement."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.data.crawlers.co_bes import (
    BESContractError,
    BESCover,
    BESReport,
    normalize_annex_cells,
)
from src.services.ingestion import co_bes_ai

PDF_URL = "https://www.ins.gov.co/buscador-eventos/BoletinEpidemiologico/2025_Boletin_epidemiologico_semana_1.pdf"
REPORT = BESReport(PDF_URL, PDF_URL, "2025 W1", 2025)
COVER = BESCover(2025, 1, date(2025, 1, 4))
DIGEST = "a" * 64


def _document(*, header="Acumulado 2025", value="1", territories=None):
    territories = territories or [f"Territorio {i}" for i in range(38)] + ["Total nacional"]
    return {"territories": territories,
            "groups": [{"position": 1, "label": "Dengue", "definition": "Casos probables",
                        "accumulated_header": header, "values": [value] * len(territories)}]}


def test_ai_page_requires_all_39_distinct_source_territories():
    document = _document(territories=[f"Territorio {i}" for i in range(37)] + ["Total nacional"])
    with pytest.raises(BESContractError, match="incomplete territorial"):
        co_bes_ai._page_cells(document, page=32, digest=DIGEST, cover=COVER)


def test_ai_cells_reject_wrong_year():
    cells = co_bes_ai._page_cells(_document(header="Acumulado 2024"),
                                   page=32, digest=DIGEST, cover=COVER)
    with pytest.raises(BESContractError, match="not source-year cumulative"):
        normalize_annex_cells(cells, REPORT, COVER)


def test_ai_cells_reject_nonnumeric_model_output():
    with pytest.raises(BESContractError, match="nonnumeric"):
        co_bes_ai._page_cells(_document(value="12x"), page=32, digest=DIGEST, cover=COVER)


def test_printed_malformed_counts_remain_unknown_and_cannot_agree_with_guessed_digits():
    cells = co_bes_ai._page_cells(_document(value="4,95"), page=32, digest=DIGEST, cover=COVER)
    rows, audit = normalize_annex_cells(cells, REPORT, COVER)
    assert len(rows) == 39
    assert all(row["Cases"] == "" and row["MissingValueStatus"] == "source_unreadable" for row in rows)
    assert audit["unparsed_cells"] == 39
    assert co_bes_ai._count_fingerprint("4,95") != co_bes_ai._count_fingerprint("495")


@pytest.mark.asyncio
async def test_independent_ai_transcriptions_must_agree(tmp_path, monkeypatch):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF fixture")
    async def fake_cover(path, report, payload):
        return COVER, []

    monkeypatch.setattr(co_bes_ai, "_read_cover", fake_cover)
    monkeypatch.setattr(co_bes_ai, "_page_document", lambda path, page: (b"%PDF page", "table"))

    class FakeCompleter:
        calls = 0

        def __init__(self, **kwargs):
            self.agents = {"author": SimpleNamespace(get_latest_conversation=dict)}

        async def __call__(self, *args, **kwargs):
            type(self).calls += 1
            return SimpleNamespace(
                text=json.dumps(_document(value=str(type(self).calls))),
                model=f"provider:model-{type(self).calls}", input_mode="native_file")

    monkeypatch.setattr(co_bes_ai, "ModelCenterCompleter", FakeCompleter)
    with pytest.raises(BESContractError, match="transcriptions disagree"):
        await co_bes_ai.extract_report(pdf, REPORT, [32], cache_directory=tmp_path / "cache")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert not list((tmp_path / "cache").glob(f"{digest}-*.json"))
    assert FakeCompleter.calls == 6


def _prepare_page(tmp_path, monkeypatch):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF fixture")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    directory = tmp_path / "cache"

    async def fake_cover(path, report, payload):
        return COVER, []

    monkeypatch.setattr(co_bes_ai, "_read_cover", fake_cover)
    monkeypatch.setattr(co_bes_ai, "_page_document", lambda path, page: (b"%PDF page", "table"))
    return pdf, digest, directory


def _seed_conflicting_responses(directory, digest):
    originals = {}
    for attempt, value in enumerate(("1", "2"), 1):
        path = directory / "responses" / f"{digest}-{co_bes_ai.PROMPT_VERSION}-p32-{attempt}.json"
        co_bes_ai._atomic_json(path, {
            "page": 32, "attempt": attempt, "response": json.dumps(_document(value=value)),
            "model": f"provider:model-{attempt}", "input_mode": "native_file",
            "page_sha256": hashlib.sha256(b"%PDF page").hexdigest(),
        })
        originals[path] = path.read_bytes()
    return originals


def _install_readings(monkeypatch, values):
    class FakeCompleter:
        calls = 0

        def __init__(self, **kwargs):
            self.agents = {"author": SimpleNamespace(get_latest_conversation=dict)}

        async def __call__(self, *args, **kwargs):
            value = values[type(self).calls]
            type(self).calls += 1
            return SimpleNamespace(
                text=json.dumps(_document(value=value)),
                model=f"provider:fresh-model-{type(self).calls}", input_mode="native_file",
            )

    monkeypatch.setattr(co_bes_ai, "ModelCenterCompleter", FakeCompleter)
    return FakeCompleter


@pytest.mark.asyncio
async def test_cached_disagreement_rereads_fresh_pair_and_preserves_originals(tmp_path, monkeypatch):
    pdf, digest, directory = _prepare_page(tmp_path, monkeypatch)
    originals = _seed_conflicting_responses(directory, digest)
    completer = _install_readings(monkeypatch, ["3", "3"])

    result = await co_bes_ai.extract_report(pdf, REPORT, [32], cache_directory=directory)

    assert completer.calls == 2
    assert result["validated"]
    assert len(result["cells"]) == 39
    assert {cell["accumulated_value_raw"] for cell in result["cells"]} == {"3"}
    for path, payload in originals.items():
        assert path.read_bytes() == payload
    history = result["transcription_attempts"]["32"]
    assert [(entry["reread_round"], entry["status"]) for entry in history] == [
        (0, "rejected"), (1, "accepted"),
    ]
    assert [json.loads(entry["response"])["groups"][0]["values"][0]
            for entry in history[0]["provenance"]] == ["1", "2"]
    assert [json.loads(entry["response"])["groups"][0]["values"][0]
            for entry in history[1]["provenance"]] == ["3", "3"]
    retry_histories = list((directory / "responses" / "retries").glob("*/*/history.json"))
    assert len(retry_histories) == 1
    assert json.loads(retry_histories[0].read_bytes())["validated"]


@pytest.mark.asyncio
async def test_cached_disagreement_exhausts_bounded_retries_without_validated_cache(tmp_path, monkeypatch):
    pdf, digest, directory = _prepare_page(tmp_path, monkeypatch)
    originals = _seed_conflicting_responses(directory, digest)
    completer = _install_readings(monkeypatch, ["3", "4", "5", "6"])

    with pytest.raises(BESContractError, match="transcriptions disagree"):
        await co_bes_ai.extract_report(pdf, REPORT, [32], cache_directory=directory)

    assert completer.calls == 4
    assert not list(directory.glob(f"{digest}-*.json"))
    assert not list((directory / "pages").glob("*.json"))
    for path, payload in originals.items():
        assert path.read_bytes() == payload
    retry_histories = list((directory / "responses" / "retries").glob("*/*/history.json"))
    assert len(retry_histories) == 1
    history = json.loads(retry_histories[0].read_bytes())
    assert not history["validated"]
    assert [(entry["reread_round"], entry["status"]) for entry in history["attempts"]] == [
        (0, "rejected"), (1, "rejected"), (2, "rejected"),
    ]


@pytest.mark.asyncio
async def test_successful_page_cache_uses_no_model_or_page_extraction(tmp_path, monkeypatch):
    pdf, digest, directory = _prepare_page(tmp_path, monkeypatch)
    cells = co_bes_ai._page_cells(_document(value="7"), page=32, digest=digest, cover=COVER)
    co_bes_ai._atomic_json(
        directory / "pages" / f"{digest}-{co_bes_ai.PROMPT_VERSION}-p32.json",
        {"validated": True, "pdf_sha256": digest, "page": 32,
         "source_epi_year": COVER.year, "source_epi_week": COVER.week,
         "cells": cells, "provenance": []},
    )

    def unexpected_call(*args, **kwargs):
        pytest.fail("Validated page cache must not trigger extraction or model calls")

    monkeypatch.setattr(co_bes_ai, "ModelCenterCompleter", unexpected_call)
    monkeypatch.setattr(co_bes_ai, "_page_document", unexpected_call)

    result = await co_bes_ai.extract_report(pdf, REPORT, [32], cache_directory=directory)

    assert result["validated"]
    assert result["cells"] == cells
