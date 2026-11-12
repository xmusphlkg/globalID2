"""Factor a rendered public paper into a shared shell and per-paper JSON.

The payload contains only fragments already present in public HTML; it never
serializes private database records.  This module is a migration primitive:
publication continues to serve existing static pages until the new serving and
rollback paths are independently verified.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

_HEAD = re.compile(r'(?is)<head(?:\s[^>]*)?>.*?</head>')
_MAIN = re.compile(r'''(?is)<main\b[^>]*\bid=["']main-content["'][^>]*>.*?</main>''')
_TAG = re.compile(r'(?is)<title\b[^>]*>.*?</title>|<meta\b[^>]*>|<link\b[^>]*>|<script\b[^>]*>.*?</script>')
_DYNAMIC_LINK = re.compile(r'''(?is)\brel=["'](?:canonical|alternate)["']''')
_DYNAMIC_SCRIPT = re.compile(r'''(?is)\btype=["']application/ld\+json["']''')
_SLUG = re.compile(r'[a-z0-9][a-z0-9-]*\Z')


@dataclass(frozen=True)
class FactoredPaper:
    template: dict
    record: dict


def factor_paper(html: str, slug: str) -> FactoredPaper:
    """Factor one public page; fail closed on unknown document structure.

    The shell is an ordered sequence of literal strings and typed slots.
    This avoids interpreting marker-like text in an article as instructions.
    """
    if not isinstance(html, str) or not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ValueError('Invalid paper HTML or slug')
    heads, mains = list(_HEAD.finditer(html)), list(_MAIN.finditer(html))
    if len(heads) != 1 or len(mains) != 1 or heads[0].end() > mains[0].start():
        raise ValueError('Expected exactly one head and main-content element')
    head, main = heads[0], mains[0]
    dynamic = []
    for tag in _TAG.finditer(html, head.start(), head.end()):
        source = tag.group()
        if (source.lower().startswith(('<title', '<meta'))
                or _DYNAMIC_LINK.search(source) or _DYNAMIC_SCRIPT.search(source)):
            dynamic.append((tag.start(), tag.end(), {'field': 'head', 'index': len(dynamic)}))
    if not any(html[start:end].lower().startswith('<title') for start, end, _ in dynamic):
        raise ValueError('Paper title is missing')
    head_values = [html[start:end] for start, end, _ in dynamic]
    dynamic.append((main.start(), main.end(), {'field': 'main'}))
    # Locale links and the no-script analytics URL contain the slug outside
    # the article body.  Match only literal occurrences outside dynamic slots.
    covered = sorted(dynamic)
    cursor = 0
    slug_spans = []
    for start, end, _ in covered + [(len(html), len(html), {})]:
        for match in re.finditer(re.escape(slug), html[cursor:start]):
            slug_spans.append((cursor + match.start(), cursor + match.end(), {'field': 'slug'}))
        cursor = end
    spans = sorted(dynamic + slug_spans)
    chunks = []
    cursor = 0
    for start, end, slot in spans:
        if start < cursor:
            raise ValueError('Overlapping document slots')
        chunks.extend((html[cursor:start], slot))
        cursor = end
    chunks.append(html[cursor:])
    template = {'schema': 1, 'chunks': chunks}
    record = {'schema': 1, 'slug': slug, 'head': head_values, 'main': main.group()}
    return FactoredPaper(template, record)


def template_digest(template: dict) -> str:
    raw = json.dumps(template, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def assemble_paper(template: dict, record: dict) -> str:
    """Reconstruct a page byte-for-byte from a validated public record."""
    if template.get('schema') != 1 or record.get('schema') != 1 or not _SLUG.fullmatch(record.get('slug', '')):
        raise ValueError('Invalid paper template or record')
    if not isinstance(template.get('chunks'), list) or not isinstance(record.get('head'), list) or not isinstance(record.get('main'), str):
        raise ValueError('Invalid paper template or record')
    out = []
    for chunk in template['chunks']:
        if isinstance(chunk, str):
            out.append(chunk)
        elif isinstance(chunk, dict) and chunk == {'field': 'slug'}:
            out.append(record['slug'])
        elif isinstance(chunk, dict) and chunk == {'field': 'main'}:
            out.append(record['main'])
        elif isinstance(chunk, dict) and set(chunk) == {'field', 'index'} and chunk['field'] == 'head' and type(chunk['index']) is int:
            index = chunk['index']
            if index < 0 or index >= len(record['head']) or not isinstance(record['head'][index], str):
                raise ValueError('Invalid paper head slot')
            out.append(record['head'][index])
        else:
            raise ValueError('Invalid paper template slot')
    return ''.join(out)
