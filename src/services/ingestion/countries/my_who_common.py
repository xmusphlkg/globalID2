"""Lossless WHO OData page capture and complete offline replay."""
import asyncio
import json
from urllib.parse import urljoin, urlsplit
from src.services.ingestion.artifacts import read_artifact


def page_document(payload):
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get('value'), list) or 'error' in data:
        raise ValueError('WHO OData response is not a successful value collection')
    return data


def next_url(current, next_link, root):
    if not next_link:
        return None
    value = urljoin(current, next_link)
    parsed, expected = urlsplit(value), urlsplit(root)
    if parsed.scheme != 'https' or parsed.netloc != expected.netloc or parsed.path != expected.path or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('WHO pagination left the reviewed dataset endpoint')
    return value


async def capture_pages(writer, crawler, *, url, dataset, retrieved_at):
    current, seen, number = url, set(), 0
    while current:
        if current in seen or number >= 1000:
            raise ValueError('WHO pagination cycle or excessive page count')
        seen.add(current)
        response = await asyncio.to_thread(crawler.get, current)
        await writer.write(response.content, source_url=response.url, kind='archive_json', metadata={'endpoint':'who_odata_page','dataset':dataset,'page_number':number,'requested_url':current,'retrieved_at':retrieved_at})
        data = page_document(response.content)
        current = next_url(current, data.get('@odata.nextLink'), url)
        number += 1


def replay_pages(artifacts, *, url, dataset):
    pages = sorted([a for a in artifacts if a.metadata_.get('endpoint')=='who_odata_page' and a.metadata_.get('dataset')==dataset], key=lambda a:a.metadata_['page_number'])
    if not pages:
        raise ValueError(f'Missing captured WHO dataset: {dataset}')
    current, seen, rows, counts = url, set(), [], set()
    for number, artifact in enumerate(pages):
        if current is None or artifact.metadata_['page_number'] != number or artifact.metadata_['requested_url'] != current or current in seen:
            raise ValueError('WHO captured page chain is duplicate, discontinuous, or unexpected')
        seen.add(current)
        data = page_document(read_artifact(artifact))
        if '@odata.count' in data:
            counts.add(int(data['@odata.count']))
        rows.extend((row, artifact, index) for index,row in enumerate(data['value']))
        current = next_url(current, data.get('@odata.nextLink'), url)
    if current is not None or (counts and counts != {len(rows)}):
        raise ValueError('WHO captured page chain is incomplete or count mismatched')
    return rows
