from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import instrument_module
from app.models import ChatConversation, ChatMessage
from app.schemas.chat import ChatSource


class ChatConversationRepository:
    def create_for_owner(
        self,
        db: Session,
        *,
        owner_id: UUID,
        title: str,
    ) -> ChatConversation:
        conversation = ChatConversation(
            user_id=owner_id,
            title=title[:200],
        )
        db.add(conversation)
        db.flush()
        return conversation

    def get_for_owner(
        self,
        db: Session,
        *,
        owner_id: UUID,
        conversation_id: UUID,
        for_update: bool = False,
    ) -> ChatConversation | None:
        statement = select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == owner_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return db.scalar(statement)

    def list_for_owner(
        self,
        db: Session,
        *,
        owner_id: UUID,
    ) -> list[ChatConversation]:
        return list(
            db.scalars(
                select(ChatConversation)
                .where(ChatConversation.user_id == owner_id)
                .order_by(
                    ChatConversation.updated_at.desc(),
                    ChatConversation.id.desc(),
                )
            ).all()
        )

    def recent_messages(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> list[ChatMessage]:
        messages = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.message_index.desc())
            .limit(limit)
        ).all()
        return list(reversed(messages))

    def save_exchange(
        self,
        db: Session,
        *,
        conversation: ChatConversation,
        question: str,
        resolved_question: str,
        keywords: list[str],
        answer: str,
        sources: list[ChatSource],
    ) -> None:
        index = conversation.next_message_index
        db.add_all(
            [
                ChatMessage(
                    conversation_id=conversation.id,
                    message_index=index,
                    role="user",
                    content=question,
                    resolved_question=resolved_question,
                    keywords=keywords,
                ),
                ChatMessage(
                    conversation_id=conversation.id,
                    message_index=index + 1,
                    role="assistant",
                    content=answer,
                    sources=[
                        {
                            "document_id": str(source.document_id),
                            "document_title": source.document_title,
                            "heading_path": source.heading_path,
                            "excerpt": source.excerpt,
                        }
                        for source in sources
                    ],
                ),
            ]
        )
        conversation.next_message_index += 2
        conversation.updated_at = func.now()
        db.flush()

    def delete_for_owner(
        self,
        db: Session,
        *,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> bool:
        conversation = self.get_for_owner(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return False
        db.delete(conversation)
        return True


instrument_module(globals())
