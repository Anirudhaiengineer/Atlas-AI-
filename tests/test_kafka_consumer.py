import logging

from app.workers import kafka_consumer


def test_document_inserted_event_invokes_ingestion_worker(monkeypatch):
    received = {}

    def process_ingestion_event(event):
        received.update(event)
        return 3

    monkeypatch.setattr(
        kafka_consumer,
        "process_ingestion_event",
        process_ingestion_event,
    )

    indexed_count = kafka_consumer.handle_document_event(
        {
            "event_name": "document inserted",
            "document_id": "document-id",
            "owner_id": "owner-id",
            "document_path": "owner-id/document-id.txt",
        }
    )

    assert indexed_count == 3
    assert received == {
        "document_id": "document-id",
        "owner_id": "owner-id",
        "document_path": "owner-id/document-id.txt",
    }


def test_document_inserted_event_logs_progress(monkeypatch, caplog):
    monkeypatch.setattr(kafka_consumer, "process_ingestion_event", lambda event: 3)

    with caplog.at_level(logging.INFO, logger="app.workers.kafka_consumer"):
        kafka_consumer.handle_document_event(
            {
                "event_name": "document inserted",
                "document_id": "document-id",
                "owner_id": "owner-id",
                "document_path": "owner-id/document-id.txt",
            }
        )

    assert "Processing document document-id." in caplog.text
    assert (
        "Done: 3 chunks embedded and stored for document document-id."
        in caplog.text
    )


def test_document_event_rejects_missing_path():
    try:
        kafka_consumer.handle_document_event(
            {
                "event_name": "document inserted",
                "document_id": "document-id",
                "owner_id": "owner-id",
            }
        )
    except ValueError as exc:
        assert "document_path" in str(exc)
    else:
        raise AssertionError("Expected malformed document event to be rejected.")
