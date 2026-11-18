from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import chat as chat_routes
from app.models import ChatConversation, ChatMessage, User
from app.repositories.chat_conversation_repository import ChatConversationRepository
from app.retriever.query_processing.hybrid_query import ProcessedQuery
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services import chat_service
from app.services.chat_service import NoIndexedContent
from tests.conftest import auth_headers, login_user, register_user


class FakeConversationRepository:
    def __init__(self, history=None):
        self.conversation = SimpleNamespace(
            id=uuid4(),
            next_message_index=0,
        )
        self.history = history or []
        self.saved_exchange = None

    def create_for_owner(self, *_args, **_kwargs):
        return self.conversation

    def get_for_owner(self, *_args, **_kwargs):
        return self.conversation

    def recent_messages(self, *_args, **_kwargs):
        return self.history

    def save_exchange(self, *_args, **kwargs):
        self.saved_exchange = kwargs


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDatabase:
    def __init__(self, vector_rows, lexical_rows=None):
        self.results = [vector_rows, lexical_rows if lexical_rows is not None else []]
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.results[len(self.statements) - 1])

    def commit(self):
        pass


def test_chat_retrieves_owner_scoped_context_and_returns_source(monkeypatch):
    document_id = uuid4()
    owner_id = uuid4()
    chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        heading_path="Overview",
        content="AtlasAI stores document vectors.",
    )
    db = FakeDatabase([(chunk, "Project notes")], [(chunk, "Project notes")])
    repository = FakeConversationRepository()
    monkeypatch.setattr(
        chat_service,
        "ChatConversationRepository",
        lambda: repository,
    )
    captured = {}

    def embed(question):
        captured["embedding_question"] = question
        return [0.0] * 768

    monkeypatch.setattr(
        chat_service,
        "process_hybrid_query",
        lambda question, _history, *, embed_query: ProcessedQuery(
            original_query=question,
            resolved_query=f"Resolved: {question}",
            keywords=["store", "vector"],
            embedding=embed_query(question),
        ),
    )
    monkeypatch.setattr(chat_service, "_embed_question", embed)
    rerank_inputs = {}

    def rerank(question, passages):
        rerank_inputs["question"] = question
        rerank_inputs["passages"] = passages
        return [1.0]

    monkeypatch.setattr(chat_service, "rerank_candidates", rerank)

    def answer(question, resolved_question, context):
        captured["question"] = question
        captured["resolved_question"] = resolved_question
        captured["context"] = context
        return "It stores document vectors."

    monkeypatch.setattr(chat_service, "_answer_question", answer)

    result = chat_service.answer_document_question(
        db,
        owner_id=owner_id,
        request=ChatRequest(question="What does it store?", document_id=document_id),
    )

    assert result.answer == "It stores document vectors."
    assert result.conversation_id == repository.conversation.id
    assert result.sources[0].document_id == document_id
    assert "AtlasAI stores document vectors." in captured["context"]
    assert captured["question"] == "What does it store?"
    assert captured["resolved_question"] == "Resolved: What does it store?"
    assert captured["embedding_question"] == "What does it store?"
    assert rerank_inputs["question"] == "Resolved: What does it store?"
    assert rerank_inputs["passages"] == [
        "Project notes\nOverview\nAtlasAI stores document vectors."
    ]
    assert "documents.owner_id" in str(db.statements[0])
    assert "documents.id" in str(db.statements[0])
    assert "search_vector" in str(db.statements[1])
    assert repository.saved_exchange["question"] == "What does it store?"
    assert (
        repository.saved_exchange["resolved_question"]
        == "Resolved: What does it store?"
    )
    assert repository.saved_exchange["keywords"] == ["store", "vector"]


