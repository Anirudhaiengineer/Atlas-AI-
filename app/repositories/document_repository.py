import os
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import instrument_module
from app.models import Document


class DocumentMetadataChanges(TypedDict, total=False):
    title: str
    description: str | None


class EmptyDocument(Exception):
    pass


class UploadTooLarge(Exception):
    pass


class DocumentRepository:
    chunk_size = 1024 * 1024

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        configured_dir = Path(storage_dir or settings.DOCUMENT_STORAGE_DIR).expanduser()
        if not configured_dir.is_absolute():
            configured_dir = Path(__file__).resolve().parents[2] / configured_dir
        self.storage_dir = configured_dir.resolve()

    def create_from_upload(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        title: str,
        original_filename: str,
        description: str | None,
        content_type: str,
        extension: str,
        source: BinaryIO,
    ) -> Document:
        document_id = uuid.uuid4()
        owner_dir = self.storage_dir / str(owner_id)
        destination = owner_dir / f"{document_id}{extension}"
        temporary_path: Path | None = None
        saved_path: Path | None = None

        try:
            owner_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".upload-",
                suffix=".tmp",
                dir=owner_dir,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                size_bytes = 0
                while True:
                    chunk = source.read(self.chunk_size)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > settings.MAX_DOCUMENT_UPLOAD_BYTES:
                        raise UploadTooLarge
                    temporary_file.write(chunk)
                if size_bytes == 0:
                    raise EmptyDocument
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, destination)
            temporary_path = None
            saved_path = destination

            document = Document(
                id=document_id,
                owner_id=owner_id,
                title=title,
                original_filename=original_filename,
                description=description,
                content_type=content_type,
                size_bytes=size_bytes,
                storage_key=destination.relative_to(self.storage_dir).as_posix(),
            )
            db.add(document)
            db.commit()
            saved_path = None
            db.refresh(document)
            return document
        except Exception:
            db.rollback()
            raise
        finally:
            for path in (temporary_path, saved_path):
                if path is not None:
                    path.unlink(missing_ok=True)
            if owner_dir.is_dir() and not any(owner_dir.iterdir()):
                owner_dir.rmdir()

    def list_for_owner(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
    ) -> list[Document]:
        return list(
            db.scalars(
                select(Document)
                .where(Document.owner_id == owner_id)
                .order_by(Document.created_at.desc(), Document.id.desc())
            )
        )

    def get_for_owner(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Document | None:
        return db.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.owner_id == owner_id,
            )
        )

    def update_metadata(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
        changes: DocumentMetadataChanges,
    ) -> Document | None:
        document = self.get_for_owner(
            db,
            owner_id=owner_id,
            document_id=document_id,
        )
        if document is None:
            return None

        for field, value in changes.items():
            setattr(document, field, value)
        db.commit()
        db.refresh(document)
        return document

    def delete_for_owner(
        self,
        db: Session,
        *,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> bool:
        document = self.get_for_owner(
            db,
            owner_id=owner_id,
            document_id=document_id,
        )
        if document is None:
            return False

        owner_dir = (self.storage_dir / str(owner_id)).resolve()
        stored_file = (self.storage_dir / document.storage_key).resolve()
        try:
            stored_file.relative_to(owner_dir)
        except ValueError as exc:
            raise ValueError("Document storage key is outside its owner directory.") from exc

        db.delete(document)
        db.commit()
        stored_file.unlink(missing_ok=True)
        if owner_dir.is_dir() and not any(owner_dir.iterdir()):
            owner_dir.rmdir()
        return True


instrument_module(globals())
