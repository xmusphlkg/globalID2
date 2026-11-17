"""Reject projected article shells that cannot style the rendered document."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit

from .papers_immutable import _source


class _ShellStyles(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stylesheets: list[str] = []
        self.inline: list[str] = []
        self.in_style = False
        self.has_layout = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and 'stylesheet' in (attrs.get('rel') or '').lower().split():
            if attrs.get('href'):
                self.stylesheets.append(attrs['href'])
        if tag == 'style':
            self.in_style = True
        if 'skip-link' in (attrs.get('class') or '').split():
            self.has_layout = True

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.inline.append(data)


def verify_projected_template_styles(template: dict, *, root: Path, assets: list[dict], origin: str) -> None:
    """Check retained shells as well as newly compiled ones before publication.

    Asset closure alone succeeds when a broken build omits every CSS reference.
    Production GIDS shells need both global layout and article rules; an
    independent theme stylesheet supplies variables, not those layout rules.
    """
    parser = _ShellStyles()
    parser.feed(''.join(chunk for chunk in template['shell']['chunks'] if isinstance(chunk, str)))
    if not parser.stylesheets and not any(css.strip() for css in parser.inline):
        raise ValueError(f"Projected {template['locale']} template is missing CSS")
    if not parser.has_layout:
        return

    styles = list(parser.inline)
    available = {entry['path']: entry for entry in assets}
    local_hosts = {urlsplit(origin).netloc, 'globalinfectiousdisease.com',
                   'www.globalinfectiousdisease.com', 'papers.globalinfectiousdisease.com'}
    for href in parser.stylesheets:
        parsed = urlsplit(href)
        if parsed.netloc and parsed.netloc not in local_hosts:
            continue
        path = parsed.path.lstrip('/')
        entry = available.get(path)
        if entry is None:
            continue
        body = _source(root, path).read_bytes()
        if len(body) != entry['bytes'] or hashlib.sha256(body).hexdigest() != entry['sha256']:
            raise ValueError(f'Projected template stylesheet changed: {path}')
        styles.append(body.decode('utf-8'))
    css = re.sub(r'/\*[\s\S]*?\*/', '', '\n'.join(styles))
    for selector, label in (('skip-link', 'global layout'), ('evidence-page', 'article layout')):
        if not re.search(r'\.' + selector + r'(?=[\s:{.,#\[])', css):
            raise ValueError(f"Projected {template['locale']} template is missing {label} CSS")
