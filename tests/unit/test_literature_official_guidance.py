from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from src.literature.clients.oai import OfficialGuidanceOaiClient
from src.literature.normalization import normalize_official_guidance


def _oai_page(*identifiers: str) -> bytes:
    records = "".join(
        f"""
        <record><header><identifier>{identifier}</identifier><datestamp>2026-08-16T00:00:00Z</datestamp><setSpec>com_10665_8</setSpec></header>
        <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>WHO dengue vaccination guideline {index}</dc:title>
          <dc:creator>World Health Organization</dc:creator>
          <dc:subject>Dengue</dc:subject><dc:subject>Guidelines as Topic</dc:subject>
          <dc:description>Evidence-based recommendations for dengue vaccination programmes and surveillance.</dc:description>
          <dc:date>2026-08-15</dc:date><dc:type>Technical Documents</dc:type>
          <dc:identifier>https://iris.who.int/handle/10665/{380000 + index}</dc:identifier>
          <dc:identifier>https://doi.org/10.2471/GIDS.TEST{index}</dc:identifier>
          <dc:rights>https://creativecommons.org/licenses/by-nc-sa/3.0/igo</dc:rights>
          <dc:publisher>World Health Organization</dc:publisher>
        </oai_dc:dc></metadata></record>
        """
        for index, identifier in enumerate(identifiers, 1)
    )
    return f"""<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>{records}</ListRecords></OAI-PMH>""".encode()


@pytest.mark.asyncio
async def test_official_guidance_oai_checkpoint_resumes_inside_a_page_without_loss() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=_oai_page("oai:iris:1", "oai:iris:2"))

    client = OfficialGuidanceOaiClient(
        endpoint="https://iris.who.int/server/oai/request",
        contact_email="research@example.org",
        transport=httpx.MockTransport(handler),
    )
    since = datetime(2026, 8, 10, tzinfo=timezone.utc)
    until = datetime(2026, 8, 17, tzinfo=timezone.utc)
    first = await client.fetch_incremental(since=since, until=until, max_records=1)
    second = await client.fetch_incremental(
        since=since,
        until=until,
        max_records=1,
        checkpoint=first.checkpoint,
    )

    assert [row["oai_identifier"] for row in first.records] == ["oai:iris:1"]
    assert [row["oai_identifier"] for row in second.records] == ["oai:iris:2"]
    assert first.checkpoint["truncated"] is True
    assert second.checkpoint["truncated"] is False
    assert len(requests) == 2
    assert "metadataPrefix=oai_dc" in requests[0]


def test_official_guidance_normalization_keeps_licensed_metadata_not_documents() -> None:
    import xml.etree.ElementTree as ET
    from src.literature.clients.oai import _parse_page

    records, _ = _parse_page(_oai_page("oai:iris:1"))
    candidate = normalize_official_guidance(records[0])

    assert candidate is not None
    assert candidate.doi == "10.2471/gids.test1"
    assert candidate.study_type == "Guideline"
    assert candidate.open_access_status == "open"
    assert candidate.open_access_url == "https://iris.who.int/handle/10665/380001"
    assert candidate.abstract_text == "Evidence-based recommendations for dengue vaccination programmes and surveillance."
    assert set(candidate.source_payload) == {"official_guidance"}
    serialized = str(candidate.source_payload).lower()
    assert "pdf" not in serialized
    assert "full text" not in serialized


def test_official_guidance_endpoint_is_pinned_to_reviewed_who_iris_host() -> None:
    with pytest.raises(ValueError, match="reviewed WHO IRIS"):
        OfficialGuidanceOaiClient(
            endpoint="https://example.org/oai/request",
            contact_email="research@example.org",
        )
