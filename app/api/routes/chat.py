from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import AuthenticatedContext, get_current_auth
from app.core.logging import instrument_module, log_execution
from app.database.connection import get_db
from app.repositories.chat_conversation_repository import ChatConversationRepository
from app.retriever.cross_encoder import CrossEncoderUnavailable
from app.retriever.query_processing.keyword_extractor import (
    KeywordModelUnavailable,
)
from app.retriever.query_processing.nlp_pipeline import (
    CoreferenceModelUnavailable,
)
from app.schemas.chat import (
    ChatConversationDetail,
    ChatConversationSummary,
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
)
from app.services.chat_service import (
    ChatConversationNotFound,
    ChatModelUnavailable,
    NoIndexedContent,
    answer_document_question,
)


router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations", response_model=list[ChatConversationSummary])
@log_execution
def list_conversations(
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ChatConversationSummary]:
    conversations = ChatConversationRepository().list_for_owner(
        db,
        owner_id=auth.user.id,
    )
    return [
        ChatConversationSummary.model_validate(conversation)
        for conversation in conversations
    ]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ChatConversationDetail,
)
@log_execution
def get_conversation(
    conversation_id: UUID,
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationDetail:
    conversation = ChatConversationRepository().get_for_owner(
        db,
        owner_id=auth.user.id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )
    messages = ChatConversationRepository().recent_messages(
        db,
        conversation_id=conversation_id,
        limit=conversation.next_message_index,
    )
    return ChatConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            ChatMessageResponse.model_validate(message) for message in messages
        ],
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@log_execution
def delete_conversation(
    conversation_id: UUID,
    response: Response,
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    deleted = ChatConversationRepository().delete_for_owner(
        db,
        owner_id=auth.user.id,
        conversation_id=conversation_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("", response_model=ChatResponse)
@log_execution
def ask_question(
    request: ChatRequest,
    auth: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatResponse:
    try:
        return answer_document_question(
            db,
            owner_id=auth.user.id,
            request=request,
        )
    except NoIndexedContent:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No indexed document chunks are available.",
        ) from None
    except ChatModelUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured Ollama model is unavailable.",
        ) from None
    except ChatConversationNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        ) from None
    except CoreferenceModelUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The query coreference model is unavailable.",
        ) from None
    except KeywordModelUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The query keyword model is unavailable.",
        ) from None
    except CrossEncoderUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The query reranking model is unavailable.",
        ) from None


instrument_module(globals())
