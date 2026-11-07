import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.peer_review import Completion
from src.generation.report_v4.peer_review import (
    ReportNarrativeReviewError,
    build_review_task,
    review_report_narrative,
    sanitize_analysis,
)
from src.generation.report_v4.pipeline import (
    ReportV4Context,
    ReportV4Pipeline,
    _insert_peer_reviewed_interpretation,
)


def _inputs():
    evidence = {
        "country": {"code": "JP", "name_en": "Japan", "name_zh": "日本"},
        "period": {"start": "2025-05-30", "end": "2026-05-30"},
        "reporting_cadence": "weekly",
        "death_reporting": {
            "status": "not_reported",
            "source_policy": {"case_scope": "sentinel", "death_counts": "not_reported"},
        },
        "summary_metrics": {"record_count": 4384, "disease_count": 1, "total_cases": 1250},
        "data_quality": {"confidence": "medium", "issues": ["Death counts are not provided"]},
        "score_semantics": {"type": "surveillance_attention_priority"},
        "sources": [{"title": "Japan sentinel surveillance", "type": "database", "record_count": 4384}],
        "diseases": [
            {
                "evidence_id": "disease:CONJ",
                "disease_id": "CONJ",
                "name_en": "Acute conjunctivitis",
                "name_zh": "急性结膜炎",
                "metrics": {"latest_cases": 120, "previous_cases": 100, "change_pct": 20.0},
                "trend": {"label": "increasing"},
                "attention": {"score": 75, "level": "high", "drivers": ["short_term_change"]},
                "data_quality": {"confidence": "medium"},
                "limitations": ["Sentinel counts are not national totals"],
                "source_coverage": ["Japan sentinel surveillance"],
            }
        ],
    }
    document = {
        "title": {"zh": "日本报告", "en": "Japan report", "fr": "Rapport Japon"},
        "summary": {"zh": "固定统计摘要", "en": "Fixed statistical summary", "fr": "Résumé statistique fixe"},
        "key_findings": {"zh": ["趋势上升"], "en": ["Trend increasing"], "fr": ["Tendance en hausse"]},
        "sections": [
            {
                "type": "decision_summary",
                "title": {"zh": "判断", "en": "Judgement", "fr": "Évaluation"},
                "body": {"zh": "固定统计正文", "en": "Fixed fact body", "fr": "Texte factuel fixe"},
                "evidence_refs": ["summary:record_count"],
            }
        ],
        "metrics": {"data_signature": "stable-signature", "total_cases": 1250},
        "attention_ranking": [{"disease_id": "CONJ"}],
    }
    return evidence, document


class FakeCompleter:
    single_model = False

    def __init__(self, *, verdict="accept", select_figure=False):
        self.calls = []
        self.verdict = verdict
        self.select_figure = select_figure

    async def __call__(self, role, system, prompt, **kwargs):
        self.calls.append((role, json.loads(prompt)))
        if role == "author":
            payload = {
                "figure_plan": [],
                "analysis": {
                    "zh": "哨点资料显示病例信号近期有所上升，但不能代表全国感染总量。建议先核对哨点覆盖和报告完整性，再判断趋势是否持续。",
                    "en": "The sentinel data show a recent rise in reported cases, but they do not represent the national infection total. Check site coverage and reporting completeness before deciding whether the pattern is sustained.",
                    "fr": "Les données sentinelles indiquent une hausse récente des cas déclarés, sans représenter le total national des infections. Vérifiez la couverture des sites et l’exhaustivité des déclarations avant de conclure à une tendance durable.",
                }
            }
            if self.select_figure:
                candidates = self.calls[-1][1]["figure_candidates"]
                payload["figure_plan"] = [{"candidate_id": candidates[0]["candidate_id"], "rationale": "Compare the observed counts for the disease discussed in this section."}]
            return Completion(text=json.dumps(payload, ensure_ascii=False), model="provider:author")
        return Completion(
            text=json.dumps({"verdict": self.verdict, "score": 0.95, "issues": [], "corrected": None}),
            model="provider:reviewer",
        )


def test_review_task_carries_computed_facts_and_complete_disease_roster():
    evidence, document = _inputs()
    task = build_review_task(evidence_packet=evidence, deterministic_document=document)

    assert task["summary_metrics"]["total_cases"] == 1250
    assert task["source_policy"]["case_scope"] == "sentinel"
    assert task["disease_signals"][0]["metrics"]["latest_cases"] == 120
    assert task["deterministic_report"]["sections"][0]["type"] == "decision_summary"


