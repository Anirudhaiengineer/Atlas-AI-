
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.logging import configure_logging, log_execution
from app.core.rate_limit import limiter
from app.workers.kafka_consumer import consume_document_events

configure_logging(settings.DEBUG)

from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.documents import router as documents_router

logger = logging.getLogger(__name__)


def _report_consumer_exit(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return

    error = task.exception()
    if error is None:
        logger.error("Kafka ingestion consumer stopped unexpectedly.")
        return

    logger.error(
        "Kafka ingestion consumer stopped unexpectedly.",
        exc_info=(type(error), error, error.__traceback__),
    )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    consumer_task = asyncio.create_task(
        consume_document_events(),
        name="kafka-ingestion-consumer",
    )
    application.state.ingestion_consumer_task = consumer_task
    consumer_task.add_done_callback(_report_consumer_exit)
    try:
        yield
    finally:
        if not consumer_task.done():
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="AtlasAI",
    description="Production-oriented RAG Intelligence System",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)


@app.get("/")
@log_execution
async def root():
    return {
        "application": "AtlasAI",
        "status": "running",
        "stage": "local-development"
    }


@app.get("/health")
@log_execution
async def health():
    return {"status": "healthy"}
