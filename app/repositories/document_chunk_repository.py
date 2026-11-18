import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.logging import instrument_module
from app.models import DocumentChunk


class DocumentChunkRepository:
    def replace_for_document(
        self,
        db: Session,
        *,
        document_id: uuid.UUID,
        chunks: list[str],
        heading_paths: list[str | None],
        embeddings: list[list[float]],
    ) -> int:
        if not (len(chunks) == len(heading_paths) == len(embeddings)):
            raise ValueError("Each document chunk needs a heading path and embedding.")

        db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        db.add_all(
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                content=content,
                heading_path=heading_path,
                embedding=embedding,
            )
            for index, (content, heading_path, embedding) in enumerate(
                zip(chunks, heading_paths, embeddings)
            )
        )
        return len(chunks)


instrument_module(globals())
