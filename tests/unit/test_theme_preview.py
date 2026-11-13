import hashlib
import json

import pytest

from src.services.data_release.theme_preview import prepare_theme_previews, read_theme_preview


@pytest.fixture
def preview(tmp_path):
    root = tmp_path / "repo"
    dist = root / "astro-site/dist"
    (dist / "_astro").mkdir(parents=True)
    (dist / "_astro/app.css").write_text(':root {--color-teal:15 118 110} @font-face {src:url("./font.woff2")}')
    for prefix in ("", "zh/", "fr/"):
        for page in ("index.html", "countries/cn/index.html"):
            path = dist / (prefix + page)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<html class="dark" data-theme="dark"><head><base href="https://elsewhere.invalid">'
                            '<meta http-equiv="Refresh" content="0;url=https://elsewhere.invalid">'
                            '<link rel="stylesheet" href="/_astro/app.css">'
                            '<link rel="stylesheet" href="https://live.invalid/theme.css">'
                            '<script>window.unsafe = true;</script></head><body onload="unsafe()">'
                            '<a href="/publish" target="_top">A page</a><main>Frozen public content</main></body></html>')
    directory = tmp_path / "plan"
    css = b":root {--color-teal:21 129 119}"
    sha = hashlib.sha256(css).hexdigest()
    key = f"theme/versions/sha256/{sha}.css"
    asset = directory / key
    asset.parent.mkdir(parents=True)
    asset.write_bytes(css)
    (directory / "theme-candidate.json").write_text(json.dumps({"objects": [{"key": key, "sha256": sha, "bytes": len(css)}]}))
    return root, directory


def test_preview_freezes_theme_and_all_six_samples_without_live_scripts_or_styles(preview):
    root, directory = preview
    index = prepare_theme_previews(root, directory, "https://main.example.org")
    assert len(index["pages"]) == 6 and index["missing"] == []
    body, headers = read_theme_preview(directory, "zh", "country")
    assert b"21 129 119" in body and b"Frozen public content" in body
    for absent in (b"<script", b"window.unsafe", b"onload=", b"live.invalid", b"elsewhere.invalid", b'target="_top"'):
        assert absent not in body
    assert b"https://main.example.org/_astro/font.woff2" in body
    assert "script-src 'none'" in headers["Content-Security-Policy"]
    assert "sandbox" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "private, no-store"
    (root / "astro-site/dist/zh/countries/cn/index.html").write_text("changed afterwards")
    assert prepare_theme_previews(root, directory, "https://main.example.org") == index
    assert read_theme_preview(directory, "zh", "country")[0] == body


def test_preview_rejects_tampering_and_path_traversal(preview):
    root, directory = preview
    prepare_theme_previews(root, directory, "https://main.example.org")
    with pytest.raises(ValueError):
        read_theme_preview(directory, "../../etc", "home")
    (directory / "theme-previews/en-home.html").write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        read_theme_preview(directory, "en", "home")


def test_missing_samples_are_reported_without_substituting_live_pages(preview):
    root, directory = preview
    (root / "astro-site/dist/fr/index.html").unlink()
    index = prepare_theme_previews(root, directory, "https://main.example.org")
    assert index["missing"] == ["fr/home"]
    with pytest.raises(ValueError, match="unavailable"):
        read_theme_preview(directory, "fr", "home")
