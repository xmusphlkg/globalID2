from types import SimpleNamespace

import pytest

from src.literature.screening import controlled_subjects, decision_is_grounded, initial_screen


def article(**changes):
    values = {"title": "An unrelated headline", "abstract_text": None, "metadata_": {}, "source_payload": {}}
    values.update(changes)
    return SimpleNamespace(**values)


def review(reason):
    return SimpleNamespace(payload={"decision": "publish", "reasons": [reason]})


def test_title_keywords_do_not_decide_and_missing_context_is_uncertain():
    row = article(title="Dengue and other diseases", metadata_={"classification_evidence": {
        "diseases": {"D021": {"confidence": 0.98, "matched_terms": ["lexical:title:Dengue"]}},
    }})
    screen = initial_screen(row)
    assert screen["status"] == "needs_more_evidence"
    assert screen["candidate_disease_ids"] == ["D021"]
    assert not decision_is_grounded(row, review('This examines "Dengue and other diseases"'))


def test_abstract_context_without_title_keyword_can_support_review():
    abstract = ("Patients with dengue were assessed for infection and clinical outcomes. "
                "The investigators measured changes over the study period.")
    row = article(abstract_text=abstract)
    assert initial_screen(row)["status"] == "contextual_review"
    assert decision_is_grounded(row, review('Relevant because "Patients with dengue were assessed for infection"'))
    assert not decision_is_grounded(row, review('Relevant because "The patients had proven malaria"'))
    assert not decision_is_grounded(row, review('The abstract appears relevant without a quotation.'))


def test_mesh_metadata_is_attributed_but_does_not_replace_source_context():
    row = article(source_payload={"europe_pmc": {"meshHeadingList": {"meshHeading": [
        {"descriptorName": "Dengue", "majorTopic_YN": "Y"},
    ]}}})
    assert controlled_subjects(row) == [{"source": "europe_pmc.mesh", "term": "Dengue", "major_topic": True}]
    assert initial_screen(row)["status"] == "needs_more_evidence"
    assert not decision_is_grounded(row, review('Indexing states "Dengue is the main topic"'))


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,expected_status", [
    ("The title appears relevant", "review"),
    ('The study states "Patients with dengue were assessed for infection"', "published"),
])
async def test_model_screening_cannot_publish_without_abstract_evidence(reason, expected_status, monkeypatch):
    from src.ai.peer_review import ReviewedOutput
    from src.services.ai_content_governance_service import AIContentGovernanceService

    row = article(
        article_id="example", publication_status="review",
        integrity_status="current", peer_review_status="peer_reviewed",
        abstract_text=("Patients with dengue were assessed for infection and clinical outcomes. "
                       "The investigators measured changes over the study period."),
    )
    reviewed = ReviewedOutput("published", {"decision": "publish", "reasons": [reason]},
                              "author", "reviewer", "accept", 1, [], 0.9, None)
    service = AIContentGovernanceService()
    monkeypatch.setattr(service, "_article_fingerprint", lambda _: "snapshot")

    class Database:
        def __init__(self):
            self.events = []

        async def execute(self, _query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))

        def add(self, event):
            self.events.append(event)

    db = Database()
    counts = (await service._apply_literature_results(db, "articles", [("example", "snapshot", reviewed)])).counts
    assert row.publication_status == expected_status
    assert counts["held"] == (expected_status == "review")
    assert len(db.events) == (expected_status == "published")
    if expected_status == "review":
        assert row.metadata_["screening_hold_reason"] == "abstract_evidence_not_verified"
