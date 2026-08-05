#!/usr/bin/env python3
"""Prevent new production dependencies on the legacy disease fact table.

The migration to source-series facts is intentionally incremental.  Existing
legacy accesses are recorded in a reviewed baseline, but this checker rejects:

* a production Python file that starts accessing the legacy model/table; and
* an increase in either legacy ORM-symbol or SQL/table-token references in a
  file that is already present in the baseline.

Reference counts may decrease without updating the baseline.  That makes the
guard a ratchet: normal migration work can only reduce the legacy surface.
Comments and docstrings are ignored so documentation can describe the
migration without creating a production dependency.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPOSITORY_ROOT / "configs" / "legacy_disease_access_baseline.json"
SCAN_TARGETS = ("src", "dashboard/api", "main.py", "scripts")
SELF_PATH = "scripts/check_legacy_disease_access.py"
BASELINE_VERSION = 1
_LEGACY_SYMBOL = "DiseaseRecord"
_LEGACY_TABLE = "disease_records"
_SYMBOL_PATTERN = re.compile(r"\bDiseaseRecord\b")
_TABLE_PATTERN = re.compile(r"\bdisease_records\b")


class BaselineError(ValueError):
    """Raised when the checked-in baseline is malformed or incomplete."""


@dataclass(frozen=True)
class AccessCounts:
    """Legacy access counts for one repository-relative Python file."""

    disease_record_symbol: int = 0
    disease_records_table: int = 0

    @property
    def total(self) -> int:
        return self.disease_record_symbol + self.disease_records_table

    def to_dict(self) -> dict[str, int]:
        return {
            _LEGACY_SYMBOL: self.disease_record_symbol,
            _LEGACY_TABLE: self.disease_records_table,
        }


@dataclass(frozen=True)
class Violation:
    """One ratchet violation suitable for CLI and test diagnostics."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Return AST string-node identities used as Python docstrings."""

    result: set[int] = set()
    docstring_owners = (
        ast.Module,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
    )
    for node in ast.walk(tree):
        if not isinstance(node, docstring_owners) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            result.add(id(first.value))
    return result


def count_legacy_accesses(source: str, *, filename: str = "<memory>") -> AccessCounts:
    """Count semantic symbol references and non-docstring table tokens."""

    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise BaselineError(
            f"Cannot parse scanned Python file {filename}: {exc}"
        ) from exc

    docstrings = _docstring_node_ids(tree)
    aliases = {_LEGACY_SYMBOL}
    symbol_count = 0
    table_count = 0

    # Track aliases so ``from src.domain import DiseaseRecord as Legacy`` cannot
    # bypass the count merely by changing the local spelling.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for imported in node.names:
            if imported.name.rsplit(".", 1)[-1] == _LEGACY_SYMBOL:
                aliases.add(imported.asname or _LEGACY_SYMBOL)
                symbol_count += 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in aliases:
            symbol_count += 1
        elif isinstance(node, ast.Attribute) and node.attr == _LEGACY_SYMBOL:
            symbol_count += 1
        elif isinstance(node, ast.ClassDef) and node.name == _LEGACY_SYMBOL:
            symbol_count += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            symbol_count += len(_SYMBOL_PATTERN.findall(node.value))
            table_count += len(_TABLE_PATTERN.findall(node.value))

    return AccessCounts(
        disease_record_symbol=symbol_count,
        disease_records_table=table_count,
    )


def _python_files(root: Path, targets: Iterable[str] = SCAN_TARGETS) -> list[Path]:
    files: set[Path] = set()
    for target_name in targets:
        target = root / target_name
        if target.is_file() and target.suffix == ".py":
            files.add(target)
        elif target.is_dir():
            files.update(path for path in target.rglob("*.py") if path.is_file())
    return sorted(files)


def scan_repository(
    root: Path = REPOSITORY_ROOT,
    *,
    targets: Iterable[str] = SCAN_TARGETS,
) -> dict[str, AccessCounts]:
    """Scan production Python targets and return only files with legacy access."""

    root = root.resolve()
    result: dict[str, AccessCounts] = {}
    for path in _python_files(root, targets):
        relative = path.resolve().relative_to(root).as_posix()
        if relative == SELF_PATH or "__pycache__" in path.parts:
            continue
        counts = count_legacy_accesses(
            path.read_text(encoding="utf-8"),
            filename=relative,
        )
        if counts.total:
            result[relative] = counts
    return result


