import httpx

from app.core.config import settings
from app.core.logging import instrument_module


_BATCH_SIZE = 64


class EmbeddingServiceError(Exception):
    pass


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    embeddings: list[list[float]] = []
    endpoint = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/embed"

    with httpx.Client(timeout=120.0) as client:
        for offset in range(0, len(texts), _BATCH_SIZE):
            batch = texts[offset : offset + _BATCH_SIZE]
            try:
                response = client.post(
                    endpoint,
                    json={
                        "model": settings.OLLAMA_EMBEDDING_MODEL,
                        "input": batch,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise EmbeddingServiceError(
                    "Ollama could not generate embeddings."
                ) from exc

            batch_embeddings = (
                payload.get("embeddings") if isinstance(payload, dict) else None
            )
            if (
                not isinstance(batch_embeddings, list)
                or len(batch_embeddings) != len(batch)
                or any(
                    not isinstance(embedding, list)
                    or len(embedding) != settings.EMBEDDING_DIMENSIONS
                    or not all(
                        isinstance(value, (int, float)) for value in embedding
                    )
                    for embedding in batch_embeddings
                )
            ):
                raise EmbeddingServiceError(
                    "Ollama returned embeddings with an invalid shape."
                )

            embeddings.extend(batch_embeddings)

    return embeddings


instrument_module(globals())
