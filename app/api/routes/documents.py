from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.dependencies import AuthenticatedContext, get_current_auth
from app.core.logging import instrument_module, log_execution
from app.database.connection import get_db
from app.schemas.document import (
    DocumentResponse,
    DocumentUpdateInput,
    DocumentUploadInput,
)
from app.services.document_service import (
    DocumentNotFound,
    DocumentUploadTooLarge,
    EmptyDocumentUpload,
    InvalidDocumentUpdate,
    UnsupportedDocumentType,
    delete_document,
    get_document,
    list_documents,
    update_document,
    upload_document,
)
from app.services.kafka_service import (
    KafkaEventPublishError,
    publish_document_inserted,
)


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
@log_execution
def create_document(
    upload: Annotated[UploadFile, File(...)],
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
) -> DocumentResponse:
    try:
        document = upload_document(
            db,
            owner_id=auth.user.id,
            upload=upload,
            metadata=DocumentUploadInput(title=title, description=description),
        )
    except UnsupportedDocumentType:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Supported file types are CSV, DOCX, Markdown, PDF, and TXT.",
        ) from None
    except EmptyDocumentUpload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded document is empty.",
        ) from None
    except DocumentUploadTooLarge:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The uploaded document exceeds the configured size limit.",
        ) from None

    try:
        publish_document_inserted(document)
    except KafkaEventPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The document was saved, but its ingestion event could not be "
                "published. Retry ingestion after Kafka is available."
            ),
        ) from exc

    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
@log_execution
def get_documents(
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DocumentResponse]:
    return [
        DocumentResponse.model_validate(document)
        for document in list_documents(db, owner_id=auth.user.id)
    ]


@router.get("/{document_id}", response_model=DocumentResponse)
@log_execution
def get_document_metadata(
    document_id: UUID,
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentResponse:
    try:
        document = get_document(
            db,
            owner_id=auth.user.id,
            document_id=document_id,
        )
    except DocumentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from None
    return DocumentResponse.model_validate(document)


@router.patch("/{document_id}", response_model=DocumentResponse)
@log_execution
def update_document_metadata(
    document_id: UUID,
    metadata: DocumentUpdateInput,
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentResponse:
    try:
        document = update_document(
            db,
            owner_id=auth.user.id,
            document_id=document_id,
            metadata=metadata,
        )
    except DocumentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from None
    except InvalidDocumentUpdate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide a title or description to update.",
        ) from None
    return DocumentResponse.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
@log_execution
def remove_document(
    document_id: UUID,
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        delete_document(
            db,
            owner_id=auth.user.id,
            document_id=document_id,
        )
    except DocumentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


instrument_module(globals())