def test_cross_encoder_reranks_hybrid_candidates_before_context(monkeypatch):
    vector_only = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        heading_path=None,
        content="A semantically similar chunk.",
    )
    keyword_match = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        heading_path=None,
        content="A chunk matching the exact search terms.",
    )
    db = FakeDatabase(
        [(vector_only, "Vector result"), (keyword_match, "Hybrid result")],
        [(keyword_match, "Hybrid result")],
    )
    repository = FakeConversationRepository()
    monkeypatch.setattr(
        chat_service,
        "ChatConversationRepository",
        lambda: repository,
    )
    monkeypatch.setattr(
        chat_service,
        "process_hybrid_query",
        lambda question, _history, *, embed_query: ProcessedQuery(
            original_query=question,
            resolved_query=question,
            keywords=["specific", "search", "phrase"],
            embedding=embed_query(question),
        ),
    )
    monkeypatch.setattr(chat_service, "_embed_question", lambda _question: [0.0] * 768)
    monkeypatch.setattr(
        chat_service,
        "rerank_candidates",
        lambda _question, _passages: [0.01, 0.99],
    )
    monkeypatch.setattr(
        chat_service,
        "_answer_question",
        lambda _question, _resolved_question, _context: "Answer",
    )

    result = chat_service.answer_document_question(
        db,
        owner_id=uuid4(),
        request=ChatRequest(question="specific search phrase"),
    )

    assert result.sources[0].document_title == "Vector result"


def test_chat_reports_when_no_indexed_chunks_exist(monkeypatch):
    repository = FakeConversationRepository()
    monkeypatch.setattr(
        chat_service,
        "ChatConversationRepository",
        lambda: repository,
    )
    monkeypatch.setattr(
        chat_service,
        "process_hybrid_query",
        lambda question, _history, *, embed_query: ProcessedQuery(
            original_query=question,
            resolved_query=question,
            keywords=["document"],
            embedding=embed_query(question),
        ),
    )
    monkeypatch.setattr(chat_service, "_embed_question", lambda _question: [0.0] * 768)

    with pytest.raises(NoIndexedContent):
        chat_service.answer_document_question(
            FakeDatabase([]),
            owner_id=uuid4(),
            request=ChatRequest(question="What is in my document?"),
        )


def test_chat_endpoint_requires_authentication(client):
    response = client.post("/chat", json={"question": "What is in my document?"})

    assert response.status_code == 401


