"""Compare the optional Papers portion of a Pages release identity.

Legacy releases remain comparable to one another. A partial, invalid or
missing binding must never compare equal to a bound release.
"""
from __future__ import annotations

from typing import Any, Mapping

PAPERS_FIELDS = ("papers_origin", "papers_generation_sha256", "papers_manifest_url", "papers_manifest_sha256")


def papers_binding_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_has = any(key in left for key in PAPERS_FIELDS)
    right_has = any(key in right for key in PAPERS_FIELDS)
    if not left_has and not right_has:
        return True
    if not left_has or not right_has:
        return False
    try:
        from .papers_immutable import bind_pages_release

        for identity in (left, right):
            # Binding validation is independent of the actual Pages fields.
            bind_pages_release({"release_id": "check", "build_digest": "0" * 64,
                                "source_commit": "check", "deployment_branch": "check"}, identity)
        return all(left.get(key) == right.get(key) for key in PAPERS_FIELDS)
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return False
