"""Compare the engineering changelog with the public website changelog.

``CHANGELOG.md`` is the release record. The public update feed exports
``astro-site/src/data/changelog.ts``, a curated multilingual rewrite, without
rebuilding the website. A data release must refuse to publish
when the Markdown file contains a version the website file does not list.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

_MARKDOWN_VERSION = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)
_TYPESCRIPT_VERSION = re.compile(r"""version:\s*['"](\d+\.\d+\.\d+)['"]""")


def validate_public_changelog(value: object) -> int:
    """Validate the complete multilingual records required by the public reader.

    History publishes independently; its latest version need not match this
    template release. Only a readable, valid history can enable the combined chart.
    """
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or not isinstance(value.get("releases"), list)
            or not value["releases"]):
        raise ValueError("Invalid public website history schema")

    def text_fields(record: dict, fields: tuple[str, ...]) -> bool:
        return all(isinstance(record.get(key), str) and record[key].strip() for key in fields)

    versions: set[str] = set()
    for release in value["releases"]:
        if (not isinstance(release, dict) or not isinstance(release.get("version"), str)
                or not re.fullmatch(r"\d+\.\d+\.\d+", release["version"])
                or release["version"] in versions or not isinstance(release.get("date"), str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release["date"])
                or not text_fields(release, ("titleEn", "titleZh", "titleFr", "summaryEn", "summaryZh", "summaryFr"))
                or not isinstance(release.get("sections"), list) or not release["sections"]):
            raise ValueError("Invalid public website release")
        date.fromisoformat(release["date"])
        for section in release["sections"]:
            if (not isinstance(section, dict) or section.get("kind") not in ("new", "improved", "fixed")
                    or not text_fields(section, ("labelEn", "labelZh", "labelFr"))
                    or not isinstance(section.get("items"), list) or not section["items"]
                    or not all(isinstance(item, dict) and text_fields(item, ("en", "zh", "fr")) for item in section["items"])):
                raise ValueError("Invalid public website release translation")
        versions.add(release["version"])
    return len(versions)


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
