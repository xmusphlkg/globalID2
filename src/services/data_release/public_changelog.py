"""Compare the engineering changelog with the public website changelog.

``CHANGELOG.md`` is the release record. The public site renders
``astro-site/src/data/changelog.ts``, which is a curated multilingual rewrite
and is not generated during site export. A data release must refuse to publish
when the Markdown file contains a version the website file does not list.
"""

from __future__ import annotations

import re
from pathlib import Path

_MARKDOWN_VERSION = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)
_TYPESCRIPT_VERSION = re.compile(r"""version:\s*['"](\d+\.\d+\.\d+)['"]""")


def changelog_markdown_versions(markdown: str) -> list[str]:
    """Return release versions in ``CHANGELOG.md`` order, without duplicates."""

    versions: list[str] = []
    seen: set[str] = set()
    for version in _MARKDOWN_VERSION.findall(markdown):
        if version in seen:
            continue
        seen.add(version)
        versions.append(version)
    return versions


def public_changelog_versions(typescript: str) -> set[str]:
    """Return versions declared by the public ``changelog.ts`` catalogue."""

    return set(_TYPESCRIPT_VERSION.findall(typescript))


def missing_public_changelog_versions(
    *,
    markdown_path: Path,
    typescript_path: Path,
) -> list[str]:
    """Return ``CHANGELOG.md`` versions absent from the public website catalogue."""

    markdown = markdown_path.read_text(encoding="utf-8")
    typescript = typescript_path.read_text(encoding="utf-8")
    published = public_changelog_versions(typescript)
    return [
        version
        for version in changelog_markdown_versions(markdown)
        if version not in published
    ]


def public_changelog_blocker(missing_versions: list[str]) -> str:
    listed = ", ".join(missing_versions)
    return (
        "Public website changelog is behind CHANGELOG.md "
        f"({listed}). Add those releases to astro-site/src/data/changelog.ts "
        "before publishing. Site export does not copy the Markdown changelog."
    )
