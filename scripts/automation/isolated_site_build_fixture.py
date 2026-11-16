"""Copy a clean site fixture without touching a developer's generated outputs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def copy_clean_site(source: Path, destination: Path) -> Path:
    """Keep source code and static assets; seed bounded data separately."""
    source = source.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).resolve().relative_to(source)
        excluded: set[str] = set()
        if relative == Path('.'):
            excluded.update({'node_modules', 'dist', '.astro', '.vite', '.wrangler', 'logs', 'test-results', 'playwright-report'})
        elif relative == Path('src'):
            excluded.add('data')
        elif relative == Path('public'):
            excluded.add('site-data')
        return excluded.intersection(names)

    shutil.copytree(source, destination, ignore=ignore)
    shutil.copytree(source.parent / 'shared', destination.parent / 'shared')
    data = destination / 'src' / 'data'
    data.mkdir(parents=True)
    for filename in ('acknowledgements.ts', 'changelog.ts'):
        shutil.copy2(source / 'src' / 'data' / filename, data / filename)
    (destination / 'node_modules').symlink_to(source / 'node_modules', target_is_directory=True)
    return destination


def fixture_build_environment() -> dict[str, str]:
    """Do not inherit a production publication's external roots or build plans."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(('GIDS_', 'PUBLIC_GIDS_', 'GLOBALID_'))
        and key not in {'SITE_URL', 'SUBSCRIPTIONS__PUBLIC_BASE_URL'}
    }
    env.update({'CI': '1', 'GIDS_ASTRO_RENDER_WORKERS': '0'})
    return env
