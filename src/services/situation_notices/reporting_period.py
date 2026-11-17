"""Conservative count-period metadata from the same evidence as a public alert."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

_ANNUAL = re.compile(r"(?:since|from)\s+(?:the\s+)?(?:beginning|start)\s+of\s+(20\d{2})", re.I)
_JANUARY = re.compile(r"(?:since|from|between)\s+(?:1|01|first)\s+(?:january|jan\.?)\b", re.I)
_INCIDENT = re.compile(
    r"\bnew\s+(?:(?!(?:reported|reports|reporting|has|have|was|were|is|are|recorded|records)\b)[\w-]+\s+){0,3}"
    r"(?:cases?|deaths?|infections?|fatalit(?:y|ies)|patients?)\b|"
    r"\b(?:additional|incident|daily|weekly|monthly|this\s+week|last\s+week|previous\s+update|(?:last|past)\s+\d+\s+days?)\b", re.I,
)
_TIME_EVIDENCE = re.compile(r"\b(?:20\d{2}|since|from|between|during|daily|weekly|monthly|year|last|past|january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)
_ISO_RANGE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\s+(?:to|through|until|and)\s+(20\d{2}-\d{2}-\d{2})\b", re.I)


def _period(text: str, as_of: date | None, published: date) -> dict[str, str] | None:
    annual = _ANNUAL.findall(text)
    january = _JANUARY.search(text)
    years = set(re.findall(r"\b20\d{2}\b", text))
    # Multiple years/periods are not a single interpretable interval. A quote
    # mixing cumulative and newly incident figures needs the full extraction.
    if annual or january:
        if len(years) != 1 or len(annual) > 1 or _INCIDENT.search(text):
            return None
        year = int(next(iter(years)))
        start = date(year, 1, 1)
        end = as_of
        if start > published or (end and (end < start or end.year != year or end > published)):
            return None
        return {"kind": "cumulative", "start": start.isoformat(), **({"end": end.isoformat()} if end else {})}
    interval = _ISO_RANGE.search(text)
    if interval and _INCIDENT.search(text) and len(_ISO_RANGE.findall(text)) == 1:
        try:
            start, end = (date.fromisoformat(value) for value in interval.groups())
        except ValueError:
            return None
        if start <= end <= published and (not as_of or as_of == end):
            return {"kind": "incident", "start": start.isoformat(), "end": end.isoformat()}
    return None


def reporting_period(alert: Any) -> dict[str, str] | None:
    """Prefer the source quote; summary cannot replace conflicting time evidence."""
    quote = getattr(alert, "quote", None) or ""
    as_of = getattr(alert, "as_of", None)
    published = alert.published_date
    period = _period(quote, as_of, published)
    if period or _TIME_EVIDENCE.search(quote) or _INCIDENT.search(quote):
        return period
    return _period(getattr(alert, "summary_en", None) or "", as_of, published)
