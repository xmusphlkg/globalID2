"""HTML and search normalization without network, throttling, or health state."""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse
from bs4 import BeautifulSoup


def extract_html_content(html: str, *, resolved_url: str | None = None, clip: Callable) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else None

    canonical = None
    canonical_link = soup.find("link", attrs={"rel": "canonical"})
    if canonical_link and canonical_link.get("href"):
        canonical = str(canonical_link["href"]).strip()
    if not canonical:
        og = soup.find("meta", attrs={"property": "og:url"})
        if og and og.get("content"):
            canonical = str(og["content"]).strip()

    content_language = None
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        content_language = str(html_tag.get("lang")).strip()

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.select_one(".content")
        or soup.select_one(".article")
        or soup.find("section")
        or soup.body
        or soup
    )

    ordered_blocks = container.find_all(["h1", "h2", "h3", "p"], recursive=True) if container else []
    paragraphs = []
    content_sections: list[dict[str, str]] = []
    active_section: dict[str, Any] | None = None
    for element in ordered_blocks:
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name in {"h1", "h2", "h3"}:
            if active_section and active_section.get("paragraphs"):
                section_text = " ".join(active_section["paragraphs"])
                content_sections.append(
                    {
                        "heading": active_section.get("heading"),
                        "text": clip(section_text) or section_text,
                    }
                )
            active_section = {"heading": text, "paragraphs": []}
            continue
        paragraphs.append(text)
        if active_section is None:
            active_section = {"heading": title, "paragraphs": []}
        active_section.setdefault("paragraphs", []).append(text)

    if active_section and active_section.get("paragraphs"):
        section_text = " ".join(active_section["paragraphs"])
        content_sections.append(
            {
                "heading": active_section.get("heading"),
                "text": clip(section_text) or section_text,
            }
        )

    excerpt = " ".join(paragraphs[:4]) if paragraphs else None
    content_text = " ".join(paragraphs[:8]) if paragraphs else None
    if content_text and len(content_text) > 4000:
        content_text = content_text[:4000].rstrip() + "..."

    meta = soup.find("meta", attrs={"name": "description"})
    if not excerpt and meta and meta.get("content"):
        excerpt = str(meta["content"]).strip()
    if not content_text and excerpt:
        content_text = excerpt

    headings = []
    for selector in ("article h2", "article h3", "main h2", "main h3", "h2", "h3"):
        for heading in soup.select(selector):
            text = heading.get_text(" ", strip=True)
            if text:
                headings.append(text)
            if len(headings) >= 6:
                break
        if len(headings) >= 6:
            break

    return {
        "title": title,
        "excerpt": excerpt,
        "content_text": content_text,
        "sections": content_sections or [{"heading": heading} for heading in headings],
        "resolved_url": resolved_url,
        "canonical_url": canonical,
        "content_language": content_language,
    }



def normalize_search_result_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    if url.startswith("/"):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        uddg = query.get("uddg")
        if uddg:
            return unquote(uddg[0])
        return ""
    parsed = urlparse(url)
    if parsed.netloc.lower().endswith("duckduckgo.com"):
        query = parse_qs(parsed.query)
        uddg = query.get("uddg")
        if uddg:
            return unquote(uddg[0])
    return url



def phrase_variants(phrase: str, *, unique_strings: Callable) -> list[str]:
    text = " ".join(str(phrase or "").split()).strip()
    if not text:
        return []
    variants = [text]
    lower = text.lower()
    without_parenthetical = re.sub(r"\s*\([^)]{2,80}\)\s*", " ", text).strip()
    if without_parenthetical and without_parenthetical != text:
        variants.append(without_parenthetical)
    for suffix in (" infection", " disease", " syndrome", " virus"):
        if lower.endswith(suffix) and len(text) > len(suffix) + 2:
            variants.append(text[: -len(suffix)].strip())
    if lower.endswith(" fever"):
        variants.append(f"{text}s")
    if lower.endswith(" fevers"):
        variants.append(text[:-1])
    if "hemorrhagic" in lower:
        variants.append(re.sub("hemorrhagic", "haemorrhagic", text, flags=re.I))
    if "haemorrhagic" in lower:
        variants.append(re.sub("haemorrhagic", "hemorrhagic", text, flags=re.I))

    exposed_match = re.fullmatch(
        r"(?P<subject>child(?:ren)?|infant(?:s)?|newborn(?:s)?)\s+exposed\s+to\s+(?P<agent>.+)",
        text,
        flags=re.I,
    )
    if exposed_match:
        subject = exposed_match.group("subject")
        agent = exposed_match.group("agent").strip()
        variants.extend(
            [
                f"{agent}-exposed {subject}",
                f"{agent} exposed {subject}",
            ]
        )

    if re.search(r"human\s+t-(?:cell\s+)?lymphotropic\s+virus\s+1\s+or\s+2", text, re.I):
        variants.extend(
            [
                "HTLV-1",
                "HTLV-2",
                "human T-cell lymphotropic virus",
                "human T-lymphotropic virus",
            ]
        )

    if "penicillin" in lower and "pneumococcal" in lower:
        variants.extend(
            [
                "penicillin-resistant pneumococcus",
                "penicillin-resistant pneumococci",
                "penicillin-nonsusceptible Streptococcus pneumoniae",
                "penicillin-resistant Streptococcus pneumoniae",
            ]
        )

    # Numbered enteroviruses are indexed inconsistently across surveillance
    # catalogues and literature (for example a full name, a compact EV form,
    # or a hyphenated EV form). Genus letters are deliberately not guessed;
    # canonical labels discovered from entity sources are harvested later.
    enterovirus_match = re.search(
        r"\benterovirus\s+(?:type\s+)?(?:[a-z]\s*)?(\d{1,3})\b",
        text,
        flags=re.I,
    )
    if enterovirus_match:
        number = enterovirus_match.group(1)
        variants.extend(
            [
                f"Enterovirus {number}",
                f"EV{number}",
                f"EV-{number}",
            ]
        )
    return unique_strings(variants)



def relevance_score(query_terms: list[str], url: str, title: Any, excerpt: Any, *, slugify: Callable, unique_strings: Callable) -> float:
    haystack = slugify(" ".join([url or "", str(title or ""), str(excerpt or "")]))
    if not haystack:
        return 0.0
    best_score = 0.0
    for term in query_terms:
        slug = slugify(term)
        tokens = unique_strings(
            token for token in slug.split("-") if len(token) >= 3
        )
        if not tokens:
            continue
        matched = sum(1 for token in tokens if token in haystack)
        token_score = matched / len(tokens)
        phrase_bonus = 0.45 if slug and slug in haystack else 0.0
        best_score = max(best_score, phrase_bonus + token_score * 0.65)
    return min(1.0, best_score)
