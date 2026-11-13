"""Freeze script-free representative HTML with a plan's exact theme CSS.

Preview pages are private review artifacts. They never read current theme
entries and cannot publish, collect analytics, submit forms or run scripts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

from .surveillance_publish import _atomic_receipt

LOCALES = ("en", "zh", "fr")
FAMILIES = ("home", "country")
MAX_HTML = 8 * 1024 * 1024


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid public preview origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def _file(root: Path, relative: str, limit: int = MAX_HTML) -> bytes:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Preview input is absent or outside its frozen root")
    return path.read_bytes()


def _css_urls(css: str, location: str) -> str:
    def absolute(match):
        value = match.group(2).strip()
        if value.startswith(("data:", "#")):
            return match.group(0)
        return 'url("' + urljoin(location, value).replace('"', '%22') + '")'
    return re.sub(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", absolute, css, flags=re.I)


def prepare_theme_previews(root: Path, directory: Path, public_origin: str) -> dict:
    """Call once after theme preparation; reuse existing verified frozen files."""
    index_path = directory / "theme-previews.json"
    if index_path.exists():
        index = json.loads(_file(directory, "theme-previews.json", 128 * 1024))
        for key in index["pages"]:
            locale, family = key.split("/")
            read_theme_preview(directory, locale, family)
        return index
    origin = _origin(public_origin)
    candidate = json.loads(_file(directory, "theme-candidate.json", 128 * 1024))
    css_entry = candidate["objects"][0]
    if not re.fullmatch(r"theme/versions/sha256/[a-f0-9]{64}\.css", css_entry["key"]):
        raise ValueError("Invalid theme preview asset")
    css_bytes = _file(directory, css_entry["key"], 128 * 1024)
    if hashlib.sha256(css_bytes).hexdigest() != css_entry["sha256"] or len(css_bytes) != css_entry["bytes"]:
        raise ValueError("Theme preview CSS failed integrity verification")
    css = css_bytes.decode("utf-8")
    if "</style" in css.lower():
        raise ValueError("Invalid theme CSS content")
    dist = root / "astro-site/dist"
    output = directory / "theme-previews"
    output.mkdir(parents=True, exist_ok=True)
    pages = {}
    missing = []
    for locale in LOCALES:
        prefix = "" if locale == "en" else locale + "/"
        for family in FAMILIES:
            path = prefix + ("index.html" if family == "home" else "countries/cn/index.html")
            try:
                body = _file(dist, path)
            except (OSError, ValueError):
                missing.append(f"{locale}/{family}")
                continue
            document = html.document_fromstring(body)
            head = document.find("head")
            if head is None:
                raise ValueError("Preview HTML has no head")
            for node in document.xpath("//script | //iframe | //object | //embed | //base | //meta[translate(@http-equiv,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='refresh']"):
                node.getparent().remove(node)
            for node in document.iter():
                for name in list(node.attrib):
                    if name.lower().startswith("on"):
                        del node.attrib[name]
                if node.tag == "a":
                    node.set("href", "#")
                    node.attrib.pop("target", None)
            base = etree.Element("base", href=origin + "/")
            head.insert(0, base)
            for link in list(document.xpath("//link[@rel='stylesheet']")):
                href = link.get("href", "")
                parsed = urlsplit(urljoin(origin + "/" + path, href))
                if parsed.netloc != urlsplit(origin).netloc or not parsed.path.startswith("/_astro/"):
                    # Exclude mutable theme entries and other live CSS sources.
                    link.getparent().remove(link)
                    continue
                bundled = _file(dist, parsed.path.lstrip("/"), 4 * 1024 * 1024).decode("utf-8")
                style = etree.Element("style")
                style.text = _css_urls(bundled, origin + parsed.path)
                link.getparent().replace(link, style)
            for link in list(document.xpath("//link[@rel='modulepreload' or @rel='preload' or @rel='prefetch']")):
                link.getparent().remove(link)
            document.set("data-theme", "light")
            document.set("class", " ".join(value for value in document.get("class", "").split() if value != "dark"))
            style = etree.SubElement(head, "style", {"data-gids-theme-preview": css_entry["sha256"]})
            style.text = css
            etree.SubElement(head, "meta", name="robots", content="noindex,nofollow")
            rendered = html.tostring(document, encoding="utf-8", doctype="<!doctype html>")
            relative = f"theme-previews/{locale}-{family}.html"
            (directory / relative).write_bytes(rendered)
            pages[f"{locale}/{family}"] = {"path": relative, "sha256": hashlib.sha256(rendered).hexdigest(), "bytes": len(rendered)}
    index = {"schema": 1, "theme_sha256": css_entry["sha256"], "public_origin": origin, "pages": pages, "missing": missing}
    _atomic_receipt(index_path, index)
    return index


def read_theme_preview(directory: Path, locale: str, family: str) -> tuple[bytes, dict[str, str]]:
    if locale not in LOCALES or family not in FAMILIES:
        raise ValueError("Unknown theme preview page")
    index = json.loads(_file(directory, "theme-previews.json", 128 * 1024))
    descriptor = index.get("pages", {}).get(f"{locale}/{family}")
    if not descriptor or descriptor.get("path") != f"theme-previews/{locale}-{family}.html":
        raise ValueError("Theme preview page is unavailable")
    body = _file(directory, descriptor["path"])
    if len(body) != descriptor["bytes"] or hashlib.sha256(body).hexdigest() != descriptor["sha256"]:
        raise ValueError("Theme preview page failed integrity verification")
    origin = _origin(index["public_origin"])
    return body, {
        "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; img-src data: {origin}; font-src {origin}; script-src 'none'; connect-src 'none'; form-action 'none'; base-uri {origin}; frame-ancestors 'self'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