def load_baseline(path: Path = DEFAULT_BASELINE) -> dict[str, Any]:
    """Load and structurally validate the reviewed ratchet baseline."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(
            f"Cannot load legacy access baseline {path}: {exc}"
        ) from exc
    validate_baseline(payload)
    return payload


def validate_baseline(payload: object) -> None:
    """Require every allowance to carry a bounded count and migration reason."""

    if not isinstance(payload, dict):
        raise BaselineError("Baseline root must be a JSON object")
    if payload.get("version") != BASELINE_VERSION:
        raise BaselineError(
            f"Baseline version must be {BASELINE_VERSION}, got {payload.get('version')!r}"
        )

    classifications = payload.get("classifications")
    if not isinstance(classifications, dict) or not classifications:
        raise BaselineError("Baseline classifications must be a non-empty object")
    for name, definition in classifications.items():
        if (
            not isinstance(name, str)
            or not isinstance(definition, dict)
            or not str(definition.get("description") or "").strip()
        ):
            raise BaselineError(
                f"Classification {name!r} must have a non-empty description"
            )

    files = payload.get("files")
    if not isinstance(files, dict):
        raise BaselineError("Baseline files must be a JSON object")
    for path, allowance in files.items():
        if not isinstance(path, str) or not path.endswith(".py"):
            raise BaselineError(f"Invalid baseline Python path: {path!r}")
        if not isinstance(allowance, dict):
            raise BaselineError(f"Allowance for {path} must be an object")
        classification = allowance.get("classification")
        if classification not in classifications:
            raise BaselineError(
                f"Allowance for {path} has unknown classification {classification!r}"
            )
        if not str(allowance.get("reason") or "").strip():
            raise BaselineError(
                f"Allowance for {path} must explain its migration reason"
            )
        maximum = allowance.get("max_references")
        if not isinstance(maximum, dict):
            raise BaselineError(f"Allowance for {path} must define max_references")
        for key in (_LEGACY_SYMBOL, _LEGACY_TABLE):
            value = maximum.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BaselineError(
                    f"Allowance for {path} must give a non-negative integer for {key}"
                )


def check_against_baseline(
    current: Mapping[str, AccessCounts],
    baseline: Mapping[str, Any],
) -> list[Violation]:
    """Return violations for new files or per-token count increases."""

    validate_baseline(baseline)
    allowed_files = baseline["files"]
    violations: list[Violation] = []
    for path, counts in sorted(current.items()):
        allowance = allowed_files.get(path)
        if allowance is None:
            violations.append(
                Violation(
                    path,
                    "new direct legacy disease access is not present in the reviewed baseline "
                    f"(current={counts.to_dict()})",
                )
            )
            continue

        maximum = allowance["max_references"]
        for key, actual in counts.to_dict().items():
            permitted = int(maximum[key])
            if actual > permitted:
                violations.append(
                    Violation(
                        path,
                        f"{key} references increased from allowed {permitted} to {actual}",
                    )
                )
    return violations


def _current_json(current: Mapping[str, AccessCounts]) -> str:
    return json.dumps(
        {path: counts.to_dict() for path, counts in sorted(current.items())},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="Repository root to scan.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Reviewed JSON allowance baseline.",
    )
    parser.add_argument(
        "--show-current",
        action="store_true",
        help="Print current per-file counts before evaluating the baseline.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    try:
        current = scan_repository(args.root)
        baseline = load_baseline(args.baseline)
        violations = check_against_baseline(current, baseline)
    except BaselineError as exc:
        print(
            f"legacy disease access guard configuration error: {exc}", file=sys.stderr
        )
        return 2

    if args.show_current:
        print(_current_json(current))
    if violations:
        print("Legacy disease access ratchet failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "Migrate the access to source-series facts; do not raise the baseline "
            "without an explicit architecture review.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Legacy disease access ratchet passed: {len(current)} grandfathered files, "
        "no new or increased references."
    )
    return 0


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()