def test_chat_endpoint_returns_answer_for_authenticated_user(client, monkeypatch):
    register_user(client)
    tokens = login_user(client)
    conversation_id = uuid4()
    monkeypatch.setattr(
        chat_routes,
        "answer_document_question",
        lambda *_args, **_kwargs: ChatResponse(
            conversation_id=conversation_id,
            answer="Here is the answer.",
            sources=[],
        ),
    )

    response = client.post(
        "/chat",
        headers=auth_headers(tokens["access_token"]),
        json={"question": "What is in my document?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": str(conversation_id),
        "answer": "Here is the answer.",
        "sources": [],
    }


def test_conversation_history_is_persisted_and_owner_scoped(client, test_engine):
    register_user(client, "first@example.com")
    first_tokens = login_user(client, "first@example.com")
    register_user(client, "second@example.com")
    second_tokens = login_user(client, "second@example.com")

    with Session(test_engine) as db:
        user = db.scalar(
            select(User).where(User.email == "first@example.com")
        )
        assert user is not None
        source_id = uuid4()
        conversation = ChatConversation(
            user_id=user.id,
            title="Tell me about Wi-Fi",
            next_message_index=2,
        )
        db.add(conversation)
        db.flush()
        db.add_all(
            [
                ChatMessage(
                    conversation_id=conversation.id,
                    message_index=0,
                    role="user",
                    content="Tell me about Wi-Fi.",
                ),
                ChatMessage(
                    conversation_id=conversation.id,
                    message_index=1,
                    role="assistant",
                    content="Wi-Fi uses IEEE 802.11.",
                    sources=[
                        {
                            "document_id": str(source_id),
                            "document_title": "Wi-Fi notes",
                            "heading_path": "Overview",
                            "excerpt": "IEEE 802.11 is a wireless standard.",
                        }
                    ],
                ),
            ]
        )
        db.commit()
        conversation_id = conversation.id

    listed = client.get(
        "/chat/conversations",
        headers=auth_headers(first_tokens["access_token"]),
    )
    detail = client.get(
        f"/chat/conversations/{conversation_id}",
        headers=auth_headers(first_tokens["access_token"]),
    )
    forbidden = client.get(
        f"/chat/conversations/{conversation_id}",
        headers=auth_headers(second_tokens["access_token"]),
    )

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(conversation_id)
    assert detail.status_code == 200
    assert [message["content"] for message in detail.json()["messages"]] == [
        "Tell me about Wi-Fi.",
        "Wi-Fi uses IEEE 802.11.",
    ]
    assert (
        detail.json()["messages"][1]["sources"][0]["document_id"]
        == str(source_id)
    )
    assert forbidden.status_code == 404


def test_conversation_context_uses_only_the_latest_ten_exchanges(
    client,
    test_engine,
):
    register_user(client)
    with Session(test_engine) as db:
        user = db.scalar(select(User).where(User.email == "reader@example.com"))
        assert user is not None
        conversation = ChatConversation(
            user_id=user.id,
            title="History window",
            next_message_index=24,
        )
        db.add(conversation)
        db.flush()
        db.add_all(
            ChatMessage(
                conversation_id=conversation.id,
                message_index=index,
                role="user" if index % 2 == 0 else "assistant",
                content=f"Message {index}",
            )
            for index in range(24)
        )
        db.commit()
        conversation_id = conversation.id

        history = ChatConversationRepository().recent_messages(
            db,
            conversation_id=conversation_id,
            limit=20,
        )

    assert [message.message_index for message in history] == list(range(4, 24))


def test_conversation_repository_persists_original_and_resolved_query(test_engine):
    source_id = uuid4()
    with Session(test_engine) as db:
        user = User(email="reader@example.com", password_hash="test-hash")
        db.add(user)
        db.flush()
        repository = ChatConversationRepository()
        conversation = repository.create_for_owner(
            db,
            owner_id=user.id,
            title="What about its security?",
        )
        repository.save_exchange(
            db,
            conversation=conversation,
            question="What about its security?",
            resolved_question="What about the Wi-Fi standard's security?",
            keywords=["security"],
            answer="It uses network protections.",
            sources=[
                ChatSource(
                    document_id=source_id,
                    document_title="Wi-Fi notes",
                    heading_path="Security",
                    excerpt="Network protections are described here.",
                )
            ],
        )
        conversation_id = conversation.id
        db.commit()
        db.expire_all()
        saved_conversation = repository.get_for_owner(
            db,
            owner_id=user.id,
            conversation_id=conversation_id,
        )
        assert saved_conversation is not None
        messages = repository.recent_messages(
            db,
            conversation_id=conversation_id,
            limit=20,
        )

    assert saved_conversation.next_message_index == 2
    assert messages[0].content == "What about its security?"
    assert (
        messages[0].resolved_question
        == "What about the Wi-Fi standard's security?"
    )
    assert messages[0].keywords == ["security"]
    assert messages[1].sources[0]["document_id"] == str(source_id)


def test_chat_conversation_delete_removes_history(client, test_engine):
    register_user(client)
    tokens = login_user(client)
    with Session(test_engine) as db:
        user = db.scalar(select(User).where(User.email == "reader@example.com"))
        assert user is not None
        conversation = ChatConversation(
            user_id=user.id,
            title="Delete me",
            next_message_index=0,
        )
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id

    deleted = client.delete(
        f"/chat/conversations/{conversation_id}",
        headers=auth_headers(tokens["access_token"]),
    )
    detail = client.get(
        f"/chat/conversations/{conversation_id}",
        headers=auth_headers(tokens["access_token"]),
    )

    assert deleted.status_code == 204
    assert detail.status_code == 404
