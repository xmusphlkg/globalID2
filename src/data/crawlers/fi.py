"""Finland THL National Infectious Diseases Register monthly crawler.

The THL ``sampo`` cube exposes a JSONP dimension catalogue and semicolon CSV
exports.  This crawler discovers every selector from that catalogue instead of
pinning volatile numeric SIDs, then requests the source's own national/all-age/
all-sex ``Cases`` slice.

Important source semantics
--------------------------
* Reporting groups are source series, not an additive hierarchy.  Rows such as
  ``Tuberculosis, total`` and ``Pulmonary tuberculosis`` are retained
  independently and are never summed here.
* The current calendar month is provisional.  Public/default fetches include
  only closed months; callers may opt in with ``include_provisional=True``.
* Historical monthly data starts in January 1995.  ``backfill_history=True``
  retrieves the complete closed-month history with one all-time cube query.
* Every normalized row carries its exact query URL and response hashes.  With
  ``save_raw=True`` the source bytes and a provenance sidecar are archived.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import requests

from src.core import get_logger

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Finland THL Infectious Diseases Register"
DEFAULT_SCOPE = "thl_ttr"
DEFAULT_CUBE_URL = "https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases"
DEFAULT_DIMENSIONS_URL = f"{DEFAULT_CUBE_URL}.dimensions.json"
DEFAULT_CSV_URL = f"{DEFAULT_CUBE_URL}.csv"
DEFAULT_LICENSE = "CC BY 4.0"
HISTORY_START_YEAR = 1995

_REQUIRED_DIMENSIONS = {
    "nidrreportgroup",
    "yearmonth",
    "wscmunicipality2022",
    "nidragegroup",
    "nidrsex",
    "measure",
}


class FIDimensionError(ValueError):
    """Raised when the live THL cube no longer satisfies the expected contract."""


class FICubeDataError(ValueError):
    """Raised when a THL CSV response violates the monthly source grain."""


@dataclass(frozen=True)
class FIDimensionNode:
    """One discovered THL dimension member."""

    dimension_id: str
    node_id: str
    sid: int
    label: str
    code: str
    stage: str

    @property
    def selector(self) -> str:
        return f"{self.dimension_id}-{self.sid}"


@dataclass(frozen=True)
class FIDimensionCatalog:
    """Validated selectors required for the national monthly Cases slice."""

    reporting_group_root: FIDimensionNode
    reporting_groups: Tuple[FIDimensionNode, ...]
    all_time: FIDimensionNode
    year_nodes: Mapping[int, FIDimensionNode]
    month_nodes: Mapping[Tuple[int, int], FIDimensionNode]
    all_areas: FIDimensionNode
    all_ages: FIDimensionNode
    all_sexes: FIDimensionNode
    cases: FIDimensionNode
    payload_sha256: str

    @property
    def reporting_groups_by_label(self) -> Mapping[str, FIDimensionNode]:
        return {_norm_key(node.label): node for node in self.reporting_groups}

    @property
    def months_by_label(self) -> Mapping[str, Tuple[int, int]]:
        return {_norm_key(node.label): key for key, node in self.month_nodes.items()}


@dataclass
class FIFetchSummary:
    row_count: int
    latest_date: Optional[date]
    reporting_groups_fetched: int
    query_count: int
    source_url: str
    included_provisional: bool
    omitted_provisional_months: int


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _norm_key(value: object) -> str:
    return _norm_text(value).casefold()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_iso(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def recent_closed_months(as_of: Optional[date] = None, count: int = 3) -> List[Tuple[int, int]]:
    """Return the most recent fully closed calendar months."""

    return recent_months(as_of=as_of, count=count, include_current_month=False)


def recent_months(
    as_of: Optional[date] = None,
    count: int = 3,
    *,
    include_current_month: bool = False,
) -> List[Tuple[int, int]]:
    """Return a bounded revision window, optionally starting at the open month."""

    today = as_of or datetime.now(timezone.utc).date()
    year = today.year
    month = today.month if include_current_month else today.month - 1
    result: List[Tuple[int, int]] = []
    for _ in range(max(1, count)):
        if month <= 0:
            month = 12
            year -= 1
        result.append((year, month))
        month -= 1
    return sorted(result)


def _json_array_from_jsonp(payload: str) -> List[Dict[str, Any]]:
    start = payload.find("[")
    end = payload.rfind("]")
    if start < 0 or end < start:
        raise FIDimensionError("THL dimension response did not contain a JSON array")
    try:
        decoded = json.loads(payload[start : end + 1])
    except json.JSONDecodeError as exc:
        raise FIDimensionError(f"Unable to decode THL dimension JSONP: {exc}") from exc
    if not isinstance(decoded, list):
        raise FIDimensionError("THL dimension payload root must be an array")
    return decoded


def _dimension_node(dimension_id: str, raw: Mapping[str, Any]) -> FIDimensionNode:
    try:
        sid = int(raw["sid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FIDimensionError(
            f"THL dimension {dimension_id!r} contains a member without a numeric SID"
        ) from exc
    label = _norm_text(raw.get("label"))
    if not label:
        raise FIDimensionError(
            f"THL dimension {dimension_id!r} contains a member without a label"
        )
    return FIDimensionNode(
        dimension_id=dimension_id,
        node_id=_norm_text(raw.get("id")),
        sid=sid,
        label=label,
        code=_norm_text(raw.get("code")),
        stage=_norm_text(raw.get("stage")),
    )


def _walk_raw_nodes(raw: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield raw
    children = raw.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, Mapping):
                yield from _walk_raw_nodes(child)


def _single_root(dimension: Mapping[str, Any], dimension_id: str) -> Mapping[str, Any]:
    children = dimension.get("children")
    if not isinstance(children, list) or len(children) != 1:
        raise FIDimensionError(
            f"THL dimension {dimension_id!r} must expose exactly one root member"
        )
    root = children[0]
    if not isinstance(root, Mapping):
        raise FIDimensionError(f"THL dimension {dimension_id!r} root is malformed")
    return root


def _unique_by_label(
    nodes: Iterable[FIDimensionNode], *, dimension_id: str
) -> Tuple[FIDimensionNode, ...]:
    output: List[FIDimensionNode] = []
    labels: Dict[str, FIDimensionNode] = {}
    codes: Dict[str, FIDimensionNode] = {}
    for node in nodes:
        label_key = _norm_key(node.label)
        if label_key in labels:
            raise FIDimensionError(
                f"THL dimension {dimension_id!r} has duplicate label {node.label!r}"
            )
        if node.code and node.code in codes:
            raise FIDimensionError(
                f"THL dimension {dimension_id!r} has duplicate code {node.code!r}"
            )
        labels[label_key] = node
        if node.code:
            codes[node.code] = node
        output.append(node)
    return tuple(output)


def parse_dimension_catalog(payload: str) -> FIDimensionCatalog:
    """Parse and validate THL's JSONP cube dimension catalogue."""

    raw_dimensions = _json_array_from_jsonp(payload)
    dimensions = {
        _norm_text(item.get("id")): item
        for item in raw_dimensions
        if isinstance(item, Mapping) and _norm_text(item.get("id"))
    }
    missing = sorted(_REQUIRED_DIMENSIONS.difference(dimensions))
    if missing:
        raise FIDimensionError(
            "THL cube is missing required dimensions: " + ", ".join(missing)
        )

    reporting_raw = _single_root(dimensions["nidrreportgroup"], "nidrreportgroup")
    reporting_root = _dimension_node("nidrreportgroup", reporting_raw)
    reporting_nodes = _unique_by_label(
        (
            _dimension_node("nidrreportgroup", node)
            for node in _walk_raw_nodes(reporting_raw)
            if node is not reporting_raw
            and _norm_text(node.get("stage")).casefold() == "reportgroup"
        ),
        dimension_id="nidrreportgroup",
    )
    if not reporting_nodes:
        raise FIDimensionError("THL cube exposes no reporting groups")

    time_raw = _single_root(dimensions["yearmonth"], "yearmonth")
    all_time = _dimension_node("yearmonth", time_raw)
    year_nodes: Dict[int, FIDimensionNode] = {}
    month_nodes: Dict[Tuple[int, int], FIDimensionNode] = {}
    for raw_node in _walk_raw_nodes(time_raw):
        if raw_node is time_raw:
            continue
        node = _dimension_node("yearmonth", raw_node)
        if node.stage.casefold() == "year":
            try:
                year = int(node.code)
            except ValueError as exc:
                raise FIDimensionError(f"Invalid THL year code {node.code!r}") from exc
            if year in year_nodes:
                raise FIDimensionError(f"Duplicate THL year member {year}")
            year_nodes[year] = node
        elif node.stage.casefold() == "month":
            try:
                parsed = datetime.strptime(node.code, "%Y-%m")
            except ValueError as exc:
                raise FIDimensionError(f"Invalid THL month code {node.code!r}") from exc
            key = (parsed.year, parsed.month)
            if key in month_nodes:
                raise FIDimensionError(f"Duplicate THL month member {node.code!r}")
            month_nodes[key] = node

    if not year_nodes or min(year_nodes) != HISTORY_START_YEAR:
        raise FIDimensionError(
            f"THL monthly history must begin in {HISTORY_START_YEAR}"
        )
    if not month_nodes or min(month_nodes) != (HISTORY_START_YEAR, 1):
        raise FIDimensionError(
            f"THL month members must begin at {HISTORY_START_YEAR}-01"
        )
    for year, node in year_nodes.items():
        months = sorted(month for (node_year, month) in month_nodes if node_year == year)
        if not months or months[0] != 1:
            raise FIDimensionError(f"THL year {node.label!r} has no January member")

    area_raw = _single_root(dimensions["wscmunicipality2022"], "wscmunicipality2022")
    age_raw = _single_root(dimensions["nidragegroup"], "nidragegroup")
    sex_raw = _single_root(dimensions["nidrsex"], "nidrsex")
    all_areas = _dimension_node("wscmunicipality2022", area_raw)
    all_ages = _dimension_node("nidragegroup", age_raw)
    all_sexes = _dimension_node("nidrsex", sex_raw)
    if _norm_key(all_areas.label) != "all areas":
        raise FIDimensionError(f"Unexpected THL national root {all_areas.label!r}")
    if _norm_key(all_ages.label) != "all ages":
        raise FIDimensionError(f"Unexpected THL age root {all_ages.label!r}")
    if _norm_key(all_sexes.label) != "all sexes":
        raise FIDimensionError(f"Unexpected THL sex root {all_sexes.label!r}")

    measure_raw = _single_root(dimensions["measure"], "measure")
    measure_nodes = [
        _dimension_node("measure", node)
        for node in _walk_raw_nodes(measure_raw)
        if node is not measure_raw
    ]
    cases_matches = [
        node
        for node in measure_nodes
        if _norm_key(node.label) == "cases" or node.node_id.casefold().endswith("/cases")
    ]
    if len(cases_matches) != 1:
        raise FIDimensionError("THL cube must expose exactly one Cases measure")

    return FIDimensionCatalog(
        reporting_group_root=reporting_root,
        reporting_groups=reporting_nodes,
        all_time=all_time,
        year_nodes=year_nodes,
        month_nodes=month_nodes,
        all_areas=all_areas,
        all_ages=all_ages,
        all_sexes=all_sexes,
        cases=cases_matches[0],
        payload_sha256=_sha256(payload.encode("utf-8")),
    )


