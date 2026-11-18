from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk
from app.repositories.document_chunk_repository import DocumentChunkRepository
from app.workers import ingestion_worker


def test_chunk_text_normalizes_whitespace_and_applies_overlap():
    chunks = ingestion_worker.chunk_text(
        "one two three four five six",
        chunk_size=13,
        chunk_overlap=5,
    )

    assert chunks == ["one two three", "three four", "four five six"]


def test_heading_chunking_keeps_sections_and_heading_context_together():
    sections = ingestion_worker._sections_from_text(
        "# Overview\nIntroductory text.\n"
        "## Details\nMore information.\n"
        "### Procedures\nFollow these steps."
    )

    chunks = ingestion_worker.chunk_sections(
        sections,
        chunk_size=100,
        chunk_overlap=20,
    )

    assert [chunk["heading_path"] for chunk in chunks] == [
        "Overview",
        "Overview > Details",
        "Overview > Details > Procedures",
    ]
    assert chunks[0]["content"] == "Overview\nIntroductory text."
    assert chunks[1]["content"] == "Overview > Details\nMore information."
    assert (
        chunks[2]["content"]
        == "Overview > Details > Procedures\nFollow these steps."
    )


def test_ingestion_graph_runs_nodes_in_order(monkeypatch):
    calls = []

    def load_document(state):
        calls.append("load_document")
        return state

    def extract_text(state):
        calls.append("extract_text")
        return state

    def chunk_document(state):
        calls.append("chunk_document")
        return state

    def embed_chunks(state):
        calls.append("embed_chunks")
        return state

    def index_chunks(state):
        calls.append("index_chunks")
        return {"indexed_chunk_count": 2}

    monkeypatch.setattr(ingestion_worker, "_load_document", load_document)
    monkeypatch.setattr(ingestion_worker, "_extract_text", extract_text)
    monkeypatch.setattr(ingestion_worker, "_chunk_document", chunk_document)
    monkeypatch.setattr(ingestion_worker, "_embed_chunks", embed_chunks)
    monkeypatch.setattr(ingestion_worker, "_index_chunks", index_chunks)

    graph = ingestion_worker.build_ingestion_graph()
    result = graph.invoke({"document_id": "doc-id", "owner_id": "owner-id"})

    assert calls == [
        "load_document",
        "extract_text",
        "chunk_document",
        "embed_chunks",
        "index_chunks",
    ]
    assert result["indexed_chunk_count"] == 2


def test_chunk_repository_persists_heading_path(test_engine):
    document_id = uuid4()
    with Session(test_engine) as db:
        count = DocumentChunkRepository().replace_for_document(
            db,
            document_id=document_id,
            chunks=["Overview\nThe document text."],
            heading_paths=["Overview"],
            embeddings=[[0.0] * ingestion_worker.settings.EMBEDDING_DIMENSIONS],
        )
        db.commit()
        chunk = db.scalar(
            select(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

    assert count == 1
    assert chunk is not None
    assert chunk.heading_path == "Overview"


def test_ingestion_graph_processes_the_requested_pdf(monkeypatch):
    pdf_path = (
        Path(__file__).resolve().parents[1]
        / "IEEE 802.11 Architecture.pdf"
    )
    assert pdf_path.is_file()
    indexed = {}

    def load_pdf(state):
        return {
            "document_id": state["document_id"],
            "owner_id": state["owner_id"],
            "original_filename": pdf_path.name,
            "file_bytes": pdf_path.read_bytes(),
        }

    def embed_chunks(state):
        return {
            "embeddings": [
                [0.0] * ingestion_worker.settings.EMBEDDING_DIMENSIONS
                for _ in state["chunks"]
            ]
        }

    def index_chunks(state):
        indexed["chunks"] = state["chunks"]
        indexed["heading_paths"] = state["heading_paths"]
        indexed["embeddings"] = state["embeddings"]
        return {"indexed_chunk_count": len(state["chunks"])}

    monkeypatch.setattr(ingestion_worker, "_load_document", load_pdf)
    monkeypatch.setattr(ingestion_worker, "_embed_chunks", embed_chunks)
    monkeypatch.setattr(ingestion_worker, "_index_chunks", index_chunks)
    monkeypatch.setattr(
        ingestion_worker,
        "ingestion_graph",
        ingestion_worker.build_ingestion_graph(),
    )

    indexed_count = ingestion_worker.process_ingestion_event(
        {
            "document_id": str(uuid4()),
            "owner_id": str(uuid4()),
        }
    )

    print(f"\nIngestion output for {pdf_path.name} ({indexed_count} chunks):")
    for index, (chunk, heading_path) in enumerate(
        zip(indexed["chunks"], indexed["heading_paths"]),
        start=1,
    ):
        output = (
            f"\nChunk {index} | Heading: {heading_path or '(no heading)'}\n{chunk}"
        )
        print(output.encode("ascii", errors="backslashreplace").decode("ascii"))

    assert indexed_count > 0
    assert indexed["chunks"]
    assert indexed["heading_paths"]
    assert len(indexed["chunks"]) == len(indexed["embeddings"]) == indexed_count
    assert len(indexed["heading_paths"]) == indexed_count
    heading_paths = [path for path in indexed["heading_paths"] if path]
    assert heading_paths
    assert any(" > " in path for path in heading_paths)
