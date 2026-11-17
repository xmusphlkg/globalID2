"""Normalize explicit Chinese provincial report counts without filling missing facts."""
from __future__ import annotations

from collections.abc import Sequence
from calendar import monthrange
from datetime import date
from pathlib import Path
import re
import pandas as pd


def _norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").replace("\u3000", ""))


_NONMONTHLY_HEADER = re.compile(
    r"累计|累积|年初|全年|本年|年度|同期|同比|环比|去年|上年|前年|"
    r"上月|前月|上期|季度|本季|上季|周|平均|预测|率|[%％]|"
    r"\d{1,2}(?:月)?[—\-~～至到]+\d{1,2}月"
)


def _is_cases_header(value: str) -> bool:
    if _NONMONTHLY_HEADER.search(value) or "死亡" in value or "病死" in value:
        return False
    return (
        any(key in value for key in ("发病数", "病例数", "发病例数"))
        or bool(re.fullmatch(r"(?:本月|当月|报告)?发病[（(]例[）)]", value))
        or value == "合计"
    )


def _is_deaths_header(value: str) -> bool:
    if _NONMONTHLY_HEADER.search(value):
        return False
    return (
        any(key in value for key in ("死亡数", "死亡病例数", "死亡例数", "死亡人数"))
        or bool(re.fullmatch(r"(?:本月|当月|报告)?死亡(?:[（(][例人][）)])?", value))
    )


def _select_count_column(columns: Sequence[str], *, deaths: bool = False) -> str | None:
    """Select a monthly count by meaning, never by column position."""
    predicate = _is_deaths_header if deaths else _is_cases_header
    candidates = [column for column in columns if predicate(re.sub(r"__\d+$", "", column))]
    # Shanghai splits case counts by residence and explicitly supplies their
    # total. An otherwise unqualified 合计 could equally describe deaths or a
    # different measure and is not a case-count header on its own.
    if not {"本市户籍", "非本市户籍"}.issubset(columns):
        candidates = [column for column in candidates if re.sub(r"__\d+$", "", column) != "合计"]
    explicit_month = [column for column in candidates if "本月" in column or "当月" in column]
    candidates = explicit_month or candidates
    if len(candidates) > 1:
        measure = "death" if deaths else "case"
        raise ValueError(f"Ambiguous monthly {measure} columns: {candidates!r}")
    return candidates[0] if candidates else None


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


