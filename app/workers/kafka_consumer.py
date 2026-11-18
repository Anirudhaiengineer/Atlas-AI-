import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.logging import configure_logging, instrument_module

configure_logging(debug=False)
logging.getLogger("aiokafka").setLevel(logging.WARNING)

from app.workers.ingestion_worker import (
    DocumentUploadedEvent,
    process_ingestion_event,
)


logger = logging.getLogger(__name__)


def handle_document_event(event: Any) -> int:
    if not isinstance(event, dict):
        raise ValueError("Kafka document event must be a JSON object.")
    if event.get("event_name") != "document inserted":
        raise ValueError(f"Unsupported document event: {event.get('event_name')!r}.")

    document_id = event.get("document_id")
    owner_id = event.get("owner_id")
    document_path = event.get("document_path")
    if not all(
        isinstance(value, str) and value
        for value in (document_id, owner_id, document_path)
    ):
        raise ValueError(
            "Document event must include document_id, owner_id, and document_path."
        )

    ingestion_event: DocumentUploadedEvent = {
        "document_id": document_id,
        "owner_id": owner_id,
        "document_path": document_path,
    }
    logger.info("Processing document %s.", document_id)
    indexed_count = process_ingestion_event(ingestion_event)
    logger.info(
        "Done: %s chunks embedded and stored for document %s.",
        indexed_count,
        document_id,
    )
    return indexed_count


async def consume_document_events() -> None:
    try:
        from aiokafka import AIOKafkaConsumer
    except ImportError as exc:
        raise RuntimeError(
            "Kafka support is unavailable; install requirements-kafka.txt."
        ) from exc

    consumer = AIOKafkaConsumer(
        settings.KAFKA_DOCUMENT_EVENTS_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="atlasai-document-ingestion",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
    )
    await consumer.start()
    logger.info(
        "Waiting for document uploads on Kafka topic '%s'.",
        settings.KAFKA_DOCUMENT_EVENTS_TOPIC,
    )
    try:
        async for message in consumer:
            await asyncio.to_thread(
                handle_document_event,
                message.value,
            )
            await consumer.commit()
    finally:
        await consumer.stop()


def main() -> None:
    asyncio.run(consume_document_events())


instrument_module(globals())


if __name__ == "__main__":
    main()
