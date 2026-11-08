"""Choose a safe number of download-export workers."""
from __future__ import annotations

from pathlib import Path


def _read_cgroup_memory_limit_bytes(
    *,
    cgroup_root: Path | None = None,
    membership_path: Path | None = None,
) -> int | None:
    """Return the tightest cgroup memory guardrail for this process.

    A systemd service usually runs below a child cgroup, while the cgroup v2
    mount root remains unlimited.  Reading only ``/sys/fs/cgroup/memory.*``
    therefore misses the service's actual ``memory.high`` limit and can make
    a memory-heavy export choose too many workers.
    """
    cgroup_root = cgroup_root or Path("/sys/fs/cgroup")
    membership_path = membership_path or Path("/proc/self/cgroup")
    roots = [cgroup_root]
    try:
        for line in membership_path.read_text(encoding="utf-8").splitlines():
            hierarchy, controllers, relative_path = line.split(":", 2)
            if hierarchy != "0" or controllers:
                continue
            normalized = relative_path.strip().lstrip("/")
            if normalized:
                roots.insert(0, cgroup_root / normalized)
            break
    except (OSError, ValueError):
        pass

    candidates = tuple(
        root / setting
        for root in roots
        for setting in ("memory.high", "memory.max")
    )
    values: list[int] = []
    for path in candidates:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw or raw == "max":
            continue
        try:
            values.append(int(raw))
        except ValueError:
            continue
    return min(values) if values else None


def _default_export_workers(cpu_count: int | None, memory_limit_bytes: int | None) -> int:
    """Scale partition workers by CPU and the cgroup memory guardrail.

    Unknown memory stays at 4. A known limit keeps 1 worker through 4 GiB and
    2 through 8 GiB. Above that, add one worker per 2 GiB, capped at 8, so a
    16 GiB worker cgroup uses 6 cores while the parent still holds the curves.
    """
    cpus = max(1, cpu_count or 1)
    if not memory_limit_bytes:
        return min(4, cpus)

    gib = 1024 * 1024 * 1024
    if memory_limit_bytes <= 4 * gib:
        return 1
    if memory_limit_bytes <= 8 * gib:
        return min(2, cpus)
    grown = 2 + (memory_limit_bytes - 8 * gib) // (2 * gib)
    return min(cpus, grown, 8)
