import hashlib
import json
from pathlib import Path
import pytest
from scripts.prepare_papers_templates import prepare
from src.services.data_release.papers_template import assemble_paper


def page(slug, version):
    return (f'<html><head><title>{slug}</title><meta name="description" content="{slug}">'
            f'<link rel="canonical" href="https://example.test/{slug}"><link rel="stylesheet" href="/{version}.css">'
            f'</head><body><a href="/{slug}">language</a><main id="main-content">{slug}</main><footer>{version}</footer></body></html>')


def populate(root, version):
    for prefix in ('', 'zh', 'fr'):
        for slug in ('paper-a', 'paper-b'):
            p = root / prefix / 'research/articles' / slug / 'index.html'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(page(slug, version))


def test_style_only_reuses_all_records_and_indexes(tmp_path):
    src=tmp_path/'source'; populate(src, 'old')
    first=tmp_path/'first'; prepare(src, first)
    populate(src, 'new'); second=tmp_path/'second'; prepare(src, second)
    for name in ('records','indexes'):
        assert {p.name for p in (first/name).iterdir()} == {p.name for p in (second/name).iterdir()}
    assert {p.name for p in (first/'templates').iterdir()} != {p.name for p in (second/'templates').iterdir()}
    pointer=json.loads((second/'current.json').read_bytes())
    gen=json.loads((second/'generations'/f"{pointer['generation']}.json").read_bytes())
    spec=gen['locales']['en']; bucket=hashlib.sha256(b'paper-a').hexdigest()[:2]
    index=json.loads((second/'indexes'/f"{gen['indexes'][bucket]}.json").read_bytes())
    record=json.loads((second/'records'/f"{index['items']['paper-a']}.json").read_bytes())
    template=json.loads((second/'templates'/f"{spec['template']}.json").read_bytes())
    assert assemble_paper(template,{'schema':1,'slug':record['slug'],**record['locales']['en']}) == page('paper-a','new')


def test_refuses_inconsistent_template_and_cleans_partial_candidate(tmp_path):
    src=tmp_path/'source'; populate(src,'old')
    (src/'research/articles/paper-b/index.html').write_text(page('paper-b','unexpected'))
    out=tmp_path/'candidate'
    with pytest.raises(ValueError, match='template shapes'): prepare(src,out)
    assert not out.exists()
    assert not list(tmp_path.glob('.papers-template-*'))


def test_changed_article_replaces_only_one_record_and_index(tmp_path):
    src = tmp_path / 'source'
    populate(src, 'same')
    first = tmp_path / 'first'
    prepare(src, first)
    article = src / 'research/articles/paper-a/index.html'
    article.write_text(article.read_text().replace('>paper-a</main>', '>updated paper-a</main>'))
    second = tmp_path / 'second'
    prepare(src, second)
    def names(root, group):
        return {path.name for path in (root / group).iterdir()}
    assert names(first, 'templates') == names(second, 'templates')
    assert len(names(second, 'records') - names(first, 'records')) == 1
    assert len(names(second, 'indexes') - names(first, 'indexes')) == 1


def test_meta_attribute_gt_does_not_change_template_shape(tmp_path):
    src = tmp_path / 'source'
    populate(src, 'same')
    description = 'HIV suppression threshold >200 copies/mL'
    for prefix in ('', 'zh', 'fr'):
        page_path = src / prefix / 'research/articles/paper-a/index.html'
        html = page_path.read_text()
        html = html.replace('content="paper-a"', f'content="{description}"')
        page_path.write_text(html)

    output = tmp_path / 'candidate'
    prepare(src, output)

    pointer = json.loads((output / 'current.json').read_bytes())
    generation = json.loads((output / 'generations' / f"{pointer['generation']}.json").read_bytes())
    assert generation['locales']['fr']['template'] == generation['locales']['en']['template']
