from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_id: UUID | None = None
    conversation_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be blank.")
        return value


class ChatSource(BaseModel):
    document_id: UUID
    document_title: str
    heading_path: str | None
    excerpt: str


class ChatResponse(BaseModel):
    conversation_id: UUID
    answer: str
    sources: list[ChatSource]


class ChatConversationSummary(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageResponse(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    keywords: list[str] = Field(default_factory=list)
    sources: list[ChatSource] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ChatConversationDetail(ChatConversationSummary):
    messages: list[ChatMessageResponse]
