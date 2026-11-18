import asyncio
import threading

from fastapi.testclient import TestClient

from app import main


def test_ingestion_consumer_starts_and_stops_with_api(monkeypatch):
    started = threading.Event()
    stopped = threading.Event()

    async def idle_consumer():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(main, "consume_document_events", idle_consumer)

    with TestClient(main.app):
        assert started.wait(timeout=1)

    assert stopped.wait(timeout=1)