def _has_report_header(values: Sequence[str], *, include_deaths: bool = False) -> bool:
    disease_columns = {
        index
        for index, value in enumerate(values)
        if len(value) <= 24
        and any(key in value for key in ("病种", "病名", "疾病", "传染病"))
    }
    cases_columns = {
        index
        for index, value in enumerate(values)
        if len(value) <= 20 and (_is_cases_header(value) or include_deaths and _is_deaths_header(value))
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


def _normalize_report_table(
    table: pd.DataFrame, *, include_deaths: bool = False,
) -> list[dict[str, object]]:
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
    if not _has_report_header(existing_columns, include_deaths=include_deaths):
        for position, values in enumerate(frame.head(10).itertuples(index=False, name=None)):
            normalized_values = [_norm(value) for value in values]
            if _has_report_header(normalized_values, include_deaths=include_deaths):
                header_row = normalized_values
                header_position = position
                break
    if header_row is not None and header_position is not None:
        frame = frame.iloc[header_position + 1 :].copy()
        frame.columns = header_row
    elif not _has_report_header(existing_columns, include_deaths=include_deaths):
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
    cases_column = _select_count_column(list(frame.columns))
    deaths_column = _select_count_column(list(frame.columns), deaths=True) if include_deaths else None
    if disease_column is None or (cases_column is None and deaths_column is None):
        return []
    result: list[dict[str, object]] = []
    for raw in frame.to_dict(orient="records"):
        label = _norm(raw.get(disease_column))
        raw_cases = _norm(raw.get(cases_column))
        if (
            any(key in label for key in ("病种", "病名", "疾病", "传染病"))
            and (_is_cases_header(raw_cases) or deaths_column is not None and _is_deaths_header(_norm(raw.get(deaths_column))))
        ):
            break
        cases = _normalize_count(raw.get(cases_column))
        deaths = _normalize_count(raw.get(deaths_column)) if deaths_column is not None else None
        if label and (cases is not None or deaths is not None):
            item = {"label": label, "cases": cases, "source_row": raw}
            if cases is None:
                item["case_column_present"] = cases_column is not None
            if deaths is not None:
                item["deaths"] = deaths
            result.append(item)
    return result


_NONMONTHLY_NARRATIVE = re.compile(
    r"累计|累积|年初|截至|截止|全年|本年|今年|年度|去年|上年|前年|"
    r"上月|前月|同期|同比|环比|季度|本季|上季|本周|上周|当周|每周|周报|"
    r"今日|当日|昨日|当天|本日|日新增|"
    r"第?\d+(?:[—\-~～至到]+\d+)?周|过去(?:一|两|三|四|\d+)(?:周|个月|天)|"
    r"(?:近|最近)\d+(?:天|日|周|月)|"
    r"\d{1,2}(?:月)?[—\-~～至到]+\d{1,2}月"
)

_DAY_RANGE = re.compile(
    r"(?:(?P<start_year>20\d{2})年)?(?P<start_month>\d{1,2})月(?P<start_day>\d{1,2})日?"
    r"[至到—–\-~～]+(?:(?P<end_year>20\d{2})年)?(?:(?P<end_month>\d{1,2})月)?(?P<end_day>\d{1,2})日"
)
_DAY_PERIOD = re.compile(r"\d{1,2}[日号]|[一二三四五六七八九十〇零两]+[日号]|20\d{2}[./-]\d{1,2}[./-]\d{1,2}")


def _whole_month_date_ranges(context: str, report_date: date) -> str:
    """Only a range covering every day of the requested month is monthly."""
    def replace(match):
        start_year = int(match["start_year"] or report_date.year)
        start_month = int(match["start_month"])
        end_year = int(match["end_year"] or start_year)
        end_month = int(match["end_month"] or start_month)
        if (start_year == end_year == report_date.year
                and start_month == end_month == report_date.month
                and int(match["start_day"]) == 1
                and int(match["end_day"]) == monthrange(report_date.year, report_date.month)[1]):
            return f"{report_date.year}年{report_date.month}月"
        return match.group(0)
    return _DAY_RANGE.sub(replace, context)


def _monthly_narrative_context(text: str, start: int, end: int, *, report_date: date) -> str | None:
    """Reject comparison/weekly/YTD claims, allowing an explicit monthly reset."""
    sentence_start = max(text.rfind(separator, 0, start) for separator in ("。", "；", ";", "！", "？")) + 1
    context = text[sentence_start:end]
    resets = list(re.finditer(r"本月|当月", context))
    if resets:
        context = context[resets[-1].start():]
    # Gansu uses this exact transition for the same month's non-statutory
    # monitoring category. It is not a previous-year comparison. Keep other
    # 同期 claims blocked unless they explicitly reset to 本月/当月.
    context = re.sub(r"^同期(?=[,，]重点监测(?:的)?其他非法定传染病)", "", context)
    period_context = _whole_month_date_ranges(context, report_date)
    if _NONMONTHLY_NARRATIVE.search(period_context) or _DAY_PERIOD.search(period_context):
        return None
    for match in re.finditer(r"(?:(?P<year>20\d{2})年)?(?P<month>1[0-2]|0?[1-9])月", period_context):
        if int(match["month"]) != report_date.month or (
            match["year"] and int(match["year"]) != report_date.year
        ):
            return None
    return context


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
    # Regex alternation consumes the longest name at a position before looking
    # further ahead: 埃博拉出血热 must not also emit 出血热, nor 甲型肝炎 emit 肝炎.
    disease_names = re.compile("|".join(re.escape(alias) for alias in ordered_aliases))

    explicit: dict[str, tuple[str, int, list[str]]] = {}

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
        if previous is None:
            explicit[code] = (label, cases, [evidence])
        elif evidence not in previous[2]:
            previous[2].append(evidence)

    # Some bulletins state a monitored category total and explicitly identify
    # its sole disease (e.g. "报告发病914例，均为水痘病例"). Only an exact
    # single registered disease is safe; rankings and multi-disease totals
    # cannot be assigned to an individual disease.
    sole_disease = re.compile(
        r"(?:报告(?:发病)?|发病|报告重点监测(?:的)?(?:\d+种)?其他非法定传染病)"
        r"([\d,]+)例[,，]均为([^,，。；;]{1,40})"
    )
    for match in sole_disease.finditer(compact):
        label = re.sub(r"(?:病例|患者)$", "", match.group(2)).casefold()
        disease = aliases.get(label)
        context = _monthly_narrative_context(compact, match.start(), match.end(), report_date=link.report_date)
        if disease is not None and context is not None and "死亡" not in context:
            add(str(disease["labels"][0]), disease, int(match.group(1).replace(",", "")), match.group(0))

    # Match a named disease immediately followed by a parenthetical case
    # count, as in 流行性感冒（2393例）. This does not parse death counts.
    for count_match in re.finditer(r"[（(]([\d,]+)例[）)]", compact):
        prefix = compact[: count_match.start()].casefold()
        context = _monthly_narrative_context(compact, count_match.start(), count_match.end(), report_date=link.report_date)
        if context is None:
            continue
        case_count_cues = (
            "病例数居", "发病数居", "报告病例数", "报告发病数",
            "发病病例数", "病例数为", "发病数为",
        )
        # A generic 病例数 cue inside 死亡病例数 is not a case-count reset.
        last_death = max(context.rfind("死亡"), context.rfind("病死"))
        cues = [match for cue in case_count_cues for match in re.finditer(re.escape(cue), context)]
        if not any(
            cue.start() > last_death
            and (last_death < 0 or not re.fullmatch(r"(?:死亡|病死)(?:病例)?", context[last_death:cue.start()]))
            for cue in cues
        ):
            continue
        for alias in ordered_aliases:
            if prefix.endswith(alias):
                disease = aliases[alias]
                add(
                    str(disease["labels"][0]),
                    disease,
                    int(count_match.group(1).replace(",", "")),
                    context,
                )
                break

    # Two official phrasings are common: ``病种甲、病种乙无发病报告`` and
    # ``无病种甲、病种乙等 N 种乙类传染病报告``. Use only the disease names
    # in those explicit clauses; never turn the remaining case total into
    # values for unlisted diseases.
    no_case_patterns = (
        re.compile(r"(?P<names>[^，。；;]{1,500})无(?:发病|病例)(?:[、，]?死亡)?(?:病例)?报告"),
        re.compile(r"无(?P<names>[^，。；;]{1,500}?)(?:病例|发病)报告"),
        re.compile(r"无(?P<names>[^，。；;]{1,500}?)(?:\d+种)?(?:甲|乙|丙)类传染病报告"),
    )
    for pattern in no_case_patterns:
        for no_case_match in pattern.finditer(compact):
            if _monthly_narrative_context(compact, no_case_match.start(), no_case_match.end(), report_date=link.report_date) is None:
                continue
            names = no_case_match.group("names").casefold()
            # Exceptions/negation and numeric prose are not an explicit list of
            # zero-case diseases (e.g. 除霍乱外其他病种无发病报告).
            if re.search(r"除|以外|其余|剩余|疑似|死亡|\d+例", names):
                continue
            for name_match in disease_names.finditer(names):
                disease = aliases[name_match.group(0)]
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
                    "explicit_narrative_value": evidence[0],
                    "explicit_narrative_evidence": evidence,
                    "artifact_sha256": artifact_sha256,
                    "title": link.title,
                },
            )
        )
    return rows
