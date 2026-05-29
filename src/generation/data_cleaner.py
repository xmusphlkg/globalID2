"""
数据清洗与宽表转换模块

将长表形式的监测数据整理为宽表形式的 Markdown 表格，
根据数据采集频率（月度/周度/日度）进行聚合与展示，
避免日期过长、信息丢失。
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

from src.core import get_logger
from src.core.missing_values import normalize_rate_columns

logger = get_logger(__name__)

Frequency = Literal["monthly", "weekly", "daily"]


def infer_frequency(df: pd.DataFrame, time_col: str = "time") -> Frequency:
    """
    根据时间列推断数据采集频率。
    - 若日期多为月初（1日）且间隔约1月 → monthly
    - 若间隔约7天 → weekly
    - 否则 → daily
    """
    if df.empty or time_col not in df.columns:
        return "daily"

    times = pd.to_datetime(df[time_col]).dropna()
    if len(times) < 2:
        return "daily"

    times = times.sort_values()
    diffs = times.diff().dropna()
    if diffs.empty:
        return "daily"

    # 中位数间隔（天）
    median_days = diffs.dt.total_seconds().median() / 86400

    # 判断是否为月初（1日）为主
    day_of_month = times.dt.day
    pct_first = (day_of_month == 1).mean()

    if median_days >= 25 and pct_first >= 0.5:
        return "monthly"
    if 5 <= median_days <= 10:
        return "weekly"
    return "daily"


def _format_period(ts: pd.Timestamp, freq: Frequency) -> str:
    """将时间戳格式化为简短周期字符串。"""
    if freq == "monthly":
        return ts.strftime("%Y-%m")
    if freq == "weekly":
        # ISO 周：2025-W01
        return ts.strftime("%Y-W%V")
    return ts.strftime("%Y-%m-%d")


def long_to_wide(
    df: pd.DataFrame,
    freq: Frequency,
    time_col: str = "time",
    value_cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    将长表转为宽表：按周期聚合，行=周期，列=指标。
    保留 cases, deaths, new_cases, new_deaths, incidence_rate 等。
    """
    if df.empty or time_col not in df.columns:
        return pd.DataFrame()

    default_cols = [
        "cases",
        "deaths",
        "new_cases",
        "new_deaths",
        "incidence_rate",
        "mortality_rate",
        "population_denominator",
    ]
    cols = value_cols or [c for c in default_cols if c in df.columns]
    if not cols:
        cols = [c for c in df.columns if c not in (time_col, "disease_id") and df[c].dtype in ("int64", "float64")]

    df = normalize_rate_columns(df)
    df[time_col] = pd.to_datetime(df[time_col])

    agg_dict = {}
    for c in cols:
        if c in df.columns:
            agg_dict[c] = "sum" if c in ("cases", "deaths", "new_cases", "new_deaths", "recoveries") else "mean"
    if not agg_dict:
        return pd.DataFrame()

    if freq == "monthly":
        df["_period"] = df[time_col].dt.to_period("M").dt.to_timestamp()
        wide = df.groupby("_period", as_index=True).agg(agg_dict).reset_index()
    elif freq == "weekly":
        # W-MON: 周一起始，符合 ISO 周习惯
        grouper = pd.Grouper(key=time_col, freq="W-MON", label="left", closed="left")
        grouped = df.groupby(grouper)
        wide = grouped.agg(agg_dict).reset_index()
        wide = wide.rename(columns={time_col: "_period"})
    else:
        df["_period"] = df[time_col].dt.normalize()
        wide = df.groupby("_period", as_index=True).agg(agg_dict).reset_index()
    wide["_period_str"] = wide["_period"].apply(lambda t: _format_period(t, freq))
    wide = wide.sort_values("_period")
    return wide


def wide_to_markdown_table(
    wide: pd.DataFrame,
    period_col: str = "_period_str",
    freq: Frequency = "monthly",
) -> str:
    """
    将宽表 DataFrame 转为 Markdown 表格字符串。
    周期列简短显示，数值列保留合理精度。
    """
    if wide.empty:
        return ""

    # 使用 period_col 作为首列，其余为指标
    cols = [c for c in wide.columns if c not in ("_period", "_period_str") and c != period_col]
    if period_col in wide.columns:
        out_cols = [period_col] + cols
    else:
        out_cols = cols

    header = wide[out_cols].copy()
    # 列名可读化
    header.columns = [_col_label(c) for c in out_cols]

    # 数值格式化
    for c in cols:
        if c in header.columns:
            if header[c].dtype in ("int64", "float64"):
                header[c] = header[c].apply(_fmt_num)

    # 周期列简短
    if period_col in header.columns:
        header[period_col] = header[period_col].astype(str)

    lines = []
    lines.append("| " + " | ".join(header.columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(header.columns)) + " |")
    for _, row in header.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")

    return "\n".join(lines)


def _col_label(col: str) -> str:
    """列名转可读标签。"""
    labels = {
        "cases": "Cases",
        "deaths": "Deaths",
        "new_cases": "New Cases",
        "new_deaths": "New Deaths",
        "incidence_rate": "Incidence Rate",
        "mortality_rate": "Mortality Rate",
        "recoveries": "Recoveries",
        "_period_str": "Period",
    }
    return labels.get(col, col.replace("_", " ").title())


def _fmt_num(x: Any) -> str:
    """数值格式化，避免过长小数。"""
    if pd.isna(x):
        return "-"
    if isinstance(x, (int, float)):
        if x == int(x):
            return str(int(x))
        return f"{x:.2f}"
    return str(x)


def clean_and_format_for_ai(
    df: pd.DataFrame,
    time_col: str = "time",
    max_rows: int = 24,
) -> Dict[str, Any]:
    """
    清洗数据并格式化为供 AI 使用的结构。

    Returns:
        {
            "markdown_table": str,      # 宽表形式的 Markdown 表格
            "frequency": str,           # 推断的采集频率
            "period_range": str,        # 周期范围（简短）
            "record_count": int,
            "summary_stats": dict,      # 汇总统计
        }
    """
    if df is None or df.empty:
        return {
            "markdown_table": "",
            "frequency": "daily",
            "period_range": "",
            "record_count": 0,
            "summary_stats": {},
        }

    freq = infer_frequency(df, time_col)
    wide = long_to_wide(df, freq, time_col)

    if wide.empty:
        return {
            "markdown_table": "",
            "frequency": freq,
            "period_range": "",
            "record_count": len(df),
            "summary_stats": {},
        }

    # 限制行数，保留最近 N 个周期
    if len(wide) > max_rows:
        wide = wide.tail(max_rows).reset_index(drop=True)

    md_table = wide_to_markdown_table(wide, "_period_str", freq)

    period_range = ""
    if "_period_str" in wide.columns:
        period_range = f"{wide['_period_str'].iloc[0]} to {wide['_period_str'].iloc[-1]}"

    summary_stats = {}
    for c in ["cases", "deaths"]:
        if c in wide.columns:
            summary_stats[f"total_{c}"] = int(wide[c].sum())
            summary_stats[f"avg_{c}"] = round(float(wide[c].mean()), 1)

    return {
        "markdown_table": md_table,
        "frequency": freq,
        "period_range": period_range,
        "record_count": len(df),
        "summary_stats": summary_stats,
    }
