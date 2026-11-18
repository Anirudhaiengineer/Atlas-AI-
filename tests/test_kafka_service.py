import sys
from types import ModuleType
from uuid import uuid4

from app.models import Document
from app.services.kafka_service import publish_document_inserted


def test_publish_document_inserted_sends_named_event_and_storage_path(monkeypatch):
    sent = {}

    class FakeProducer:
        def __init__(self, **kwargs):
            sent["producer_options"] = kwargs

        async def start(self):
            sent["started"] = True

        async def send_and_wait(self, topic, event, *, key):
            sent.update(topic=topic, event=event, key=key)

        async def stop(self):
            sent["stopped"] = True

    class FakeKafkaError(Exception):
        pass

    aiokafka = ModuleType("aiokafka")
    aiokafka.AIOKafkaProducer = FakeProducer
    errors = ModuleType("aiokafka.errors")
    errors.KafkaError = FakeKafkaError
    monkeypatch.setitem(sys.modules, "aiokafka", aiokafka)
    monkeypatch.setitem(sys.modules, "aiokafka.errors", errors)

    document_id = uuid4()
    owner_id = uuid4()
    document = Document(
        id=document_id,
        owner_id=owner_id,
        storage_key=f"{owner_id}/{document_id}.pdf",
    )
    publish_document_inserted(document)

    assert sent["event"] == {
        "event_name": "document inserted",
        "document_id": str(document_id),
        "owner_id": str(owner_id),
        "document_path": f"{owner_id}/{document_id}.pdf",
    }
    assert sent["started"] is True
    assert sent["stopped"] is True
    assert sent["key"] == str(document_id).encode("utf-8")
