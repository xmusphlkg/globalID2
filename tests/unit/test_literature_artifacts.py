from pathlib import Path

from src.services.data_release.artifact_index import ArtifactIndex
from src.services.data_release.literature_artifacts import build_research_papers_manifest


def test_only_a_trusted_matching_build_reuses_article_receipts(tmp_path):
    root = tmp_path / 'stage'
    for path, value in {'research/articles/a/index.html': 'page a', 'research/articles/b/index.html': 'new b', '_astro/site.css': 'css'}.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value)
    articles = {'a': {'digest': 'input-a', 'indexable': True}, 'b': {'digest': 'new-b', 'indexable': True}}
    build = {'schema': 1, 'inputs': {'schema': 1, 'shared': 'shared', 'articles': articles},
             'reused': ['a'], 'previous_cache_version': 'committed'}
    prior = {'build': {'cache_version': 'committed', 'inputs': {'shared': 'shared', 'articles': articles}},
             'files': [{'path': 'research/articles/a/index.html', 'sha256': 'a' * 64, 'bytes': 6}]}
    index = ArtifactIndex(root, cache_dir=tmp_path / 'index')
    manifest = build_research_papers_manifest(root, origin='https://papers.example', build=build, previous=prior, index=index)
    assert index.read_files == 2
    entry = next(item for item in manifest['files'] if item['path'] == 'research/articles/a/index.html')
    assert entry['sha256'] == 'a' * 64
    build['previous_cache_version'] = 'uncommitted'
    other = ArtifactIndex(root, cache_dir=tmp_path / 'fresh-index')
    build_research_papers_manifest(root, origin='https://papers.example', build=build, previous=prior, index=other)
    assert other.read_files == 3
