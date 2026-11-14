from src.literature.recommendations import attach_related_research
from src.literature import recommendations
import pytest


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


def test_new_paper_in_a_broad_topic_merges_exact_top_k_instead_of_rescoring_every_old_pair(tmp_path, monkeypatch):
    path = tmp_path / "related.json"
    source = [_article(f"paper-{index:03}") for index in range(200)]
    attach_related_research(source, cache_path=path)
    # Equal scores/dates exercise the article-ID tiebreaker. The new winner
    # enters every old list, so no broad-topic target is wholly unaffected.
    changed = [*source, _article("zz-new")]
    original = recommendations._score
    calls = 0

    def counted(left, right):
        nonlocal calls
        calls += 1
        return original(left, right)

    monkeypatch.setattr(recommendations, "_score", counted)
    incremental = attach_related_research(changed, cache_path=path)
    incremental_calls, calls = calls, 0
    exhaustive = attach_related_research(changed)
    full_calls = calls
    assert incremental == exhaustive
    assert incremental[0]["related_articles"][0]["article_id"] == "zz-new"
    assert incremental_calls < full_calls / 10


@pytest.mark.parametrize("change", ["remove_winner", "remove_loser", "demote_winner", "promote_loser",
                                    "winner_projection", "target_labels", "reorder", "short_list"])
def test_incremental_ranking_preserves_vacancy_fill_ties_and_explanations(tmp_path, change):
    import copy

    path = tmp_path / "related.json"
    source = [_article(f"paper-{index:02}") for index in range(14)]
    if change == "short_list":
        source = source[:3]
    attach_related_research(source, cache_path=path)
    changed = copy.deepcopy(source)
    if change == "remove_winner":
        changed.pop(-1)
    elif change == "remove_loser":
        changed.pop(1)
    elif change == "demote_winner":
        changed[-1].update(diseases=[], topics=[])
    elif change == "promote_loser":
        changed[1]["published_at"] = "2026-02-01T00:00:00+00:00"
    elif change == "winner_projection":
        changed[-1].update(title="Updated title", journal="Changed journal", peer_review_status="preprint")
        changed[-1]["diseases"][0]["name_en"] = "Updated disease explanation"
    elif change == "target_labels":
        changed[0]["diseases"][0]["name_en"] = "Target disease explanation"
    elif change == "reorder":
        changed.reverse()
    else:
        changed.append(_article("new-short-list-member"))
    assert attach_related_research(changed, cache_path=path) == attach_related_research(changed)


@pytest.mark.parametrize("seed", [0, 19, 1729])
@pytest.mark.parametrize("limit", [1, 6, 80])
def test_random_incremental_edits_match_every_exhaustive_projection(tmp_path, seed, limit):
    import copy
    import random

    randomizer = random.Random(seed)
    path = tmp_path / "related.json"
    next_id = 0

    def make_article():
        nonlocal next_id
        next_id += 1
        return _article(f"paper-{next_id:04}",
                        disease=randomizer.choice(["D001", "D002", " d003 ", None]),
                        country=randomizer.choice(["JP", "US", None]),
                        topic=randomizer.choice(["Surveillance", "surveillance", "Vaccines", None]),
                        study=randomizer.choice(["Cohort study", "Guideline", None]),
                        date=randomizer.choice(["2026-01-01", "2026-02-01", "2010-01-01"]))

    source = [make_article() for _ in range(40)]
    attach_related_research(source, limit=limit, cache_path=path)
    for step in range(12):
        changed = copy.deepcopy(source)
        for _ in range(randomizer.randrange(3)):
            changed.pop(randomizer.randrange(len(changed)))
        changed.extend(make_article() for _ in range(randomizer.randrange(1, 4)))
        for _ in range(3):
            target = randomizer.choice(changed)
            replacement = make_article()
            field = randomizer.choice(["diseases", "countries", "topics", "study_type", "published_at", "title"])
            target[field] = replacement[field]
            target["related_signals"] = [{"signal_id": f"fresh-{step}"}]
        randomizer.shuffle(changed)
        assert attach_related_research(changed, limit=limit, cache_path=path) == attach_related_research(changed, limit=limit)
        source = changed


@pytest.mark.parametrize("corruption", ["unknown_implementation", "missing_implementation", "bad_schema",
                                        "non_list_row", "duplicate_winner", "missing_winner"])
def test_incompatible_or_structurally_invalid_cache_falls_back_to_full_ranking(tmp_path, monkeypatch, corruption):
    import hashlib
    import json

    path = tmp_path / "related.json"
    source = [_article(str(index)) for index in range(10)]
    attach_related_research(source, cache_path=path)
    saved = json.loads(path.read_text())
    if corruption == "unknown_implementation":
        saved["implementation"] = "0" * 64
    elif corruption == "missing_implementation":
        saved.pop("implementation")
    elif corruption == "bad_schema":
        saved["schema"] = 999
    elif corruption == "non_list_row":
        saved["related"][0] = {}
    elif corruption == "duplicate_winner":
        saved["related"][0][1] = saved["related"][0][0]
    else:
        saved["related"][0][0]["article_id"] = "missing"
    # Even a well-checksummed incompatible shape must never be merged.
    saved["sha256"] = hashlib.sha256(recommendations._json_bytes({
        "records": saved["records"], "related": saved["related"],
    })).hexdigest()
    path.write_text(json.dumps(saved))
    changed = [*source, _article("new")]
    original = recommendations._score
    counts = []

    def counted(left, right):
        counts.append(1)
        return original(left, right)

    monkeypatch.setattr(recommendations, "_score", counted)
    actual = attach_related_research(changed, cache_path=path)
    actual_count = len(counts)
    counts.clear()
    expected = attach_related_research(changed)
    assert actual == expected
    assert actual_count == len(counts)


@pytest.mark.parametrize("ids", [["same", "same"], ["", "other"], [None, "other"]])
def test_non_unique_or_missing_ids_keep_the_exhaustive_fallback(tmp_path, ids):
    path = tmp_path / "related.json"
    source = [_article(identity) for identity in ids]
    attach_related_research(source, cache_path=path)
    changed = [*source, _article("new")]
    assert attach_related_research(changed, cache_path=path) == attach_related_research(changed)
