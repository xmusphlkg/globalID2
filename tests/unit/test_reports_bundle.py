import hashlib
import json
from pathlib import Path

from src.services.data_release.papers_r2 import R2PapersConfig, publish_papers_site, _content_type_for
from src.services.data_release.reports_bundle import (
    DEFAULT_REPORTS_ORIGIN,
    MANIFEST_KEY,
    split_reports_bundle,
)


def test_split_moves_report_pages_rewrites_shared_links_and_adds_redirects(tmp_path: Path):
    dist = tmp_path / "dist"
    reports = tmp_path / "reports-site"
    (dist / "countries/jp/reports/50").mkdir(parents=True)
    (dist / "zh/countries/jp/reports").mkdir(parents=True)
    (dist / "reports-host").mkdir(parents=True)
    (dist / "reports").mkdir(parents=True)
    (dist / "zh/reports").mkdir(parents=True)
    (dist / "_astro").mkdir()
    (dist / "_astro/site.css").write_text("body { color: black; }", encoding="utf-8")
    (dist / "_headers").write_text(
        "/_astro/*\n  Access-Control-Allow-Origin: https://papers.globalinfectiousdisease.com\n"
        "/release.json\n  Cache-Control: no-store\n",
        encoding="utf-8",
    )
    (dist / "countries/jp/reports/index.html").write_text(
        '<link rel="canonical" href="https://globalinfectiousdisease.com/countries/jp/reports/">'
        '<link rel="stylesheet" href="/_astro/site.css">'
        '<a href="/countries/jp/reports/50/">report</a>'
        '<a href="/countries/jp/">country</a><a href="/reports/">archive</a><a href="/zh/">home</a>',
        encoding="utf-8",
    )
    (dist / "countries/jp/reports/50/index.html").write_text("<html>report</html>", encoding="utf-8")
    (dist / "site-data/reports").mkdir(parents=True)
    (dist / "site-data/reports/index.json").write_text('{"reports":[]}', encoding="utf-8")
    (dist / "site-data/reports/50.json").write_text('{"id":50}', encoding="utf-8")
    (dist / "zh/countries/jp/reports/index.html").write_text("<html>中文</html>", encoding="utf-8")
    (dist / "reports/index.html").write_text(
        '<link rel="canonical" href="https://globalinfectiousdisease.com/reports/">main archive',
        encoding="utf-8",
    )
    (dist / "zh/reports/index.html").write_text(
        '<link rel="canonical" href="https://globalinfectiousdisease.com/zh/reports/">中文目录',
        encoding="utf-8",
    )
    (dist / "reports-host/sitemap.xml").write_text("<urlset></urlset>", encoding="utf-8")
    (dist / "reports-host/robots.txt").write_text("User-agent: *\n", encoding="utf-8")

    result = split_reports_bundle(dist, reports_dir=reports)

    assert not (dist / "countries/jp/reports/index.html").exists()
    rewritten = (reports / "countries/jp/reports/index.html").read_text(encoding="utf-8")
    assert 'href="https://reports.globalinfectiousdisease.com/countries/jp/reports/"' in rewritten
    assert 'href="/_astro/site.css"' in rewritten
    assert (reports / "_astro/site.css").is_file()
    assert 'href="/countries/jp/reports/50/"' in rewritten
    assert 'href="https://globalinfectiousdisease.com/countries/jp/"' in rewritten
    assert 'href="/reports/"' in rewritten
    assert 'href="https://globalinfectiousdisease.com/zh/"' in rewritten
    assert not (dist / "reports/index.html").exists()
    directory = (reports / "reports/index.html").read_text(encoding="utf-8")
    assert 'href="https://reports.globalinfectiousdisease.com/reports/"' in directory
    assert "main archive" in directory
    assert 'href="https://reports.globalinfectiousdisease.com/zh/reports/"' in (reports / "zh/reports/index.html").read_text(encoding="utf-8")
    assert (reports / "sitemap.xml").is_file()
    assert (reports / "robots.txt").is_file()
    sitemap = (reports / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://reports.globalinfectiousdisease.com/reports/" in sitemap
    assert "https://reports.globalinfectiousdisease.com/zh/reports/" in sitemap

    redirects = (dist / "_redirects").read_text(encoding="utf-8")
    assert "/countries/jp/reports/ https://reports.globalinfectiousdisease.com/countries/jp/reports/ 301" in redirects
    assert "/countries/jp/reports/* https://reports.globalinfectiousdisease.com/countries/jp/reports/:splat 301" in redirects
    assert "/reports/ https://reports.globalinfectiousdisease.com/reports/ 301" in redirects
    assert "/site-data/reports/* https://reports.globalinfectiousdisease.com/site-data/reports/:splat 301" in redirects
    headers = (dist / "_headers").read_text(encoding="utf-8")
    assert "Access-Control-Allow-Origin: https://papers.globalinfectiousdisease.com" not in headers
    assert "Access-Control-Allow-Origin: *" in headers
    astro_rule = headers.split("/_astro/*", 1)[1].split("\n\n", 1)[0]
    assert astro_rule.count("Access-Control-Allow-Origin:") == 1
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert next(row for row in manifest["files"] if row["path"] == "countries/jp/reports/50/index.html")["key"] == "countries/jp/reports/50/"
    assert next(row for row in manifest["files"] if row["path"] == "countries/jp/reports/50/index.html")["url"] == "https://reports.globalinfectiousdisease.com/countries/jp/reports/50/"
    assert next(row for row in manifest["files"] if row["path"] == "reports/index.html")["key"] == "reports/"
    assert not (dist / "site-data/reports/index.json").exists()
    assert (reports / "site-data/reports/index.json").is_file()
    assert next(row for row in manifest["files"] if row["path"] == "site-data/reports/50.json")["url"] == "https://reports.globalinfectiousdisease.com/site-data/reports/50.json"
    assert MANIFEST_KEY == "reports-manifest.json"


def test_report_asset_mime_types_allow_module_hydration():
    assert _content_type_for("_astro/panel.js") == "text/javascript; charset=utf-8"
    assert _content_type_for("_astro/panel.css") == "text/css; charset=utf-8"
    assert _content_type_for("_astro/font.woff2") == "font/woff2"


def test_report_bundle_includes_hydration_dynamic_imports_and_fonts(tmp_path: Path):
    dist, reports = tmp_path / "dist", tmp_path / "reports"
    (dist / "reports").mkdir(parents=True)
    (dist / "_astro").mkdir()
    (dist / "reports/index.html").write_text('<astro-island component-url="/_astro/panel.js" renderer-url="/_astro/client.js"></astro-island>')
    (dist / "_astro/panel.js").write_text('import "./base.js"; import(`./chart.js`); const styles=[`_astro/plot.css`];')
    (dist / "_astro/client.js").write_text('import "./base.js";')
    (dist / "_astro/base.js").write_text('export const base = 1;')
    (dist / "_astro/chart.js").write_text('export const chart = 2;')
    (dist / "_astro/plot.css").write_text('@font-face{src:url("./font.woff2")}')
    (dist / "_astro/font.woff2").write_bytes(b"font")
    (dist / "_astro/unrelated.js").write_text('export const unrelated = true;')
    result = split_reports_bundle(dist, reports_dir=reports)
    paths = {row["path"] for row in json.loads(result.manifest_path.read_text())["files"]}
    assert {"_astro/panel.js", "_astro/client.js", "_astro/base.js", "_astro/chart.js", "_astro/plot.css", "_astro/font.woff2"} <= paths
    assert "_astro/unrelated.js" not in paths
    assert (dist / "_astro/panel.js").exists()


class MemoryR2Client:
    def __init__(self):
        self.puts = {}

    def get_object(self, key):
        assert key == "reports-manifest.json"
        return None

    def list_objects(self):
        return {}

    def put_object(self, key, body, **kwargs):
        self.puts[key] = (body, kwargs)

    def delete_object(self, key):
        self.puts.pop(key, None)


def test_shared_incremental_publisher_accepts_reports_manifest_and_slash_keys(tmp_path: Path):
    source = tmp_path / "bundle"
    page = source / "countries/jp/reports/50/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("<html>report</html>", encoding="utf-8")
    body = page.read_bytes()
    (source / MANIFEST_KEY).write_text(json.dumps({
        "schema_version": 1,
        "reports_origin": DEFAULT_REPORTS_ORIGIN,
        "file_count": 1,
        "files": [{
            "path": "countries/jp/reports/50/index.html",
            "key": "countries/jp/reports/50/",
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "url": f"{DEFAULT_REPORTS_ORIGIN}/countries/jp/reports/50/",
        }],
    }), encoding="utf-8")
    config = R2PapersConfig.from_mapping({
        "account_id": "account",
        "bucket": "globalid-reports",
        "access_key_id": "access",
        "secret_access_key": "secret",
        "public_base_url": DEFAULT_REPORTS_ORIGIN,
        "manifest_key": MANIFEST_KEY,
    })
    client = MemoryR2Client()

    result = publish_papers_site(
        source,
        config,
        client=client,
        verify_public=False,
        workers=1,
        lock_dir=tmp_path / "locks",
    )

    assert result.uploaded == 1
    assert result.manifest_url == f"{DEFAULT_REPORTS_ORIGIN}/{MANIFEST_KEY}"
    assert "countries/jp/reports/50/" in client.puts
    assert MANIFEST_KEY in client.puts
