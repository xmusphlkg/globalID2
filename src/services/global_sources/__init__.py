"""Normalized acquisition layer for international public-health authorities.

The adapters in this package collect official outbreak and surveillance
communications from supranational authorities (WHO and ECDC), normalize them
onto the GIDS ``disease_id`` / ISO country / publication-date key, and export a
deterministic static payload for the Astro site.

Design rules that keep this layer honest:

* Only metadata plus short, attributed extracts are retained.  Long article
  bodies are never re-published.
* Every record keeps its authoritative source URL so each entry on the site is
  one click away from the original.
* Each source declares its redistribution posture in :data:`SOURCE_REGISTRY`.
  Sources whose redistribution terms are unverified stay metadata-only.
"""

from __future__ import annotations

from .catalogue import SOURCE_REGISTRY

__all__ = ["SOURCE_REGISTRY"]
