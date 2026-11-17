import hashlib
import re

import pytest

from src.services.data_release import papers_projection
from src.services.data_release.papers_styles import verify_projected_template_styles
from tests.unit.test_papers_content import make_candidate
from tests.unit.test_papers_projection import article, initial


def shell(markup):
    return {'locale': 'zh', 'shell': {'chunks': ['<head>', {'field': 'head', 'index': 0},
            markup, '</head><body><a class="skip-link">Skip</a>', {'field': 'main'}, '</body>']}}


def test_missing_styles_and_external_theme_cannot_pass_article_style_check(tmp_path):
    kwargs = {'root': tmp_path, 'assets': [], 'origin': 'https://papers.example'}
    with pytest.raises(ValueError, match='missing CSS'):
        verify_projected_template_styles(shell(''), **kwargs)
    with pytest.raises(ValueError, match='missing global layout CSS'):
        verify_projected_template_styles(shell('<link href="https://theme.example/theme.css" rel="stylesheet">'), **kwargs)
    with pytest.raises(ValueError, match='missing article layout CSS'):
        verify_projected_template_styles(shell('<style>.skip-link{position:fixed}</style>'), **kwargs)
    verify_projected_template_styles(shell('<style>.skip-link{position:fixed}.evidence-page[data-astro-cid-abc123]{width:90%}</style>'), **kwargs)


def test_bundled_global_and_article_styles_must_match_frozen_assets(tmp_path):
    path = tmp_path / '_astro/layout.css'
    path.parent.mkdir()
    body = b'.skip-link{position:fixed}.evidence-page[data-astro-cid-abc123]{width:90%}'
    path.write_bytes(body)
    assets = [{'path': '_astro/layout.css', 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}]
    template = shell('<link rel="stylesheet" href="/_astro/layout.css">')
    verify_projected_template_styles(template, root=tmp_path, assets=assets, origin='https://papers.example')
    path.write_text('/* changed stylesheet */')
    with pytest.raises(ValueError, match='stylesheet changed'):
        verify_projected_template_styles(template, root=tmp_path, assets=assets, origin='https://papers.example')


@pytest.mark.parametrize('mode', ['body', 'full'])
def test_reusing_unstyled_template_fails_before_candidate_is_saved(tmp_path, monkeypatch, mode):
    base = initial(tmp_path)
    read = papers_projection._read_object

    def unstyled(candidate, key):
        value = read(candidate, key)
        if '/templates/' in key:
            value['shell']['chunks'] = [re.sub(r'<link[^>]*rel="stylesheet"[^>]*>', '', chunk)
                if isinstance(chunk, str) else chunk for chunk in value['shell']['chunks']]
        return value

    monkeypatch.setattr(papers_projection, '_read_object', unstyled)
    destination = tmp_path / 'broken'
    with pytest.raises(ValueError, match='template is missing CSS'):
        if mode == 'body':
            papers_projection.prepare_projection_candidate(base, articles=[article('paper-a', 'Updated')],
                directory=destination, release_id='next')
        else:
            static = make_candidate(tmp_path / 'static', {'research/index.html': 'Collection'})
            papers_projection.prepare_full_snapshot_candidate(base, articles=[article('paper-a')], static=static,
                directory=destination, release_id=static.release_id)
    assert not (destination / 'candidate.json').exists()
