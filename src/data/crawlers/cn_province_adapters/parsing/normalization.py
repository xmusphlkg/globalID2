"""Normalize explicit Chinese provincial report counts without filling missing facts."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
import re
import pandas as pd


def _norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").replace("\u3000", ""))


def _is_cases_header(value: str) -> bool:
    return "发病数" in value or "病例数" in value or value == "合计"


def _is_report_total_label(value: object) -> bool:
    normalized = _norm(value).casefold()
    if not normalized:
        return False
    if normalized == "total" or "合计" in normalized or "总计" in normalized:
        return True
    if "小计" in normalized:
        return True
    return (
        normalized in {"甲类", "乙类", "丙类", "甲乙类", "甲乙丙类", "丙类类计"}
        or "非法定传染病" in normalized
    )


def _has_report_header(values: Sequence[str]) -> bool:
    disease_columns = {
        index
        for index, value in enumerate(values)
        if len(value) <= 24
        and any(key in value for key in ("病种", "病名", "疾病", "传染病"))
    }
    cases_columns = {
        index
        for index, value in enumerate(values)
        if len(value) <= 20 and _is_cases_header(value)
    }
    return any(disease != cases for disease in disease_columns for cases in cases_columns)


def _normalize_count(value: object) -> int | None:
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    normalized = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized):
        return None
    number = float(normalized)
    if not number.is_integer() or number < 0:
        raise ValueError(f"Province count must be a non-negative integer: {value!r}")
    return int(number)


def _normalize_report_table(table: pd.DataFrame) -> list[dict[str, object]]:
    frame = table.copy()
    # Word-exported HTML often leaves the real header inside the first few
    # body rows (for example a title row followed by 病名/发病数/死亡数).
    existing_columns = [
        "".join(_norm(piece) for piece in column)
        if isinstance(column, tuple)
        else _norm(column)
        for column in frame.columns
    ]
    # Excel exports from provincial sites often put the report title in the
    # worksheet's first row (which pandas promotes to column names), followed
    # by the real 病种/发病数 header as the first data row. A long title can
    # itself contain both words and fool _has_report_header(existing_columns),
    # so prefer a concrete header row whenever one is present.
    header_row = None
    header_position = None
    # Trust a compact concrete header. Later repeated headers can be revision
    # artifacts, so only scan the body when pandas did not expose a real header.
    if not _has_report_header(existing_columns):
        for position, values in enumerate(frame.head(10).itertuples(index=False, name=None)):
            normalized_values = [_norm(value) for value in values]
            if _has_report_header(normalized_values):
                header_row = normalized_values
                header_position = position
                break
    if header_row is not None and header_position is not None:
        frame = frame.iloc[header_position + 1 :].copy()
        frame.columns = header_row
    elif not _has_report_header(existing_columns):
        return []
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = ["".join(_norm(piece) for piece in column) for column in frame.columns]
    else:
        frame.columns = [_norm(column) for column in frame.columns]
    # Preserve every cell when Word exports duplicate display headers.
    seen_columns: dict[str, int] = {}
    unique_columns: list[str] = []
    for column in frame.columns:
        seen_columns[column] = seen_columns.get(column, 0) + 1
        count = seen_columns[column]
        unique_columns.append(column if count == 1 else f"{column}__{count}")
    frame.columns = unique_columns
    disease_column = next(
        (column for column in frame.columns if any(key in column for key in ("病种", "病名", "疾病", "传染病"))),
        None,
    )
    cases_column = next(
        (column for column in frame.columns if _is_cases_header(column)),
        None,
    )
    if disease_column is None or cases_column is None:
        return []
    result: list[dict[str, object]] = []
    for raw in frame.to_dict(orient="records"):
        label = _norm(raw.get(disease_column))
        raw_cases = _norm(raw.get(cases_column))
        if (
            any(key in label for key in ("病种", "病名", "疾病", "传染病"))
            and _is_cases_header(raw_cases)
        ):
            break
        cases = _normalize_count(raw.get(cases_column))
        if label and cases is not None:
            result.append({"label": label, "cases": cases, "source_row": raw})
    return result


def _normalize_narrative_report(
    text: str,
    *,
    link,
    source_url: str,
    artifact_sha256: str,
    config_path: str | Path,
    load_config,
    source_row,
    monthly_source_id,
) -> list[dict[str, object]]:
    """Extract only disease counts stated explicitly in report prose.

    This deliberately records a sparse set: named diseases with an explicit
    numeric case count, or named diseases in an explicit no-case statement.
    Rankings, percentages, category totals, and unmentioned diseases are not
    converted to disease observations.
    """

    compact = _norm(text)
    aliases: dict[str, dict] = {}
    for item in load_config(config_path)["diseases"]:
        for label in item["labels"]:
            key = _norm(label).casefold()
            if key:
                aliases[key] = item
    ordered_aliases = sorted(aliases, key=len, reverse=True)

    explicit: dict[str, tuple[str, int, str]] = {}

    def add(label: str, disease: dict, cases: int, evidence: str) -> None:
        valid_from = disease.get("valid_from")
        if valid_from and link.report_date < date.fromisoformat(str(valid_from)):
            return
        code = str(disease["code"])
        previous = explicit.get(code)
        if previous is not None and previous[1] != cases:
            raise ValueError(
                f"Conflicting explicit narrative values for {code}: "
                f"{previous[1]} vs {cases} ({source_url})"
            )
        explicit[code] = (label, cases, evidence)

    # Some bulletins state a monitored category total and explicitly identify
    # its sole disease (e.g. "报告发病914例，均为水痘病例"). Only an exact
    # single registered disease is safe; rankings and multi-disease totals
    # cannot be assigned to an individual disease.
    sole_disease = re.compile(
        r"(?:报告(?:发病)?|发病|报告重点监测的\d+种其他非法定传染病)([\d,]+)例[,，]均为([^,，。；;]{1,40})"
    )
    for match in sole_disease.finditer(compact):
        label = re.sub(r"(?:病例|患者)$", "", match.group(2)).casefold()
        disease = aliases.get(label)
        clause_start = max(compact.rfind("。", 0, match.start()), compact.rfind("；", 0, match.start())) + 1
        if disease is not None and "死亡" not in compact[clause_start:match.start()]:
            add(str(disease["labels"][0]), disease, int(match.group(1).replace(",", "")), match.group(0))

    # Match a named disease immediately followed by a parenthetical case
    # count, as in 流行性感冒（2393例）. This does not parse death counts.
    for count_match in re.finditer(r"[（(]([\d,]+)例[）)]", compact):
        prefix = compact[: count_match.start()].casefold()
        context_start = max(
            prefix.rfind("。"), prefix.rfind("；"), prefix.rfind(";"),
        )
        context = prefix[context_start + 1 :]
        case_count_cues = (
            "病例数居", "发病数居", "报告病例数", "报告发病数",
            "发病病例数", "病例数为", "发病数为",
        )
        if not any(cue in context for cue in case_count_cues):
            continue
        if "死亡" in context and not any(
            cue in context[context.rfind("死亡") + len("死亡") :]
            for cue in case_count_cues
        ):
            continue
        for alias in ordered_aliases:
            if prefix.endswith(alias):
                disease = aliases[alias]
                add(
                    str(disease["labels"][0]),
                    disease,
                    int(count_match.group(1).replace(",", "")),
                    count_match.group(0),
                )
                break

    # Two official phrasings are common: ``病种甲、病种乙无发病报告`` and
    # ``无病种甲、病种乙等 N 种乙类传染病报告``. Use only the disease names
    # in those explicit clauses; never turn the remaining case total into
    # values for unlisted diseases.
    no_case_patterns = (
        re.compile(r"(?P<names>[^，。；;]{1,500})无(?:发病|病例)(?:、死亡)?(?:病例)?报告"),
        re.compile(r"无(?P<names>[^，。；;]{1,500}?)(?:病例|发病)报告"),
        re.compile(r"无(?P<names>[^，。；;]{1,500}?)(?:\d+种)?(?:甲|乙|丙)类传染病报告"),
    )
    for pattern in no_case_patterns:
        for no_case_match in pattern.finditer(compact):
            names = no_case_match.group("names").casefold()
            for alias in ordered_aliases:
                if alias in names:
                    disease = aliases[alias]
                    add(
                        str(disease["labels"][0]),
                        disease,
                        0,
                        no_case_match.group(0),
                    )

    rows: list[dict[str, object]] = []
    for code, (label, cases, evidence) in sorted(explicit.items()):
        disease = next(item for item in load_config(config_path)["diseases"] if item["code"] == code)
        rows.append(
            source_row(
                report_date=link.report_date,
                province_code=link.jurisdiction_code,
                disease=disease,
                raw_label=label,
                cases=cases,
                source_id=monthly_source_id,
                source_url=source_url,
                source_name="Chinese provincial statutory infectious disease monthly report",
                quality_status="raw",
                raw={
                    "explicit_narrative_value": evidence,
                    "artifact_sha256": artifact_sha256,
                    "title": link.title,
                },
            )
        )
    return rows
