import asyncio

from src.services import model_dispatch_worker


async def test_standalone_dispatch_does_not_wait_on_stale_worker_marker(monkeypatch):
    calls = []

    class Dispatcher:
        async def run(self, stop):
            calls.append("run")
            stop.set()

    async def dispose():
        calls.append("dispose")

    def unexpected_readiness():
        raise AssertionError("Standalone dispatcher must not depend on another worker")

    monkeypatch.setattr(model_dispatch_worker, "AIWorkDispatcher", Dispatcher)
    monkeypatch.setattr(model_dispatch_worker, "dispose_database", dispose)
    monkeypatch.setattr(model_dispatch_worker, "worker_ready", unexpected_readiness)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    await model_dispatch_worker.run(wait_for_worker=False)
    assert calls == ["run", "dispose"]
