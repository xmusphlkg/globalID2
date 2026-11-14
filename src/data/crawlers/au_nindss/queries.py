"""AU query construction and state-count DM0 decoding."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _parse_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        text = str(value).strip().strip("'\"").replace(",", "")
        if text.endswith(("L", "l")):
            text = text[:-1]
        if not text:
            return None
        if "." in text:
            return int(float(text))
        return int(text)
    except (ValueError, TypeError):
        return None


def _find_dm0(obj: Any) -> Optional[List[Dict]]:
    """Recursively find the first DM0 array in a DSR response structure."""
    if isinstance(obj, dict):
        for v in obj.values():
            res = _find_dm0(v)
            if res:
                return res
    elif isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            dm0_like_keys = {"G0", "G", "G1", "G2", "C", "M0", "M", "V", "X", "R"}
            if any(any(k in dm0_like_keys for k in item.keys()) for item in obj):
                return obj
        for item in obj:
            res = _find_dm0(item)
            if res:
                return res
    return None


def _parse_dm0_to_state_counts(dm0: List[Dict], raw: Dict, year: Any) -> Dict[str, int]:
    """
    Parse DM0 list into {state: count} mapping.
    Ported from ID_AU/ScriptGetData/GetDataFunctions.py::parse_dm0_to_state_counts.
    """
    results: Dict[str, Any] = {}

    # Strategy 1: use SH/DM1 metadata to map column indices to state names
    try:
        ds = raw["results"][0]["result"]["data"]["dsr"]["DS"][0]
        sh = ds.get("SH", [])[0]
        dm1 = sh.get("DM1") if sh else None
        if dm1 and isinstance(dm1, list) and dm1[0].get("G1"):
            states = [entry.get("G1") for entry in dm1]
            for block in dm0:
                year_key = block.get("G0") or block.get("G")
                if str(year_key) == str(year):
                    for i, xi in enumerate(block.get("X", [])):
                        st = states[i] if i < len(states) else f"idx_{i}"
                        val = None
                        if isinstance(xi, dict):
                            for kk in ("M0", "M", "V"):
                                if kk in xi:
                                    val = xi[kk]
                                    break
                            if val is None:
                                for vv in xi.values():
                                    if isinstance(vv, (int, float)):
                                        val = vv
                                        break
                        else:
                            val = xi
                        results[st] = _parse_int(val) if val is not None else 0
                        if results[st] is None:
                            results[st] = 0
                    return results
    except Exception:
        pass

    # Strategy 2: generic fallback
    for item in dm0:
        if not isinstance(item, dict):
            continue
        state = None
        value = None

        for gkey in ("G0", "G", "G1", "G2"):
            if gkey in item and item[gkey] is not None:
                state = item[gkey]
                break

        if "C" in item and isinstance(item["C"], (list, tuple)) and item["C"]:
            c = item["C"]
            state = state or (c[0] if len(c) >= 1 else state)
            if len(c) >= 2:
                value = c[1]

        if value is None:
            for k, v in item.items():
                if k == "C" and isinstance(v, (list, tuple)):
                    continue
                if k.startswith("M") or (k.startswith("C") and not isinstance(v, (list, tuple))):
                    parsed = _parse_int(v)
                    if parsed is not None:
                        value = parsed
                    else:
                        try:
                            value = float(str(v).strip().strip("'\"").replace(",", ""))
                        except Exception:
                            value = v
                    break

        if value is None and "V" in item:
            value = item["V"]
        if value is None and "R" in item:
            value = 0

        if state is None:
            for v in item.values():
                if isinstance(v, str):
                    state = v
                    break
                if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
                    state = v[0]
                    break
        if state is None:
            state = str(item)

        if isinstance(state, (list, tuple)) and state:
            state = state[0]
        if isinstance(state, dict):
            for candidate in ("C", "Label", "G", "G1", "G0"):
                if candidate in state:
                    s = state[candidate]
                    if isinstance(s, (list, tuple)) and s:
                        state = s[0]
                        break
                    if isinstance(s, str):
                        state = s
                        break
            else:
                state = str(state)

        if isinstance(value, (list, tuple)):
            value = value[1] if len(value) >= 2 else (value[0] if value else None)
        if isinstance(value, str):
            parsed = _parse_int(value)
            if parsed is not None:
                value = parsed
            else:
                try:
                    value = float(value.strip().strip("'\"").replace(",", ""))
                except Exception:
                    pass
        if value is None:
            value = 0

        k_str = str(state)
        v_norm = int(value) if isinstance(value, float) and float(value).is_integer() else value
        results[k_str] = v_norm

    return results


def _month_name(m: int) -> str:
    return datetime(2000, m, 1).strftime("%B")


def _quarter_for_month(m: int) -> str:
    # NOTE: The PowerBI dashboard requires TWO spaces after "Quarter".
    return f"Quarter  {(m - 1) // 3 + 1}"


def _build_location_payload(
    year: str, quarter: str, month_name: str, disease: str, *, runtime_hints: dict, config: dict
) -> Dict[str, Any]:
    location_hints = (
        runtime_hints.get("location", {})
        if isinstance(runtime_hints.get("location"), dict)
        else {}
    )
    fact_alias = _norm_text(location_hints.get("fact_alias")) or "d1"
    location_alias = _norm_text(location_hints.get("location_alias")) or "d"
    disease_alias = _norm_text(location_hints.get("disease_alias")) or "d11"
    case_alias = _norm_text(location_hints.get("case_alias")) or "d3"
    state_property = _norm_text(location_hints.get("state_property")) or "STATE"
    measure_property = _norm_text(location_hints.get("measure_property")) or "Count_Notification"
    return {
        "version": "1.0.0",
        "queries": [{
            "Query": {
                "Commands": [{
                    "SemanticQueryDataShapeCommand": {
                        "Query": {
                            "Version": 2,
                            "From": [
                                {"Name": fact_alias, "Entity": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT", "Type": 0},
                                {"Name": location_alias,  "Entity": "DELTALOAD_DATAMART LOCATION_DIM", "Type": 0},
                                {"Name": disease_alias, "Entity": "DELTALOAD_DATAMART DISEASE_DIM", "Type": 0},
                                {"Name": case_alias, "Entity": "DELTALOAD_DATAMART CASE_DIM", "Type": 0},
                            ],
                            "Select": [
                                {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Source": location_alias}},
                                        "Property": state_property,
                                    },
                                    "Name": "DELTALOAD_DATAMART LOCATION_DIM.STATE",
                                },
                                {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Source": fact_alias}},
                                        "Property": measure_property,
                                    },
                                    "Name": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT.M_Notification",
                                },
                            ],
                            "Where": [
                                {"Condition": {"Not": {"Expression": {"In": {
                                    "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": location_alias}}, "Property": state_property}}],
                                    "Values": [
                                        [{"Literal": {"Value": "'AUS'"}}],
                                        [{"Literal": {"Value": "'Unknown'"}}],
                                    ],
                                }}}}},
                                {"Condition": {"In": {
                                    "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": disease_alias}}, "Property": "DISEASE NAME"}}],
                                    "Values": [[{"Literal": {"Value": f"'{disease}'"}}]],
                                }}},
                                {"Condition": {"In": {
                                    "Expressions": [
                                        {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DIAGNOSIS_YEAR_HIERARCHY"}},
                                        {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DIAGNOSIS_QUARTER"}},
                                        {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DIAGNOSIS_MONTHNAME"}},
                                    ],
                                    "Values": [[
                                        {"Literal": {"Value": f"'{year}'"}},
                                        {"Literal": {"Value": f"'{quarter}'"}},
                                        {"Literal": {"Value": f"'{month_name}'"}},
                                    ]],
                                }}},
                                {"Condition": {"Comparison": {
                                    "ComparisonKind": 1,
                                    "Left": {"Column": {"Expression": {"SourceRef": {"Source": fact_alias}}, "Property": "DAX_Year"}},
                                    "Right": {"Literal": {"Value": "1990L"}},
                                }}},
                                {"Condition": {"In": {
                                    "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": case_alias}}, "Property": "CONFIRMATION_STATUS"}}],
                                    "Values": [
                                        [{"Literal": {"Value": "'Confirmed'"}}],
                                        [{"Literal": {"Value": "'Probable'"}}],
                                    ],
                                }}},
                            ],
                        },
                        "Binding": {
                            "Primary": {"Groupings": [{"Projections": [0, 1]}]},
                            "DataReduction": {"DataVolume": 4, "Primary": {"Window": {"Count": 200}}},
                        },
                    }
                }]
            },
            "ApplicationContext": {
                "DatasetId": config["datasetId"],
                "Sources": [{"ReportId": config["reportId"]}],
            },
        }],
        "modelId": config["modelId"],
        "cancelRequests": True,
    }
