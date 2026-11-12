import pytest
from src.services.data_release.papers_template import assemble_paper, factor_paper, template_digest


def page(slug, title, version='v1'):
    return (f'<html><head><title>{title}</title><meta name="description" content="{title}">'
            f'<link rel="canonical" href="https://papers.example/research/articles/{slug}/">'
            f'<link rel="stylesheet" href="/_astro/{version}.css"></head>'
            f'<body><nav><a href="/research/articles/{slug}/">language</a></nav>'
            f'<main id="main-content"><h1>{title}</h1><p>{{{{MAIN}}}} {slug}</p></main>'
            f'<footer>{version}</footer></body></html>')


def test_round_trip_and_shared_shell():
    first = factor_paper(page('first', 'First &amp; safe'), 'first')
    second = factor_paper(page('second', 'Second'), 'second')
    assert first.template == second.template
    assert assemble_paper(first.template, first.record) == page('first', 'First &amp; safe')
    assert assemble_paper(second.template, second.record) == page('second', 'Second')
    assert template_digest(first.template) == template_digest(second.template)


def test_shared_style_update_does_not_change_article_record():
    old = factor_paper(page('first', 'First', 'v1'), 'first')
    new = factor_paper(page('first', 'First', 'v2'), 'first')
    assert old.record == new.record
    assert template_digest(old.template) != template_digest(new.template)
    assert assemble_paper(new.template, old.record) == page('first', 'First', 'v2')


def test_bad_structure_and_slots_fail_closed():
    with pytest.raises(ValueError): factor_paper('<html></html>', 'first')
    with pytest.raises(ValueError): factor_paper(page('first', 'First'), '../first')
    with pytest.raises(ValueError): assemble_paper({'schema': 1, 'chunks': [{'field': 'head', 'index': -1}]}, {'schema': 1, 'slug': 'first', 'head': ['x'], 'main': ''})
