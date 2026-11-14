"""Stable calendar partitions and byte-verified artifact reuse for direct downloads."""
from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path

from .windows import PartitionSpec, partition_rows


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _download_url(base_url: str, relative_path: str) -> str:
    return f"{base_url.rstrip('/')}/{relative_path.lstrip('/')}"


def _row_provenance_counts(rows: list[dict]) -> dict:
    projection_rows = [
        row for row in rows if row.get("record_kind") == "public_projection"
    ]
    source_rows = [
        row
        for row in rows
        if row.get("record_kind") == "source_series_observation"
    ]
    return {
        "projection_record_count": len(projection_rows),
        "source_observation_count": len(source_rows),
        "source_series_count": len(
            {row.get("series_code") for row in source_rows if row.get("series_code")}
        ),
    }


def _sum_measure(rows: list[dict], field: str) -> int | float:
    total = sum(float(row.get(field) or 0) for row in rows)
    return int(total) if total.is_integer() else total


def _partition_metadata(dataset: dict, spec: PartitionSpec, rows: list[dict]) -> dict:
    dates = [str(row["date"]) for row in rows]
    projection_rows = [
        row for row in rows if row.get("record_kind") == "public_projection"
    ]
    summary_rows = projection_rows or rows
    return {
        "schema_version": 4,
        "dataset_kind": dataset["kind"],
        "dataset_id": dataset["dataset_id"],
        "dataset_slug": dataset["slug"],
        "dataset_name": dataset["name"],
        "partition_id": spec.key,
        "window_start": spec.start.isoformat(),
        "window_end": spec.end.isoformat(),
        "data_start": min(dates),
        "data_end": max(dates),
        "record_count": len(rows),
        **_row_provenance_counts(rows),
        "total_cases": _sum_measure(summary_rows, "cases"),
        "total_deaths": _sum_measure(summary_rows, "deaths"),
        "total_basis": (
            "public_projection"
            if projection_rows
            else "source_series_observation"
        ),
    }


