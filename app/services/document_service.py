import mimetypes
import os
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import instrument_module
from app.models import Document
from app.repositories.document_repository import (
    DocumentMetadataChanges,
    DocumentRepository,
    EmptyDocument,
    UploadTooLarge,
)
from app.schemas.document import DocumentUpdateInput, DocumentUploadInput


ALLOWED_EXTENSIONS = {".csv", ".docx", ".md", ".pdf", ".txt"}


class UnsupportedDocumentType(Exception):
    pass


class EmptyDocumentUpload(Exception):
    pass


class DocumentUploadTooLarge(Exception):
    pass


class DocumentNotFound(Exception):
    pass


class InvalidDocumentUpdate(Exception):
    pass


def _safe_filename(filename: str | None) -> str:
    if not filename or "\x00" in filename:
        raise UnsupportedDocumentType
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise UnsupportedDocumentType
    return name[:255]


def upload_document(
    db: Session,
    *,
    owner_id: UUID,
    upload: UploadFile,
    metadata: DocumentUploadInput,
) -> Document:
    filename = _safe_filename(upload.filename)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedDocumentType

    title = metadata.title or PurePosixPath(filename).stem
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    repository = DocumentRepository()
    try:
        return repository.create_from_upload(
            db,
            owner_id=owner_id,
            title=title,
            original_filename=filename,
            description=metadata.description,
            content_type=content_type,
            extension=extension,
            source=upload.file,
        )
    except EmptyDocument as exc:
        raise EmptyDocumentUpload from exc
    except UploadTooLarge as exc:
        raise DocumentUploadTooLarge from exc


def list_documents(db: Session, *, owner_id: UUID) -> list[Document]:
    return DocumentRepository().list_for_owner(db, owner_id=owner_id)


def get_document(
    db: Session,
    *,
    owner_id: UUID,
    document_id: UUID,
) -> Document:
    document = DocumentRepository().get_for_owner(
        db,
        owner_id=owner_id,
        document_id=document_id,
    )
    if document is None:
        raise DocumentNotFound
    return document


def update_document(
    db: Session,
    *,
    owner_id: UUID,
    document_id: UUID,
    metadata: DocumentUpdateInput,
) -> Document:
    changes: DocumentMetadataChanges = {}
    if "title" in metadata.model_fields_set:
        if metadata.title is None:
            raise InvalidDocumentUpdate
        changes["title"] = metadata.title
    if "description" in metadata.model_fields_set:
        changes["description"] = metadata.description
    if not changes:
        raise InvalidDocumentUpdate

    document = DocumentRepository().update_metadata(
        db,
        owner_id=owner_id,
        document_id=document_id,
        changes=changes,
    )
    if document is None:
        raise DocumentNotFound
    return document


def delete_document(
    db: Session,
    *,
    owner_id: UUID,
    document_id: UUID,
) -> None:
    deleted = DocumentRepository().delete_for_owner(
        db,
        owner_id=owner_id,
        document_id=document_id,
    )
    if not deleted:
        raise DocumentNotFound


instrument_module(globals())
