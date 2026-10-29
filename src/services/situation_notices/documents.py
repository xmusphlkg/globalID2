"""Full text of official reports, fetched on demand.

A notice carries a ``document_ref`` saying where its body lives.  Each ``kind``
has one handler that returns a :class:`FetchedDocument`: the extracted text
every model can read and, for PDFs, the original bytes so a model that reads
files natively gets the tables and layout intact.

``DocumentUnavailable`` means the source has no body to give (final);
any other exception is transient and the caller retries with backoff.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from src.services.global_sources.catalogue import ROOT
from src.services.global_sources.client import USER_AGENT, fetch

PDF = "application/pdf"
HTML = "text/html"
PLAIN = "text/plain"

MAX_TEXT_CHARS = 400_000
MAX_PDF_BYTES = 40 * 1024 * 1024
DOWNLOAD_TIMEOUT = 90.0
CACHE_DIR = ROOT / "data" / "cache" / "situation"

#: Body sections of a WHO Disease Outbreak News item, in reading order.
WHO_SECTIONS = ("Summary", "Overview", "Epidemiology", "Assessment", "Response", "Advice")
WHO_API = "https://www.who.int/api/hubs/diseaseoutbreaknews"
DSPACE_API = "https://iris.paho.org/server/api"

_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")
_HANDLE = re.compile(r"^\d+(?:\.\d+)*/\d+$")
_BLANK = re.compile(r"[ \t\xa0]+")
_BLOCKS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "div", "tr")


class DocumentUnavailable(Exception):
    """The source offers no retrievable body for this report."""


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    media_type: str
    text: str
    data: bytes | None = None
    page_count: int | None = None


# --- text extraction ---


def html_to_text(markup: str) -> str:
    """Readable text from an HTML fragment; table rows become ``a | b | c`` lines."""

    soup = BeautifulSoup(markup or "", "html.parser")
    for table in soup.find_all("table"):
        rows = [" | ".join(cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])) for tr in table.find_all("tr")]
        table.replace_with(soup.new_string("\n" + "\n".join(row for row in rows if row.strip(" |")) + "\n"))
    for tag in soup.find_all(_BLOCKS):
        tag.append("\n")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines = (_BLANK.sub(" ", line).strip() for line in soup.get_text("").splitlines())
    return "\n".join(line for line in lines if line)


def pdf_to_text(data: bytes) -> tuple[str, int]:
    """Page text via pdfium: orders of magnitude faster than pdfminer on large reports."""

    import pypdfium2

    pdf = pypdfium2.PdfDocument(data)
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                pages.append(page.get_textpage().get_text_range().strip())
            finally:
                page.close()
        return "\n\n".join(page for page in pages if page), len(pages)
    finally:
        pdf.close()


_OCR_LOCK = threading.Lock()
_OCR_ENGINE: Any = None
#: Scanned reports are pictures of pages; beyond this many pages OCR costs too much.
MAX_OCR_PAGES = 40


def pdf_ocr_text(data: bytes) -> tuple[str, int]:
    """Text of a scanned PDF by OCR; one engine is shared and used by one thread at a time."""

    global _OCR_ENGINE
    import pypdfium2

    pdf = pypdfium2.PdfDocument(data)
    try:
        count = len(pdf)
        if count > MAX_OCR_PAGES:
            raise DocumentUnavailable(f"scanned PDF has {count} pages, over the {MAX_OCR_PAGES}-page OCR limit")
        pages = []
        with _OCR_LOCK:
            if _OCR_ENGINE is None:
                from rapidocr import RapidOCR

                _OCR_ENGINE = RapidOCR()
            for index in range(count):
                page = pdf[index]
                try:
                    image = page.render(scale=2).to_numpy()
                finally:
                    page.close()
                result = _OCR_ENGINE(image)
                pages.append("\n".join(result.txts or ()).strip())
        return "\n\n".join(page for page in pages if page), count
    finally:
        pdf.close()


def _pdf_document(data: bytes) -> FetchedDocument:
    if not data.startswith(b"%PDF"):
        raise DocumentUnavailable("download is not a PDF")
    if len(data) > MAX_PDF_BYTES:
        raise DocumentUnavailable(f"PDF is {len(data) // 1_000_000} MB, over the {MAX_PDF_BYTES // 1_000_000} MB limit")
    text, pages = pdf_to_text(data)
    if not text.strip():
        text, pages = pdf_ocr_text(data)
    if not text.strip():
        raise DocumentUnavailable("PDF has no readable text, even by OCR")
    return FetchedDocument(PDF, text, data, pages)


# --- handlers, one per document_ref kind ---


def _https(url: Any) -> str:
    value = str(url or "")
    if not value.startswith("https://"):
        raise DocumentUnavailable("document reference is not an https URL")
    return value


def _who_don(ref: dict[str, Any], client: httpx.Client) -> FetchedDocument:
    slug = str(ref.get("slug") or "")
    if not _SLUG.match(slug):
        raise DocumentUnavailable("invalid WHO item key")
    params = {"$filter": f"UrlName eq '{slug}'", "$select": ",".join(WHO_SECTIONS)}
    rows = fetch(WHO_API, params=params, client=client).json().get("value") or []
    if not rows:
        raise DocumentUnavailable("WHO no longer lists this item")
    sections = [(name, html_to_text(rows[0].get(name) or "")) for name in WHO_SECTIONS]
    text = "\n\n".join(f"## {name}\n{body}" for name, body in sections if body)
    if not text:
        raise DocumentUnavailable("WHO item has no body text")
    return FetchedDocument(HTML, text)


def _wp_rest(ref: dict[str, Any], client: httpx.Client) -> FetchedDocument:
    url = f"{_https(ref.get('endpoint'))}/{int(ref['id'])}"
    payload = fetch(url, params={"_fields": "title,content"}, client=client).json()
    body = html_to_text(((payload.get("content") or {}).get("rendered")) or "")
    if not body:
        raise DocumentUnavailable("post has no body text")
    title = html_to_text(((payload.get("title") or {}).get("rendered")) or "")
    return FetchedDocument(HTML, f"{title}\n\n{body}" if title else body)


def pdf_link(markup: str, base_url: str) -> str | None:
    """First anchor on a landing page that downloads a PDF (a ``.pdf`` link or a repository bitstream)."""

    for anchor in BeautifulSoup(markup, "html.parser").find_all("a", href=True):
        href = str(anchor["href"])
        path = href.lower().split("?")[0]
        if ".pdf" in href.lower() or (path.endswith("/content") and "/bitstreams/" in path):
            return urljoin(base_url, href)
    return None


def _pdf_page(ref: dict[str, Any], client: httpx.Client) -> FetchedDocument:
    page = fetch(_https(ref.get("page_url")), client=client)
    link = pdf_link(page.text, str(page.url))
    if link is None:
        raise DocumentUnavailable("no PDF link on the report page")
    return _pdf_document(fetch(link, client=client, timeout=DOWNLOAD_TIMEOUT).content)


def _embedded(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return list((payload.get("_embedded") or {}).get(key) or [])


def _dspace(ref: dict[str, Any], client: httpx.Client) -> FetchedDocument:
    handle = str(ref.get("handle") or "")
    if not _HANDLE.match(handle):
        raise DocumentUnavailable("invalid repository handle")
    item = fetch(f"{DSPACE_API}/pid/find", params={"id": f"hdl:{handle}"}, client=client).json()
    bundles_url = ((item.get("_links") or {}).get("bundles") or {}).get("href")
    if not bundles_url:
        raise DocumentUnavailable("repository item has no files")
    files: dict[str, list[tuple[str, str]]] = {}
    for bundle in _embedded(fetch(bundles_url, client=client).json(), "bundles"):
        listing = ((bundle.get("_links") or {}).get("bitstreams") or {}).get("href")
        if not listing:
            continue
        for stream in _embedded(fetch(listing, client=client).json(), "bitstreams"):
            content = ((stream.get("_links") or {}).get("content") or {}).get("href")
            if content:
                files.setdefault(str(bundle.get("name")), []).append((str(stream.get("name") or ""), content))

    original = next((url for name, url in files.get("ORIGINAL", []) if name.lower().endswith(".pdf")), None)
    if original:
        return _pdf_document(fetch(original, client=client, timeout=DOWNLOAD_TIMEOUT).content)
    extracted = next((url for name, url in files.get("TEXT", []) if name.lower().endswith(".txt")), None)
    if extracted:
        text = fetch(extracted, client=client, timeout=DOWNLOAD_TIMEOUT).text.strip()
        if text:
            return FetchedDocument(PLAIN, text)
    raise DocumentUnavailable("repository item has no readable file")


_HANDLERS: dict[str, Callable[[dict[str, Any], httpx.Client], FetchedDocument]] = {
    "who_don": _who_don,
    "wp_rest": _wp_rest,
    "pdf_page": _pdf_page,
    "dspace": _dspace,
}


def fetch_document(ref: dict[str, Any] | None) -> FetchedDocument:
    """Fetch one report body; text is capped so one document cannot dominate a prompt."""

    handler = _HANDLERS.get(str((ref or {}).get("kind") or ""))
    if handler is None:
        raise DocumentUnavailable("no document source for this report")
    with httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "*/*"}, follow_redirects=True) as client:
        document = handler(ref or {}, client)
    if len(document.text) > MAX_TEXT_CHARS:
        document = FetchedDocument(
            document.media_type, document.text[:MAX_TEXT_CHARS], document.data, document.page_count
        )
    return document


# --- original-file cache (internal; never published) ---


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def store_original(notice_id: str, data: bytes, *, directory: Path = CACHE_DIR) -> str:
    """Keep the original file so native-file models can read it again; returns its name."""

    name = hashlib.sha256(notice_id.encode("utf-8")).hexdigest()[:20] + ".pdf"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return name


def load_original(name: str | None, *, directory: Path = CACHE_DIR) -> bytes | None:
    if not name or "/" in name or "\\" in name:
        return None
    path = directory / name
    return path.read_bytes() if path.is_file() else None
