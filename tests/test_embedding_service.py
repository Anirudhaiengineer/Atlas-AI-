import pytest

from app.core.config import settings
from app.services import embedding_service
from app.services.embedding_service import (
    EmbeddingServiceError,
    generate_embeddings,
)


def test_generate_embeddings_batches_text_and_validates_dimensions(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, batch):
            self.batch = batch

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "embeddings": [
                    [float(index)] * settings.EMBEDDING_DIMENSIONS
                    for index, _ in enumerate(self.batch)
                ]
            }

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == 120.0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, *, json):
            requests.append((url, json))
            return FakeResponse(json["input"])

    monkeypatch.setattr(embedding_service.httpx, "Client", FakeClient)
    texts = [f"chunk {index}" for index in range(65)]

    embeddings = generate_embeddings(texts)

    assert [len(request[1]["input"]) for request in requests] == [64, 1]
    assert all(
        request[1]["model"] == settings.OLLAMA_EMBEDDING_MODEL
        for request in requests
    )
    assert len(embeddings) == len(texts)
    assert all(
        len(embedding) == settings.EMBEDDING_DIMENSIONS
        for embedding in embeddings
    )


def test_generate_embeddings_rejects_mismatched_response(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": []}

    class FakeClient:
        def __init__(self, *, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(embedding_service.httpx, "Client", FakeClient)

    with pytest.raises(EmbeddingServiceError, match="invalid shape"):
        generate_embeddings(["one chunk"])
