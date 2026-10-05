"""Compatibility facade for provider-specific literature normalization."""

from .normalization_common import (
    compact_text,
    normalize_doi,
    normalize_oa_url,
    normalize_openalex_id,
    sanitize_openalex_metadata,
    sanitize_unpaywall_metadata,
)
from .normalization_guidance import normalize_official_guidance
from .normalization_indexed import (
    apply_europe_pmc,
    apply_openalex,
    apply_pubmed_abstract,
    apply_unpaywall,
    crossref_version_relations,
    normalize_crossref,
    normalize_europe_pmc,
    normalize_pubmed,
)
from .normalization_publishers import (
    normalize_biorxiv,
    normalize_elsevier,
    normalize_publisher_rss,
    normalize_springer_nature,
)

__all__ = [
    "apply_europe_pmc",
    "apply_openalex",
    "apply_pubmed_abstract",
    "apply_unpaywall",
    "compact_text",
    "crossref_version_relations",
    "normalize_biorxiv",
    "normalize_crossref",
    "normalize_doi",
    "normalize_elsevier",
    "normalize_europe_pmc",
    "normalize_oa_url",
    "normalize_official_guidance",
    "normalize_openalex_id",
    "normalize_publisher_rss",
    "normalize_pubmed",
    "normalize_springer_nature",
    "sanitize_openalex_metadata",
    "sanitize_unpaywall_metadata",
]
