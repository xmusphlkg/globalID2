"""The release gate accepts marker maps and still requires quotes on timelines."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "validate_situation_release",
    ROOT / "scripts" / "validate_situation_release.py",
)
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)
validate = _validator.validate

SCHEMA = "gids.situation_alerts.v1"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _tree(root: Path, *, marker: dict, alert: dict) -> None:
    outbreak = {
        "outbreak_id": "OB-1",
        "disease_id": "D050",
        "first_published": "2026-10-23",
        "last_published": "2026-10-23",
        "country_codes": ["KE"],
        "alert_count": 1,
        "notice_count": 1,
        "source_ids": ["who_don"],
        "status": "active",
        "alerts": [alert],
    }
    _write(
        root / "map.json",
        {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-30T00:00:00+00:00",
            "as_of": "2026-10-30",
            "since_date": "2025-10-30",
            "point_count": 1,
            "points": [marker],
            "facets": {},
            "outbreaks": [{key: value for key, value in outbreak.items() if key != "alerts"}],
        },
    )
    _write(root / "outbreaks" / "OB-1.json", {"schema_version": SCHEMA, "as_of": "2026-10-30", **outbreak})
    _write(root / "weekly" / "index.json", {"schema_version": SCHEMA, "periods": []})
    _write(root / "monitoring.json", {"schema_version": SCHEMA, "as_of": "2026-10-30", "products": []})
    _write(
        root / "sources.json",
        {
            "schema_version": SCHEMA,
            "sources": [{"source_id": "who_don", "redistribution": "metadata_and_extract"}],
        },
    )


def _marker() -> dict:
    return {
        "alert_id": "a1",
        "outbreak_id": "OB-1",
        "source_id": "who_don",
        "disease_label": "Ebola",
        "disease_name_en": "Ebola virus disease",
        "country_code": "KE",
        "country_name_en": "Kenya",
        "status": "ongoing",
        "published_date": "2026-10-23",
    }


def _alert() -> dict:
    return {
        **_marker(),
        "url": "https://www.who.int/item/1",
        "quote": "Nine cases were reported in Kenya.",
        "summary_en": "Kenya reported nine cases.",
    }


def test_marker_map_passes_when_the_timeline_keeps_the_quote(tmp_path: Path) -> None:
    data = tmp_path / "data"
    site = tmp_path / "site"
    for prefix in ("", "zh/", "fr/"):
        (site / prefix / "situation").mkdir(parents=True)
        (site / prefix / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    _tree(data, marker=_marker(), alert=_alert())
    result = validate(data, site)
    assert result["passed"], result["checks"]


def test_prose_on_a_map_point_fails_the_release_gate(tmp_path: Path) -> None:
    data = tmp_path / "data"
    site = tmp_path / "site"
    (site / "situation").mkdir(parents=True)
    (site / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "zh" / "situation").mkdir(parents=True)
    (site / "zh" / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "fr" / "situation").mkdir(parents=True)
    (site / "fr" / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    _tree(data, marker={**_marker(), "quote": "Nine cases were reported in Kenya."}, alert=_alert())
    result = validate(data, site)
    assert result["passed"] is False
    failed = {item["id"] for item in result["checks"] if not item["passed"]}
    assert "map_markers" in failed


def _indexed_tree(root: Path, *, index=None, alerts=None) -> tuple[Path, Path]:
    data, site = root / 'data', root / 'site'
    for prefix in ('', 'zh/', 'fr/'):
        path = site / prefix / 'situation' / 'index.html'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<html></html>', encoding='utf-8')
    _tree(data, marker=_marker(), alert=_alert())
    mapping = json.loads((data / 'map.json').read_text())
    mapping['filter_points'] = [_marker()] if index is None else index
    _write(data / 'map.json', mapping)
    if alerts is not None:
        outbreak = json.loads((data / 'outbreaks' / 'OB-1.json').read_text())
        outbreak['alerts'] = alerts
        _write(data / 'outbreaks' / 'OB-1.json', outbreak)
    return data, site


def _failed(result: dict) -> set[str]:
    return {check['id'] for check in result['checks'] if not check['passed']}


def test_complete_filter_index_keeps_historical_sources_and_excludes_routine_alerts(tmp_path: Path) -> None:
    older = {**_marker(), 'alert_id': 'historical-channel', 'source_id': 'ecdc_events', 'published_date': '2026-10-20'}
    routine = {**_alert(), 'alert_id': 'routine', 'status': 'routine'}
    data, site = _indexed_tree(tmp_path, index=[_marker(), older], alerts=[
        _alert(), {**_alert(), **older}, routine,
    ])
    result = validate(data, site)
    assert result['passed'], result['checks']
    assert {'filter_index_shape', 'filter_index_markers', 'filter_index_unique',
            'filter_index_in_timeline', 'filter_index_complete'} <= {check['id'] for check in result['checks']}


def test_missing_historical_report_fails_index_completeness_even_with_valid_latest_marker(tmp_path: Path) -> None:
    older = {**_alert(), 'alert_id': 'historical-channel', 'source_id': 'ecdc_events', 'published_date': '2026-10-20'}
    data, site = _indexed_tree(tmp_path, alerts=[_alert(), older])
    result = validate(data, site)
    assert 'filter_index_complete' in _failed(result)
    assert 'point_in_timeline' not in _failed(result)


def test_index_prose_and_duplicate_ids_fail_the_release_gate(tmp_path: Path) -> None:
    data, site = _indexed_tree(tmp_path, index=[_marker(), {**_marker(), 'summary_en': 'A private duplicate summary.'}])
    failed = _failed(validate(data, site))
    assert {'filter_index_markers', 'filter_index_unique'} <= failed


def test_index_must_link_the_right_outbreak_and_match_source_geography_and_date(tmp_path: Path) -> None:
    data, site = _indexed_tree(tmp_path, index=[{**_marker(), 'outbreak_id': 'OB-WRONG'}])
    failed = _failed(validate(data, site))
    assert {'filter_index_in_timeline', 'filter_index_complete'} <= failed
    mapping = json.loads((data / 'map.json').read_text())
    mapping['filter_points'] = [{**_marker(), 'source_id': 'ecdc_events', 'country_code': 'CD', 'published_date': '2026-10-22'}]
    _write(data / 'map.json', mapping)
    result = validate(data, site)
    assert 'filter_index_in_timeline' in _failed(result)
    detail = next(check['detail'] for check in result['checks'] if check['id'] == 'filter_index_in_timeline')
    assert all(field in detail for field in ('source_id', 'country_code', 'published_date'))


def test_routine_extra_and_malformed_index_fail_without_crashing(tmp_path: Path) -> None:
    routine = {**_marker(), 'alert_id': 'routine', 'status': 'routine'}
    data, site = _indexed_tree(tmp_path, index=[_marker(), routine], alerts=[_alert(), {**_alert(), **routine}])
    assert 'filter_index_complete' in _failed(validate(data, site))
    for malformed in ({'alert_id': 'a1'}, [None, _marker()], [{'source_id': 'who_don'}]):
        mapping = json.loads((data / 'map.json').read_text())
        mapping['filter_points'] = malformed
        _write(data / 'map.json', mapping)
        assert 'filter_index_shape' in _failed(validate(data, site))


def test_runtime_map_index_must_match_the_same_release(tmp_path: Path) -> None:
    data, site = _indexed_tree(tmp_path)
    _write(data / 'map-data.json', {'schema_version': SCHEMA, 'points': [_marker()], 'filter_points': []})
    assert 'filter_index_publication' in _failed(validate(data, site))
    _write(data / 'map-data.json', {'schema_version': SCHEMA, 'points': [_marker()], 'filter_points': [_marker()]})
    result = validate(data, site)
    assert result['passed'], result['checks']



def test_data_only_gate_requires_new_index_but_not_built_html_or_nonempty_map(tmp_path: Path) -> None:
    data, _site = _indexed_tree(tmp_path)
    result = validate(data, tmp_path / 'unbuilt-site', require_pages=False, require_points=False, require_filter_index=True)
    assert result['passed'], result['checks']
    mapping = json.loads((data / 'map.json').read_text())
    del mapping['filter_points']
    _write(data / 'map.json', mapping)
    assert 'filter_index_present' in _failed(validate(data, require_pages=False, require_filter_index=True))
    # A valid release containing monitoring only is still a complete generation.
    mapping.update(points=[], filter_points=[], outbreaks=[])
    _write(data / 'map.json', mapping)
    (data / 'outbreaks' / 'OB-1.json').unlink()
    result = validate(data, require_pages=False, require_points=False, require_filter_index=True)
    assert result['passed'], result['checks']


async def test_export_cli_semantic_gate_rejects_bad_index_before_returning_candidate(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from scripts import export_situation_data as cli
    data, _site = _indexed_tree(tmp_path)
    calls = []
    async def export(state_dir, public_dir, *, release_id):
        calls.append((state_dir, public_dir, release_id))
        return {'release_id': release_id, 'point_count': 1}
    monkeypatch.setattr(cli, 'export_json_publication', export)
    result = await cli.export_and_validate(tmp_path / 'state', data, release_id='review-valid')
    assert result['release_id'] == 'review-valid'
    assert result['validation_checks'] >= 10
    assert calls == [(tmp_path / 'state', data, 'review-valid')]
    mapping = json.loads((data / 'map.json').read_text())
    mapping['filter_points'][0]['source_id'] = 'wrong-source'
    _write(data / 'map.json', mapping)
    with pytest.raises(ValueError, match='filter_index_in_timeline'):
        await cli.export_and_validate(tmp_path / 'state', data, release_id='review-invalid')