def _split_spec(spec: PartitionSpec, rows: list[dict]) -> tuple[tuple[PartitionSpec, list[dict]], tuple[PartitionSpec, list[dict]]]:
    """Bisect an oversized window at a stable calendar boundary."""

    start_ordinal = spec.start.toordinal()
    end_ordinal = spec.end.toordinal()
    if start_ordinal >= end_ordinal:
        midpoint = len(rows) // 2
        if midpoint <= 0 or midpoint >= len(rows):
            raise RuntimeError(
                f"A single-date partition cannot fit below the configured limit: {spec.key}"
            )
        left_rows = rows[:midpoint]
        right_rows = rows[midpoint:]
        left_spec = PartitionSpec(
            key=f"{spec.key}-part-01",
            start=spec.start,
            end=spec.end,
            label=f"{spec.label} · 1",
        )
        right_spec = PartitionSpec(
            key=f"{spec.key}-part-02",
            start=spec.start,
            end=spec.end,
            label=f"{spec.label} · 2",
            is_current=spec.is_current,
        )
        return (left_spec, left_rows), (right_spec, right_rows)

    midpoint_date = date.fromordinal((start_ordinal + end_ordinal) // 2)
    left_rows = [row for row in rows if str(row["date"])[:10] <= midpoint_date.isoformat()]
    right_rows = [row for row in rows if str(row["date"])[:10] > midpoint_date.isoformat()]
    if not left_rows or not right_rows:
        ordered_dates = sorted({str(row["date"])[:10] for row in rows})
        if len(ordered_dates) < 2:
            return _split_spec(
                PartitionSpec(spec.key, spec.start, spec.start, spec.label, spec.is_current),
                rows,
            )
        boundary = ordered_dates[len(ordered_dates) // 2 - 1]
        midpoint_date = date.fromisoformat(boundary)
        left_rows = [row for row in rows if str(row["date"])[:10] <= boundary]
        right_rows = [row for row in rows if str(row["date"])[:10] > boundary]

    right_start = date.fromordinal(midpoint_date.toordinal() + 1)
    left_key = f"{spec.start.isoformat()}_{midpoint_date.isoformat()}"
    right_key = f"{right_start.isoformat()}_{spec.end.isoformat()}"
    left_spec = PartitionSpec(
        key=left_key,
        start=spec.start,
        end=midpoint_date,
        label=f"{spec.start.isoformat()} – {midpoint_date.isoformat()}",
    )
    right_spec = PartitionSpec(
        key=right_key,
        start=right_start,
        end=spec.end,
        label=(
            f"{right_start.isoformat()} – now"
            if spec.is_current
            else f"{right_start.isoformat()} – {spec.end.isoformat()}"
        ),
        is_current=spec.is_current,
    )
    return (left_spec, left_rows), (right_spec, right_rows)


def _planned_partition_rows(
    rows: list[dict], existing_parts: dict[str, dict], *, partitioner=partition_rows
) -> list[tuple[PartitionSpec, list[dict]]]:
    """Keep existing calendar subdivisions instead of rebuilding their parent.

    Reuse only a complete, non-overlapping cover of a stable base window. Old
    single-date row chunks and malformed manifests fall back to normal splitting;
    they cannot safely assign new/deleted observations by calendar bounds.
    """
    planned = []
    for base, base_rows in partitioner(rows):
        children = []
        for part in existing_parts.values():
            try:
                start = date.fromisoformat(part["window"]["start"])
                end = date.fromisoformat(part["window"]["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if (base.start <= start <= end <= base.end
                    and part.get("id") == f"{start.isoformat()}_{end.isoformat()}"):
                children.append((start, end, part))
        children.sort(key=lambda child: (child[0], child[1]))
        cursor = base.start.toordinal()
        for start, end, _ in children:
            if start.toordinal() != cursor:
                break
            cursor = end.toordinal() + 1
        else:
            if len(children) > 1 and cursor == base.end.toordinal() + 1:
                buckets = [[] for _ in children]
                # Binary search avoids scanning all observations for each part.
                from bisect import bisect_left
                ends = [end.isoformat() for _, end, _ in children]
                for row in base_rows:
                    buckets[bisect_left(ends, str(row["date"])[:10])].append(row)
                for (start, end, part), part_rows in zip(children, buckets):
                    if not part_rows:
                        continue
                    current = base.is_current and end == base.end
                    planned.append((PartitionSpec(
                        key=part["id"], start=start, end=end,
                        label=f"{start.isoformat()} – {'now' if current else end.isoformat()}",
                        is_current=current,
                    ), part_rows))
                continue
        planned.append((base, base_rows))
    return planned


class PartitionWriter:
    """Keep a task's serializers and write policy together through recursive splits."""

    def __init__(self, *, json_bytes, csv_bytes, xlsx_bytes, file_sha256,
                 write_if_changed, planned_partition_rows, public_formats,
                 github_max_file_bytes):
        self.json_bytes = json_bytes
        self.csv_bytes = csv_bytes
        self.xlsx_bytes = xlsx_bytes
        self.file_sha256 = file_sha256
        self.write_if_changed = write_if_changed
        self.planned_partition_rows = planned_partition_rows
        self.public_formats = public_formats
        self.github_max_file_bytes = github_max_file_bytes

    def _bounded_partition_artifacts(
        self,
        dataset: dict,
        spec: PartitionSpec,
        rows: list[dict],
        max_file_bytes: int,
        *,
        json_content: bytes | None = None,
    ) -> list[tuple[PartitionSpec, list[dict], dict[str, bytes]]]:
        # JSON is normally the largest representation. Reject an oversized parent
        # before spending time constructing CSV and an XLSX workbook for it.
        artifacts = {"json": json_content if json_content is not None else self.json_bytes(_partition_metadata(dataset, spec, rows), rows)}
        if len(artifacts["json"]) < max_file_bytes:
            artifacts["csv"] = self.csv_bytes(rows)
            if len(artifacts["csv"]) < max_file_bytes:
                artifacts["xlsx"] = self.xlsx_bytes(_partition_metadata(dataset, spec, rows), rows)
                if len(artifacts["xlsx"]) < max_file_bytes:
                    return [(spec, rows, artifacts)]
        left, right = _split_spec(spec, rows)
        return self._bounded_partition_artifacts(dataset, *left, max_file_bytes) + self._bounded_partition_artifacts(
            dataset, *right, max_file_bytes
        )

    def _dataset_parts(
        self,
        *,
        dataset: dict,
        rows: list[dict],
        output_dir: Path,
        download_url_base: str,
        max_file_bytes: int,
        expected_paths: set[Path],
        existing_parts: dict[str, dict],
        trusted_paths: set[str] | None = None,
        counters: dict[str, int] | None = None,
    ) -> tuple[list[dict], int]:
        parts: list[dict] = []
        changed = 0
        for base_spec, base_rows in self.planned_partition_rows(rows, existing_parts):
            base_metadata = _partition_metadata(dataset, base_spec, base_rows)
            base_json = self.json_bytes(base_metadata, base_rows)
            base_content_sha = _sha256(base_json)
            existing = existing_parts.get(base_spec.key)
            existing_files = (existing or {}).get("files") or {}
            can_reuse = bool(
                existing
                and existing.get("content_sha256") == base_content_sha
                and set(existing_files) == set(self.public_formats)
            )
            for format_name in self.public_formats if can_reuse else ():
                file_meta = existing_files.get(format_name) or {}
                relative_path = str(file_meta.get("relative_path") or "")
                path = output_dir / relative_path
                if (
                    not relative_path
                    or relative_path != f"{dataset['directory']}/{dataset['path_id']}/{base_spec.key}.{format_name}"
                    or not path.is_file()
                    or path.stat().st_size != int(file_meta.get("bytes") or -1)
                    or path.stat().st_size >= max_file_bytes
                    or (relative_path not in (trusted_paths or ())
                        and self.file_sha256(path) != str(file_meta.get("sha256") or ""))
                ):
                    can_reuse = False
                    break
            if can_reuse:
                if counters is not None:
                    counters["reused_partitions"] += 1
                dates = [str(row["date"]) for row in base_rows]
                files: dict[str, dict] = {}
                for format_name in self.public_formats:
                    file_meta = existing_files[format_name]
                    expected_paths.add(output_dir / file_meta["relative_path"])
                    # The artifacts are content-addressed and can be reused, but
                    # their public locations are tied to the configured Git branch.
                    # Never carry an old Raw base URL forward into a new manifest.
                    files[format_name] = {
                        **file_meta,
                        "url": _download_url(
                            download_url_base,
                            file_meta["relative_path"],
                        ),
                    }
                parts.append(
                    {
                        "id": base_spec.key,
                        "label": base_spec.label,
                        "is_current": base_spec.is_current,
                        "window": {
                            "start": base_spec.start.isoformat(),
                            "end": base_spec.end.isoformat(),
                        },
                        "date_range": {"start": min(dates), "end": max(dates)},
                        "record_count": len(base_rows),
                        **_row_provenance_counts(base_rows),
                        "content_sha256": base_content_sha,
                        "files": files,
                    }
                )
                continue

            bounded = self._bounded_partition_artifacts(
                dataset,
                base_spec,
                base_rows,
                max_file_bytes,
                json_content=base_json,
            )
            for spec, part_rows, artifacts in bounded:
                if counters is not None:
                    counters["rebuilt_partitions"] += 1
                files: dict[str, dict] = {}
                for format_name in self.public_formats:
                    relative_path = (
                        f"{dataset['directory']}/{dataset['path_id']}/"
                        f"{spec.key}.{format_name}"
                    )
                    path = output_dir / relative_path
                    expected_paths.add(path)
                    content = artifacts[format_name]
                    changed += int(self.write_if_changed(path, content))
                    if len(content) >= self.github_max_file_bytes:
                        raise RuntimeError(
                            f"Generated file exceeds GitHub's limit: {path} ({len(content)} bytes)"
                        )
                    files[format_name] = {
                        "url": _download_url(download_url_base, relative_path),
                        "relative_path": relative_path,
                        "filename": f"globalid-{dataset['path_id']}-{spec.key}.{format_name}",
                        "bytes": len(content),
                        "sha256": _sha256(content),
                    }
                dates = [str(row["date"]) for row in part_rows]
                parts.append(
                    {
                        "id": spec.key,
                        "label": spec.label,
                        "is_current": spec.is_current,
                        "window": {
                            "start": spec.start.isoformat(),
                            "end": spec.end.isoformat(),
                        },
                        "date_range": {"start": min(dates), "end": max(dates)},
                        "record_count": len(part_rows),
                        **_row_provenance_counts(part_rows),
                        "content_sha256": _sha256(artifacts["json"]),
                        "files": files,
                    }
                )
        return parts, changed
