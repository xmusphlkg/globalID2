from src.literature.recommendations import attach_related_research
from src.literature import recommendations


def test_indexed_ranking_matches_exhaustive_reference_and_only_explains_winners(monkeypatch):
    import random

    randomizer = random.Random(1729)
    articles = [
        _article(
            str(index // 2),  # Duplicate identifiers exercise the self gate.
            disease=randomizer.choice(["D001", "D002", " d003 ", None]),
            topic=randomizer.choice(["Surveillance", "surveillance", "Vaccines", None]),
            country=randomizer.choice(["JP", "US", None]),
            study=randomizer.choice(["Cohort study", "Guideline", None]),
            date=randomizer.choice(["2026-01-01", "2026-02-01", "2010-01-01"]),
        ) for index in range(60)
    ]
    expected = []
    original_similarity = recommendations._similarity
    for article in articles:
        pairs = [(candidate, original_similarity(article, candidate))
                 for candidate in articles if candidate["article_id"] != article["article_id"]]
        pairs = [pair for pair in pairs if pair[1][0] > 0]
        pairs.sort(key=lambda pair: (pair[1][0], pair[0]["published_at"], pair[0]["article_id"]), reverse=True)
        expected.append([(candidate["article_id"], *details) for candidate, details in pairs[:6]])
    explanations = []

    def explain(left, right):
        explanations.append((left["article_id"], right["article_id"]))
        return original_similarity(left, right)

    monkeypatch.setattr(recommendations, "_similarity", explain)
    result = attach_related_research(iter(articles))
    actual = [[(item["article_id"], item["similarity_score"], item["reasons_en"], item["reasons_zh"])
               for item in article["related_articles"]] for article in result]
    assert actual == expected
    assert len(explanations) == sum(map(len, expected))
    assert len(explanations) <= len(articles) * 6
    assert all(not article["related_articles"] for article in attach_related_research(articles, limit=0))


def _article(article_id, *, disease="D021", country="JP", topic="Surveillance", study="Cohort study", date="2026-01-01"):
    return {
        "article_id": article_id,
        "slug": article_id,
        "title": article_id,
        "journal": "Test Journal",
        "published_at": f"{date}T00:00:00+00:00",
        "study_type": study,
        "peer_review_status": "peer_reviewed",
        "diseases": [{"disease_id": disease, "name_en": disease}] if disease else [],
        "countries": [{"code": country}] if country else [],
        "topics": [{"name": topic}] if topic else [],
    }


def test_related_research_is_ranked_stably_and_excludes_self():
    result = attach_related_research([
        _article("source"),
        _article("strong", date="2026-02-01"),
        _article("topic-only", disease="D999", country="US", study="Guideline"),
        _article("unrelated", disease="D050", country="CD", topic="Treatment"),
    ])
    source = result[0]
    assert [item["article_id"] for item in source["related_articles"]] == ["strong", "topic-only"]
    assert source["related_articles"][0]["similarity_score"] > source["related_articles"][1]["similarity_score"]
    assert source["related_articles"][0]["reasons_en"] == [
        "Shared disease: D021", "Shared geography: JP", "Shared topic: Surveillance", "Same study type: Cohort study",
    ]
    assert all(item["article_id"] != "source" for item in source["related_articles"])


def test_geography_or_study_design_alone_never_recommends_an_unrelated_article():
    result = attach_related_research([
        _article("source", disease="D021", topic="Surveillance"),
        _article("same-place-only", disease="D050", topic="Treatment"),
    ])
    assert result[0]["related_articles"] == []


def test_related_research_projection_never_exports_private_source_fields():
    private = _article("private")
    private["abstract_text"] = "not public"
    private["source_payload"] = {"secret": True}
    result = attach_related_research([_article("source"), private])
    projection = result[0]["related_articles"][0]
    assert "abstract_text" not in projection
    assert "source_payload" not in projection


def test_related_cache_reuses_ranking_but_keeps_fresh_surveillance_signals(tmp_path, monkeypatch):
    path = tmp_path / "related.json"
    articles = [_article("a"), _article("b")]
    expected = attach_related_research(articles, cache_path=path)
    monkeypatch.setattr(recommendations, "_score", lambda *_: (_ for _ in ()).throw(AssertionError("unchanged ranking recomputed")))
    articles[0]["related_signals"] = [{"signal_id": "new-signal"}]
    actual = attach_related_research(articles, cache_path=path)
    assert actual[0]["related_signals"] == [{"signal_id": "new-signal"}]
    assert [row["related_articles"] for row in actual] == [row["related_articles"] for row in expected]


def test_related_cache_invalidates_every_projected_input_and_catalogue_change(tmp_path):
    import copy
    import json

    path = tmp_path / "related.json"
    source = [_article("a"), _article("b"), _article("c")]
    for field in recommendations._RELATED_INPUT_FIELDS:
        attach_related_research(source, cache_path=path)
        before = json.loads(path.read_text())["identity"]
        changed = copy.deepcopy(source)
        changed[1][field] = [] if field in {"diseases", "countries", "topics"} else "different"
        cached = attach_related_research(changed, cache_path=path)
        assert cached == attach_related_research(changed)
        assert json.loads(path.read_text())["identity"] != before
    for changed in [source[:2], source[::-1], [*source, _article("d")]]:
        assert attach_related_research(changed, cache_path=path) == attach_related_research(changed)
    assert attach_related_research(source, cache_path=path, limit=1) == attach_related_research(source, limit=1)


def test_corrupt_related_cache_is_recomputed(tmp_path):
    import json

    path = tmp_path / "related.json"
    source = [_article("a"), _article("b")]
    expected = attach_related_research(source, cache_path=path)
    saved = json.loads(path.read_text())
    saved["related"][0][0]["title"] = "corruption"
    path.write_text(json.dumps(saved))
    assert attach_related_research(source, cache_path=path) == expected
    path.write_text("invalid JSON")
    assert attach_related_research(source, cache_path=path) == expected


def test_incremental_related_ranking_recomputes_only_affected_topics(tmp_path, monkeypatch):
    path = tmp_path / 'related.json'
    source = [_article('a', disease='D001', topic=None), _article('b', disease='D001', topic=None),
              _article('c', disease='D002', topic=None), _article('d', disease='D002', topic=None)]
    attach_related_research(source, cache_path=path)
    changed = [*source, _article('new', disease='D001', topic=None)]
    original = recommendations._score
    seen = []
    def score(left, right):
        seen.append((left.diseases, right.diseases))
        return original(left, right)
    monkeypatch.setattr(recommendations, '_score', score)
    result = attach_related_research(changed, cache_path=path)
    assert seen and all('d002' not in left and 'd002' not in right for left, right in seen)
    monkeypatch.setattr(recommendations, '_score', original)
    assert result == attach_related_research(changed)
    moved = [*changed[:2], _article('c', disease='D001', topic=None), changed[3]]
    assert attach_related_research(moved, cache_path=path) == attach_related_research(moved)
