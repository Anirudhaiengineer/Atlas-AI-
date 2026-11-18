import asyncio
import json

from app.core.config import settings
from app.core.logging import instrument_module
from app.models import Document


class KafkaEventPublishError(Exception):
    pass


async def _send_document_inserted(event: dict[str, str]) -> None:
    try:
        from aiokafka import AIOKafkaProducer
        from aiokafka.errors import KafkaError
    except ImportError as exc:
        raise KafkaEventPublishError(
            "Kafka support is unavailable; install requirements-kafka.txt."
        ) from exc

    producer = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
    )
    started = False
    try:
        await producer.start()
        started = True
        await producer.send_and_wait(
            settings.KAFKA_DOCUMENT_EVENTS_TOPIC,
            event,
            key=event["document_id"].encode("utf-8"),
        )
    except (KafkaError, OSError) as exc:
        raise KafkaEventPublishError("Could not publish the document event.") from exc
    finally:
        if started:
            try:
                await producer.stop()
            except (KafkaError, OSError) as exc:
                raise KafkaEventPublishError(
                    "Could not close the Kafka producer cleanly."
                ) from exc


def build_document_inserted_event(document: Document) -> dict[str, str]:
    return {
        "event_name": "document inserted",
        "document_id": str(document.id),
        "owner_id": str(document.owner_id),
        "document_path": document.storage_key,
    }


def publish_document_inserted(document: Document) -> None:
    event = build_document_inserted_event(document)
    asyncio.run(_send_document_inserted(event))


instrument_module(globals())
