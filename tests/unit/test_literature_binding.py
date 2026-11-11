import pytest

from src.generation.literature_publication import freeze_literature_json
from src.services.data_release.literature_binding import bind_literature_candidate
from src.services.data_release.papers_bundle import build_papers_manifest
from src.services.data_release.papers_content import prepare_papers_content
from src.services.data_release.papers_immutable import prepare_immutable_papers


def test_binding_uses_one_release_identity_without_reading_pages(tmp_path):
    source, papers = tmp_path / "source", tmp_path / "papers"
    source.mkdir()
    (source / "index.json").write_text('{}')
    data = freeze_literature_json(source, tmp_path / "data", release_id="one", generated_at="t")
    papers.mkdir()
    (papers / "article.html").write_text('A')
    split = build_papers_manifest(papers, papers_origin="https://papers.example")
    prepared = prepare_immutable_papers(papers_dir=papers, manifest=split, papers_origin="https://papers.example")
    content = prepare_papers_content(prepared, release_id="one")
    # Binding is metadata-only, even after the private source is removed.
    (papers / "article.html").unlink()
    bound = bind_literature_candidate(data, content, expected_papers_origin="https://papers.example")
    assert bound['papers']['papers_generation_sha256'] == content.generation
    assert bind_literature_candidate(bound, content, expected_papers_origin="https://papers.example") == bound
    with pytest.raises(ValueError, match="identities differ"):
        bind_literature_candidate({**data, "release_id": "other"}, content, expected_papers_origin="https://papers.example")
    with pytest.raises(ValueError, match="origin"):
        bind_literature_candidate(data, content, expected_papers_origin="https://other.example")