def test_sanitizer_rejects_new_numbers_but_allows_numeric_disease_names():
    task = {
        "disease_signals": [{"name_en": "COVID-19", "name_zh": "COVID-19"}],
    }
    clean, notes = sanitize_analysis(
        {"analysis": {locale: "COVID-19 surveillance signals require cautious review." for locale in ("zh", "en", "fr")}},
        task=task,
    )
    assert clean["_usable"] is True
    assert notes == []

    dirty, notes = sanitize_analysis(
        {"analysis": {locale: "The signal rose by 20 percent and needs careful review." for locale in ("zh", "en", "fr")}},
        task=task,
    )
    assert dirty["_usable"] is False
    assert any("numeric claim" in note for note in notes)

    adjacent, notes = sanitize_analysis(
        {"analysis": {locale: "近期病例增加20%，但这一哨点信号仍需结合覆盖变化和报告完整性仔细复核。" for locale in ("zh", "en", "fr")}},
        task=task,
    )
    assert adjacent["_usable"] is False
    assert any("numeric claim" in note for note in notes)


@pytest.mark.asyncio
async def test_shared_peer_review_returns_provenance_and_preserves_report_facts():
    evidence, document = _inputs()
    original_metrics = dict(document["metrics"])
    complete = FakeCompleter()

    narrative, provenance = await review_report_narrative(
        evidence_packet=evidence,
        deterministic_document=document,
        completer=complete,
    )
    _insert_peer_reviewed_interpretation(document, narrative)
    document["peer_review"] = provenance

    assert [role for role, _ in complete.calls] == ["author", "reviewer"]
    assert provenance["status"] == "published"
    assert provenance["review_mode"] == "cross_model"
    assert document["metrics"] == original_metrics
    assert document["sections"][1]["type"] == "peer_reviewed_interpretation"
    assert document["sections"][1]["evidence_refs"] == ["disease:CONJ"]


@pytest.mark.asyncio
async def test_report_narrative_is_blocked_when_shared_review_rejects():
    evidence, document = _inputs()
    with pytest.raises(ReportNarrativeReviewError, match="was not accepted"):
        await review_report_narrative(
            evidence_packet=evidence,
            deterministic_document=document,
            completer=FakeCompleter(verdict="reject"),
        )


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _ReportDB:
    def __init__(self, country, diseases):
        self.country = country
        self.diseases = diseases
        self.calls = 0

    async def execute(self, _query):
        self.calls += 1
        return _Result(self.country if self.calls == 1 else self.diseases)

    async def commit(self):
        return None


class _CapturePersistence:
    def __init__(self):
        self.document = None

    async def persist(self, **kwargs):
        self.document = kwargs["document"]
        report = kwargs["report"]
        report.metadata_ = {**(report.metadata_ or {}), "peer_review": self.document["peer_review"]}
        return []


class _NoopExporter:
    def write_files(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_pipeline_runs_review_before_persisting_and_keeps_metrics_deterministic(monkeypatch, tmp_path: Path):
    from src.generation.report_v4 import peer_review as report_peer_review

    _evidence, _document = _inputs()
    completer = FakeCompleter(select_figure=True)

    async def fake_factory():
        return completer

    monkeypatch.setattr(report_peer_review, "completer_factory", fake_factory)
    country = SimpleNamespace(id=1, code="JP", name="Japan", name_en="Japan", name_local="日本", metadata_={})
    disease = SimpleNamespace(
        id=1, name="CONJ", name_en="Conjunctivitis", name_local="急性结膜炎", aliases=[],
        metadata_={}, category="Viral", icd_10=None, icd_11=None,
    )
    db = _ReportDB(country, [disease])
    report = SimpleNamespace(id=12, country_id=1, metadata_={})
    data = __import__("pandas").DataFrame(
        [
            {"time": datetime(2025, 1, 6, tzinfo=timezone.utc), "disease_id": 1, "cases": 10, "deaths": None,
             "new_cases": 10, "new_deaths": None, "data_source": "Japan sentinel surveillance", "metadata": {"death_reporting": "not_provided_by_source"}},
            {"time": datetime(2025, 1, 13, tzinfo=timezone.utc), "disease_id": 1, "cases": 14, "deaths": None,
             "new_cases": 14, "new_deaths": None, "data_source": "Japan sentinel surveillance", "metadata": {"death_reporting": "not_provided_by_source"}},
        ]
    )
    persistence = _CapturePersistence()
    events = []

    async def progress(*args):
        events.append(args)

    pipeline = ReportV4Pipeline(persistence=persistence, file_exporter=_NoopExporter())
    await pipeline.generate(
        ReportV4Context(
            db=db,
            report=report,
            data=data,
            historical_data=data,
            period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2025, 1, 31, tzinfo=timezone.utc),
            output_dir=tmp_path,
            progress_callback=progress,
        )
    )

    stored = persistence.document
    assert [role for role, _ in completer.calls] == ["author", "reviewer"]
    assert stored["peer_review"]["status"] == "published"
    assert stored["sections"][1]["type"] == "peer_reviewed_interpretation"
    assert stored["metrics"]["total_cases"] == 24
    assert stored["figures"][0]["figure_type"] == "period_comparison_bar"
    assert stored["figures"][0]["section_type"] == "disease_context"
    assert [row["cases"] for row in stored["figure_data"]["datasets"]["period_comparison_bar:CONJ"]["points"]] == [10, 14]
    assert report.metadata_["peer_review"]["review_mode"] == "cross_model"
    assert [event[1] for event in events] == [0, 1]
