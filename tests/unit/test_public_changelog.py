from __future__ import annotations

from pathlib import Path

from src.services.data_release.public_changelog import (
    missing_public_changelog_versions,
    public_changelog_blocker,
)
from src.services.data_release_service import ASTRO_DIR, ROOT_DIR


def test_missing_public_changelog_versions_follow_markdown_order(tmp_path: Path) -> None:
    markdown = tmp_path / "CHANGELOG.md"
    typescript = tmp_path / "changelog.ts"
    markdown.write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## [0.13.5] - 2026-10-22",
                "",
                "### Added",
                "",
                "- Mention version 0.1.0 in prose, not as a heading.",
                "",
                "## [0.13.4] - 2026-10-21",
                "",
                "## [0.13.5] - 2026-10-22",
                "",
                "## [0.12.1] - 2026-10-18",
            ]
        ),
        encoding="utf-8",
    )
    typescript.write_text(
        "export const changelogReleases = [\n"
        "  { version: '0.13.4', date: '2026-10-21' },\n"
        "  { version: \"0.9.0\", date: '2026-08-01' },\n"
        "];\n",
        encoding="utf-8",
    )

    missing = missing_public_changelog_versions(
        markdown_path=markdown,
        typescript_path=typescript,
    )

    assert missing == ["0.13.5", "0.12.1"]
    assert "0.13.5, 0.12.1" in public_changelog_blocker(missing)
    assert "astro-site/src/data/changelog.ts" in public_changelog_blocker(missing)


def test_repository_public_changelog_includes_every_markdown_release() -> None:
    missing = missing_public_changelog_versions(
        markdown_path=ROOT_DIR / "CHANGELOG.md",
        typescript_path=ASTRO_DIR / "src" / "data" / "changelog.ts",
    )

    assert missing == []