def _parse_cases(value: object, *, row_number: int) -> Optional[int]:
    text = _norm_text(value).replace(",", "")
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError as exc:
        raise FICubeDataError(
            f"THL CSV row {row_number} has a non-numeric Cases value {text!r}"
        ) from exc
    if not numeric.is_integer() or numeric < 0:
        raise FICubeDataError(
            f"THL CSV row {row_number} has an invalid Cases value {text!r}"
        )
    return int(numeric)


def parse_monthly_csv(
    csv_text: str,
    catalog: FIDimensionCatalog,
    *,
    requested_months: Sequence[Tuple[int, int]],
    as_of: date,
    query_url: str,
    retrieved_at: datetime,
    response_sha256: str,
    source_updated_at: str = "",
) -> List[Dict[str, str]]:
    """Normalize one THL cube CSV without aggregating reporting groups."""

    reader = csv.DictReader(io.StringIO(csv_text.replace("\ufeff", "")), delimiter=";")
    required_fields = {"Time", "Reporting group", "val"}
    if not reader.fieldnames or not required_fields.issubset(reader.fieldnames):
        raise FICubeDataError(
            "THL CSV schema changed; expected fields Time, Reporting group, val"
        )

    target = set(requested_months)
    month_by_label = catalog.months_by_label
    reporting_by_label = catalog.reporting_groups_by_label
    current_month = (as_of.year, as_of.month)
    output: List[Dict[str, str]] = []
    seen: Dict[Tuple[int, int, str], int] = {}

    for row_number, raw in enumerate(reader, start=2):
        time_label = _norm_text(raw.get("Time"))
        if not time_label:
            continue
        month_key = month_by_label.get(_norm_key(time_label))
        if month_key is None:
            # A year/root selector also returns annual and all-years totals.
            # Neither is a monthly observation and neither may be differenced
            # or imported.
            if (
                _norm_key(time_label).startswith("year ")
                or _norm_key(time_label) == _norm_key(catalog.all_time.label)
            ):
                continue
            raise FICubeDataError(
                f"THL CSV row {row_number} has unknown time member {time_label!r}"
            )
        if month_key not in target:
            continue

        reporting_label = _norm_text(raw.get("Reporting group"))
        # Selecting the reporting-group root returns its 97 children plus a
        # synthetic ``All reporting groups`` line.  That line has no disease
        # meaning (and normally no value), so it is not an observation.
        if _norm_key(reporting_label) == _norm_key(
            catalog.reporting_group_root.label
        ):
            continue
        reporting_node = reporting_by_label.get(_norm_key(reporting_label))
        if reporting_node is None:
            raise FICubeDataError(
                f"THL CSV row {row_number} has unknown reporting group {reporting_label!r}"
            )
        cases = _parse_cases(raw.get("val"), row_number=row_number)
        if cases is None:
            # Empty is unknown, never a synthetic zero.
            continue

        identity = (month_key[0], month_key[1], reporting_node.code)
        previous = seen.get(identity)
        if previous is not None:
            raise FICubeDataError(
                "THL CSV contains duplicate monthly reporting-group grain for "
                f"{month_key[0]:04d}-{month_key[1]:02d}/{reporting_node.code}: "
                f"{previous} and {cases}"
            )
        seen[identity] = cases

        provisional = month_key == current_month
        output.append(
            {
                "Date": date(month_key[0], month_key[1], 1).isoformat(),
                "RawDiseaseLabel": reporting_node.label,
                "DiseaseCode": reporting_node.code,
                "ReportingGroupSID": str(reporting_node.sid),
                "Year": str(month_key[0]),
                "Month": str(month_key[1]),
                "Cases": str(cases),
                "PeriodType": "monthly",
                "Geography": catalog.all_areas.label,
                "GeographyKey": "country:FI:national",
                "Age": catalog.all_ages.label,
                "Sex": catalog.all_sexes.label,
                "Measure": catalog.cases.label,
                "PopulationScope": "all",
                "DatasetStatus": "provisional" if provisional else "closed_revisable",
                "IsProvisional": "true" if provisional else "false",
                "RevisionSemantics": "authoritative_revision",
                "AuthoritativeRevision": "true",
                # This row is already constrained to the national, all-age,
                # all-sex slice.  The series store represents that base grain
                # with an empty dimensions object (dimension_key="all"); the
                # explicit source selections remain in Geography/Age/Sex and
                # the archived query provenance.
                "Dimensions": "{}",
                "Source": DEFAULT_SOURCE_NAME,
                "SourceURL": DEFAULT_CUBE_URL,
                "QueryURL": query_url,
                "RetrievedAt": _utc_iso(retrieved_at),
                "SourceUpdatedAt": source_updated_at,
                "RawSHA256": response_sha256,
                "DimensionsSHA256": catalog.payload_sha256,
                "License": DEFAULT_LICENSE,
            }
        )

    output.sort(key=lambda row: (row["Date"], row["DiseaseCode"], row["RawDiseaseLabel"]))
    return output


