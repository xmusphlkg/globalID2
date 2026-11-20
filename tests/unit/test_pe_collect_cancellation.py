"""Collection cancellation drains executor work and retains resumable source bytes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from src.data.crawlers.pe import PeruCDCCrawler
from src.services.exceptions import TaskCancelledError
from src.services.ingestion import runner
from src.services.ingestion.countries import pe


@pytest.fixture
async def collection_executor():
    # The collector starts eight downloads. CPU-limited CI runners can have
    # fewer default executor threads; the test's readiness wait also uses one.
    loop = asyncio.get_running_loop()
    previous = getattr(loop, "_default_executor", None)
    executor = ThreadPoolExecutor(max_workers=9)
    loop.set_default_executor(executor)
    try:
        yield
    finally:
        if previous is None:
            loop._default_executor = None
        else:
            loop.set_default_executor(previous)
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_dashboard_cancel_during_collect_drains_threads_and_preserves_partials(tmp_path, monkeypatch, collection_executor):
    monkeypatch.setattr(runner, 'ORGANIZE_CANCEL_POLL_SECONDS', 0.01)
    request_cancel = asyncio.Event()
    started, lock = Event(), Lock()
    counts = {'running': 0, 'started': 0, 'finished': 0}
    session = SimpleNamespace(close=lambda: counts.update(closed_while_running=counts['running']))
    class Crawler:
        supports_cooperative_cancellation = True
        def __init__(self): self.session = session
        def discover(self, **kwargs): return [{'url': str(i)} for i in range(16)], b'listing'
        def download(self, target, *, force, cancel_event):
            with lock:
                counts['running'] += 1; counts['started'] += 1
                if counts['started'] == 8: started.set()
            path = tmp_path / (target['url'] + '.pdf.part')
            path.write_bytes(b'partial-source-bytes')
            try:
                assert cancel_event.wait(2), 'collection did not propagate cancellation to worker'
                raise TaskCancelledError('cancelled at source chunk boundary')
            finally:
                with lock:
                    counts['running'] -= 1; counts['finished'] += 1
    class Writer:
        artifacts = []
        async def write(self, payload, **kwargs): self.artifacts.append(SimpleNamespace(byte_size=len(payload)))
    async def cancelled():
        if request_cancel.is_set(): raise TaskCancelledError('Dashboard cancellation requested')
    operation = asyncio.create_task(runner._organize_with_cancellation(
        pe.collect(Writer(), crawler_type=Crawler), cancelled,
    ))
    assert await asyncio.to_thread(started.wait, 1)
    request_cancel.set()
    with pytest.raises(TaskCancelledError, match='Dashboard'):
        await asyncio.wait_for(operation, 1)
    assert counts == {'running': 0, 'started': 8, 'finished': 8, 'closed_while_running': 0}
    assert len(list(tmp_path.glob('*.pdf.part'))) == 8


def test_pdf_download_cancellation_keeps_partial_receipt_and_closes_response(tmp_path, monkeypatch):
    from src.services.ingestion import capture
    monkeypatch.setattr(capture, 'assert_source_access', lambda: None)
    stop = Event(); closed = []
    class Response:
        status_code = 200
        headers = {'Content-Length': '12', 'ETag': 'revision'}
        url = 'https://epipublic.dge.gob.pe/uploads/boletin/example.pdf'
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): closed.append(True)
        def iter_content(self, size):
            yield b'%PDF-first'
            stop.set()
            yield b'next'
    crawler = PeruCDCCrawler(cache_directory=tmp_path)
    monkeypatch.setattr(crawler.session, 'get', lambda *args, **kwargs: Response())
    with pytest.raises(TaskCancelledError):
        crawler.download({'url': Response.url, 'year': 2026}, cancel_event=stop)
    assert closed == [True]
    parts = list(tmp_path.glob('*.pdf.part'))
    assert len(parts) == 1 and parts[0].read_bytes() == b'%PDF-first'
    assert len(list(tmp_path.glob('*.part.json'))) == 1
    assert not list(tmp_path.glob('*.pdf'))
