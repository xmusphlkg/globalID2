"""Current CCDC pages embed surveillance tables in base64 article metadata."""
import base64
from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.data.crawlers.base import CrawlerResult
from src.data.parsers.html_parser import HTMLTableParser
from src.services.ingestion.countries import cn

URL = "https://weekly.chinacdc.cn/en/article/doi/10.46234/ccdcw2026.212"
TABLE = '''<table><thead><tr><td>Diseases</td><td>Cases</td><td>Deaths</td></tr></thead>
<tbody><tr><td>Plague</td><td>0</td><td>0</td></tr>
<tr><td>Dengue</td><td>12</td><td>0</td></tr></tbody></table>'''


def embedded(payload):
    value = base64.b64encode(json.dumps(payload).encode()).decode()
    return f'<table><tbody></tbody></table><script>var article_meta_data="{value}";</script>'


def test_embedded_cdc_table_parses_offline_without_dropping_first_disease(monkeypatch):
    parser = HTMLTableParser()
    monkeypatch.setattr(parser, "fetch_and_parse", lambda *a, **k: pytest.fail("parse must stay offline"))
    result = parser.parse(embedded({"tableList": [{"contentEn": TABLE}]}), url=URL,
                          date=datetime(2026, 8, 1), language="en")
    assert result.success
    assert result.data["RawDiseaseLabel"].tolist() == ["Plague", "Dengue"]
    assert result.data["Cases"].tolist() == ["0", "12"]


@pytest.mark.parametrize("payload", [[], {}, {"tableList": "not-a-list"},
    {"tableList": ["invalid"]}, {"tableList": [{"contentEn": 42}]},
    {"tableList": [{"contentEn": "<table><tbody><tr><td>junk</td><td>1</td><td>2</td></tr></tbody></table>"}]},
])
def test_malformed_embedded_metadata_fails_closed(payload):
    result = HTMLTableParser().parse(embedded(payload), url=URL)
    assert not result.success
    assert not result.has_data
    assert "CDC" in result.error_message


def test_invalid_base64_and_challenge_are_explicit_errors():
    parser = HTMLTableParser()
    malformed = parser.parse('<script>var article_meta_data="%%%";</script>', url=URL)
    assert not malformed.success
    assert "Invalid CDC embedded" in malformed.error_message
    blocked = parser.parse('<title>Security Verification</title><div id="captcha-container"></div>', url=URL)
    assert not blocked.success
    assert "Upstream blocked" in blocked.error_message


@pytest.mark.parametrize("header_in_body", [True, False])
def test_legacy_table_headers_do_not_drop_data(header_in_body):
    table = TABLE
    if header_in_body:
        table = TABLE.replace("<thead>", "<tbody>").replace("</thead>\n<tbody>", "")
    parsed = HTMLTableParser().parse(table, language="en", date=datetime(2026, 8, 1))
    assert parsed.success
    assert parsed.data["RawDiseaseLabel"].tolist() == ["Plague", "Dengue"]


@pytest.mark.asyncio
async def test_blocked_report_keeps_raw_artifact_and_cannot_complete_empty(monkeypatch):
    report = CrawlerResult(title="Report", url=URL, content='<title>Security Verification</title>',
                           date=datetime(2026, 8, 1), year_month="2026 August")
    crawler = SimpleNamespace(fetch_list=AsyncMock(return_value=[report]),
                              _select_preferred_period_results=lambda rows: rows)
    writer = SimpleNamespace(task_uuid="audit", attempt=1, artifacts=[])
    async def write(payload, **kwargs):
        artifact = SimpleNamespace(payload=payload, **kwargs)
        writer.artifacts.append(artifact)
        return artifact
    writer.write = write
    monkeypatch.setattr(cn.task_manager, "is_cancel_requested", AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="blocked by Security Verification"):
        await cn.collect(writer, crawler=crawler, source="cdc_weekly", force=True, fill_missing=False)
    assert len(writer.artifacts) == 1
    artifact = writer.artifacts[0]
    assert artifact.kind == "blocked_html_report"
    assert artifact.metadata["access_status"] == "blocked"
    assert b"Security Verification" in artifact.payload


@pytest.mark.asyncio
async def test_unavailable_source_index_is_not_a_successful_noop():
    crawler = SimpleNamespace(fetch_list=AsyncMock(return_value=[]))
    writer = SimpleNamespace(task_uuid="audit", attempt=1, artifacts=[])
    with pytest.raises(RuntimeError, match="index returned no reports"):
        await cn.collect(writer, crawler=crawler, source="cdc_weekly", force=False, fill_missing=False)