class FinlandTHLCrawler(BaseCrawler):
    """Crawler for THL national monthly infectious-disease case counts."""

    SOURCE_URL = DEFAULT_CUBE_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        cube_url: str = DEFAULT_CUBE_URL,
        dimensions_url: str = DEFAULT_DIMENSIONS_URL,
        csv_url: str = DEFAULT_CSV_URL,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; FI-THL-TTR)",
            timeout=120,
            max_retries=3,
            delay=0.2,
        )
        self.cube_url = cube_url
        self.dimensions_url = dimensions_url
        self.csv_url = csv_url
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir is not None else Path("data/raw/fi")
        # THL rejects requests with requests' default user agent.  BaseCrawler
        # supplies a named UA; these headers also mirror a normal cube export
        # request and make the required origin explicit.
        self.session.headers.update(
            {
                "Accept": "text/csv,application/json,text/javascript,text/plain,*/*;q=0.8",
                "Referer": self.cube_url,
            }
        )
        self._dimension_payload: Optional[bytes] = None
        self._dimension_response_url = dimensions_url

    def discover_dimensions(self) -> FIDimensionCatalog:
        """Fetch live cube metadata and discover all SIDs used by later queries."""

        response = self.get(self.dimensions_url)
        content = response.content
        try:
            payload = content.decode(getattr(response, "encoding", None) or "utf-8-sig")
        except (LookupError, UnicodeDecodeError):
            payload = content.decode("utf-8-sig")
        catalog = parse_dimension_catalog(payload)
        self._dimension_payload = content
        self._dimension_response_url = str(getattr(response, "url", self.dimensions_url))
        self._archive_response(
            stem="dimensions",
            suffix=".jsonp",
            content=content,
            url=self._dimension_response_url,
            retrieved_at=datetime.now(timezone.utc),
            headers=getattr(response, "headers", {}),
            selectors={"kind": "dimension_catalog"},
        )
        logger.info(
            "[FI-THL] Dimensions discovered | reporting_groups={} years={} months={}",
            len(catalog.reporting_groups),
            len(catalog.year_nodes),
            len(catalog.month_nodes),
        )
        return catalog

    @staticmethod
    def _eligible_months(
        catalog: FIDimensionCatalog,
        *,
        as_of: date,
        include_provisional: bool,
    ) -> set[Tuple[int, int]]:
        current = (as_of.year, as_of.month)
        return {
            key
            for key in catalog.month_nodes
            if key >= (HISTORY_START_YEAR, 1)
            and (key <= current if include_provisional else key < current)
        }

    def _resolve_months(
        self,
        catalog: FIDimensionCatalog,
        *,
        months: Optional[Sequence[Tuple[int, int]]],
        backfill_history: bool,
        include_provisional: bool,
        as_of: date,
    ) -> Tuple[List[Tuple[int, int]], int]:
        eligible = self._eligible_months(
            catalog, as_of=as_of, include_provisional=include_provisional
        )
        if backfill_history:
            return sorted(eligible), 0

        requested = (
            set(months)
            if months is not None
            else set(
                recent_months(
                    as_of,
                    3,
                    include_current_month=include_provisional,
                )
            )
        )
        invalid = sorted(key for key in requested if key not in catalog.month_nodes)
        if invalid:
            rendered = ", ".join(f"{year:04d}-{month:02d}" for year, month in invalid)
            raise FIDimensionError(f"Requested THL months are not in the cube: {rendered}")

        omitted = requested.difference(eligible)
        selected = sorted(requested.intersection(eligible))
        if not selected:
            policy = "including" if include_provisional else "excluding"
            raise FIDimensionError(
                f"No eligible THL months remain after {policy} the current provisional month"
            )
        return selected, len(omitted)

    @staticmethod
    def _query_nodes(
        catalog: FIDimensionCatalog,
        requested_months: Sequence[Tuple[int, int]],
        *,
        as_of: date,
        include_provisional: bool,
    ) -> List[FIDimensionNode]:
        # The cube's ``All years`` root yields aggregate/year rows but does not
        # reliably expand monthly descendants.  Query the discovered year
        # selectors even for a complete backfill so monthly grain is explicit.
        years = sorted({year for year, _ in requested_months})
        missing_years = [year for year in years if year not in catalog.year_nodes]
        if missing_years:
            raise FIDimensionError(
                "THL cube has no year selector for: "
                + ", ".join(str(year) for year in missing_years)
            )
        return [catalog.year_nodes[year] for year in years]

    @staticmethod
    def _query_params(
        catalog: FIDimensionCatalog, time_node: FIDimensionNode
    ) -> List[Tuple[str, str]]:
        return [
            ("row", catalog.reporting_group_root.selector),
            ("column", time_node.selector),
            ("filter", catalog.all_areas.selector),
            ("filter", catalog.all_ages.selector),
            ("filter", catalog.all_sexes.selector),
            ("filter", catalog.cases.selector),
        ]

    def _archive_response(
        self,
        *,
        stem: str,
        suffix: str,
        content: bytes,
        url: str,
        retrieved_at: datetime,
        headers: Mapping[str, Any],
        selectors: Mapping[str, Any],
    ) -> None:
        if not self.save_raw:
            return
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = self.raw_dir / f"{stem}{suffix}"
        provenance_path = self.raw_dir / f"{stem}.provenance.json"
        raw_path.write_bytes(content)
        provenance = {
            "source": DEFAULT_SOURCE_NAME,
            "source_url": self.cube_url,
            "request_url": url,
            "retrieved_at": _utc_iso(retrieved_at),
            "sha256": _sha256(content),
            "bytes": len(content),
            "license": DEFAULT_LICENSE,
            "selectors": dict(selectors),
            "response_headers": {
                str(key): str(value)
                for key, value in headers.items()
                if str(key).casefold() in {"etag", "last-modified", "content-type"}
            },
        }
        provenance_path.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_output_csv(output_csv: Path, rows: Sequence[Dict[str, str]]) -> None:
        fieldnames = [
            "Disease",
            "DiseaseCode",
            "ReportingGroupSID",
            "Year",
            "Month",
            "Date",
            "Cases",
            "PeriodType",
            "Geography",
            "GeographyKey",
            "Age",
            "Sex",
            "Measure",
            "PopulationScope",
            "DatasetStatus",
            "IsProvisional",
            "RevisionSemantics",
            "AuthoritativeRevision",
            "Dimensions",
            "Source",
            "SourceURL",
            "QueryURL",
            "RetrievedAt",
            "SourceUpdatedAt",
            "RawSHA256",
            "DimensionsSHA256",
            "License",
        ]
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output_csv.name}.", suffix=".tmp", dir=output_csv.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            "Disease": row["RawDiseaseLabel"],
                            **{key: row.get(key, "") for key in fieldnames if key != "Disease"},
                        }
                    )
            os.replace(temporary_name, output_csv)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[Sequence[Tuple[int, int]]] = None,
        backfill_history: bool = False,
        include_provisional: bool = False,
        as_of: Optional[date] = None,
        retrieved_at: Optional[datetime] = None,
    ) -> FIFetchSummary:
        """Fetch a normalized, national monthly THL CSV snapshot."""

        effective_date = as_of or datetime.now(timezone.utc).date()
        fetched_at = retrieved_at or datetime.now(timezone.utc)
        catalog = self.discover_dimensions()
        requested, omitted = self._resolve_months(
            catalog,
            months=months,
            backfill_history=backfill_history,
            include_provisional=include_provisional,
            as_of=effective_date,
        )
        query_nodes = self._query_nodes(
            catalog,
            requested,
            as_of=effective_date,
            include_provisional=include_provisional,
        )

        rows_by_key: Dict[Tuple[str, str], Dict[str, str]] = {}
        for time_node in query_nodes:
            params = self._query_params(catalog, time_node)
            response = self.get(self.csv_url, params=params)
            content = response.content
            csv_text = content.decode("utf-8-sig")
            query_url = str(getattr(response, "url", self.csv_url))
            response_hash = _sha256(content)
            source_updated_at = _norm_text(
                getattr(response, "headers", {}).get("last-modified")
            )
            stem = (
                "all-years"
                if time_node.sid == catalog.all_time.sid
                else f"year-{time_node.code}"
            )
            self._archive_response(
                stem=stem,
                suffix=".csv",
                content=content,
                url=query_url,
                retrieved_at=fetched_at,
                headers=getattr(response, "headers", {}),
                selectors={
                    "reporting_group": catalog.reporting_group_root.selector,
                    "time": time_node.selector,
                    "area": catalog.all_areas.selector,
                    "age": catalog.all_ages.selector,
                    "sex": catalog.all_sexes.selector,
                    "measure": catalog.cases.selector,
                },
            )
            parsed = parse_monthly_csv(
                csv_text,
                catalog,
                requested_months=requested,
                as_of=effective_date,
                query_url=query_url,
                retrieved_at=fetched_at,
                response_sha256=response_hash,
                source_updated_at=source_updated_at,
            )
            for row in parsed:
                key = (row["Date"], row["DiseaseCode"])
                previous = rows_by_key.get(key)
                if previous is not None and previous != row:
                    raise FICubeDataError(
                        f"Conflicting THL rows returned across cube queries for {key}"
                    )
                rows_by_key[key] = row

        rows = sorted(
            rows_by_key.values(),
            key=lambda row: (row["Date"], row["DiseaseCode"], row["RawDiseaseLabel"]),
        )
        if not rows:
            raise FICubeDataError("THL cube returned no national monthly Cases rows")

        self._write_output_csv(Path(output_csv), rows)
        latest = max(datetime.strptime(row["Date"], "%Y-%m-%d").date() for row in rows)
        groups = {row["DiseaseCode"] for row in rows}
        logger.info(
            "[FI-THL] CSV written | path={} rows={} groups={} queries={} latest={}",
            output_csv,
            len(rows),
            len(groups),
            len(query_nodes),
            latest,
        )
        return FIFetchSummary(
            row_count=len(rows),
            latest_date=latest,
            reporting_groups_fetched=len(groups),
            query_count=len(query_nodes),
            source_url=self.cube_url,
            included_provisional=include_provisional,
            omitted_provisional_months=omitted,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/fi/finland_national_monthly.csv"
        )
        summary = self.crawl_monthly_national(
            output_csv,
            months=kwargs.get("months"),
            backfill_history=bool(kwargs.get("backfill_history", False)),
            include_provisional=bool(kwargs.get("include_provisional", False)),
            as_of=kwargs.get("as_of"),
        )
        return [
            CrawlerResult(
                title="Finland THL monthly national infectious disease data",
                url=self.cube_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": DEFAULT_SCOPE,
                    "country_code": "FI",
                    "row_count": summary.row_count,
                    "latest_date": (
                        summary.latest_date.isoformat() if summary.latest_date else None
                    ),
                    "reporting_groups_fetched": summary.reporting_groups_fetched,
                    "query_count": summary.query_count,
                    "included_provisional": summary.included_provisional,
                    "license": DEFAULT_LICENSE,
                },
            )
        ]

    def parse(self, response: requests.Response) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated into the cube crawl."""

        return []


__all__ = [
    "DEFAULT_CSV_URL",
    "DEFAULT_CUBE_URL",
    "DEFAULT_DIMENSIONS_URL",
    "DEFAULT_LICENSE",
    "DEFAULT_SCOPE",
    "DEFAULT_SOURCE_NAME",
    "FICubeDataError",
    "FIDimensionCatalog",
    "FIDimensionError",
    "FIDimensionNode",
    "FIFetchSummary",
    "FinlandTHLCrawler",
    "HISTORY_START_YEAR",
    "parse_dimension_catalog",
    "parse_monthly_csv",
    "recent_closed_months",
    "recent_months",
]
