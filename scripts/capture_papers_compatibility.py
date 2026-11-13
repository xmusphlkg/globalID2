#!/usr/bin/env python3
"""Read-only production Papers baseline and compatibility comparison."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.request


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links, self.seo, self.resources, self.structured = [], [], [], [], []
        self.hidden = 0
        self.script = None
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('style', 'script'):
            self.hidden += 1
        if tag == 'script':
            self.script = attrs
            self.buffer = []
            src = attrs.get('src', '')
            if src and '/cdn-cgi/' not in src and 'cloudflareinsights.com' not in src:
                self.resources.append(['script', src])
        if tag == 'a':
            href = attrs.get('href', '')
            if '/cdn-cgi/l/email-protection#' in href:
                encoded = bytes.fromhex(href.split('#', 1)[1])
                href = 'mailto:' + bytes(x ^ encoded[0] for x in encoded[1:]).decode()
            if '/cdn-cgi/' not in href:
                self.links.append([href, attrs.get('hreflang'), attrs.get('download')])
        if tag == 'link':
            if attrs.get('rel') in ('canonical', 'alternate'):
                self.seo.append([tag, sorted(attrs.items())])
            elif attrs.get('rel') == 'stylesheet':
                self.resources.append(['style', attrs.get('href')])
        if tag == 'meta' and (attrs.get('name') in ('description', 'robots') or attrs.get('property', '').startswith('og:')):
            self.seo.append([tag, sorted(attrs.items())])

    def handle_endtag(self, tag):
        if tag == 'script':
            if self.script and self.script.get('type') == 'application/ld+json':
                raw = ''.join(self.buffer)
                try:
                    self.structured.append(json.loads(raw))
                except ValueError:
                    self.structured.append(raw)
            self.script = None
        if tag in ('style', 'script'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.script is not None:
            self.buffer.append(data)
        elif not self.hidden:
            value = re.sub(r'\s+', ' ', data).strip()
            if value:
                self.text.append(value)

    def contract(self):
        return {key: getattr(self, key) for key in ('text', 'links', 'seo', 'resources', 'structured')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--slug', default='10-1371-journal-pntd-0014184-3645e0a4')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    previous = {row['url']: row for row in json.loads((args.compare / 'verification.json').read_text())['results']} if args.compare else {}
    results = []
    for host in ['globalinfectiousdisease.com', 'www.globalinfectiousdisease.com', 'papers.globalinfectiousdisease.com']:
        for locale in ['', 'zh/', 'fr/']:
            url = f'https://{host}/{locale}research/articles/{args.slug}/'
            name = f'{host}-{locale.strip("/") or "en"}'
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read()
                headers = {k: v for k, v in response.headers.items() if k.lower().startswith('x-gids-') or k.lower() in ('etag', 'content-type')}
                result = {'url': url, 'status': response.status, 'headers': headers, 'final_url': response.url,
                          'name': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            (args.output / f'{name}.html').write_bytes(body)
            doc = Document()
            doc.feed(body.decode('utf-8'))
            normalized = json.dumps(doc.contract(), ensure_ascii=False, sort_keys=True, indent=2)
            (args.output / f'{name}.normalized.json').write_text(normalized)
            result['normalized_sha256'] = hashlib.sha256(normalized.encode()).hexdigest()
            if args.compare:
                old = json.loads((args.compare / f'{name}.normalized.json').read_text())
                new = doc.contract()
                # JSON round-trip normalizes tuples in sorted HTML attributes.
                new = json.loads(json.dumps(new))
                result['changed_fields'] = [key for key in old if old[key] != new[key]]
                old_headers = {k.lower(): v for k, v in previous[url]['headers'].items()}
                new_headers = {k.lower(): v for k, v in headers.items()}
                result['changed_headers'] = [key for key in ('x-gids-paper-mode', 'x-gids-paper-sha256', 'x-gids-research-release')
                                             if old_headers.get(key) != new_headers.get(key)]
                result['matched'] = not result['changed_fields'] and not result['changed_headers']
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    receipt = {'scope': 'Read-only public Papers HTML, text, links, SEO, resource and structured-data contracts', 'results': results}
    (args.output / 'verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    if args.compare and not all(row['matched'] for row in results):
        raise SystemExit('Papers compatibility comparison failed')


if __name__ == '__main__':
    main()
