from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.core.logging import instrument_module
from app.models import ChatMessage
from app.retriever.query_processing.keyword_extractor import extract_keywords
from app.retriever.query_processing.nlp_pipeline import process_query


@dataclass(frozen=True)
class ProcessedQuery:
    original_query: str
    resolved_query: str
    keywords: list[str]
    embedding: list[float]


def process_hybrid_query(
    query: str,
    history: Sequence[ChatMessage],
    *,
    embed_query: Callable[[str], list[float]],
) -> ProcessedQuery:
    keywords = extract_keywords(query)
    resolved_query = process_query(query, history)
    embedding = embed_query(query)
    return ProcessedQuery(
        original_query=query,
        resolved_query=resolved_query,
        keywords=keywords,
        embedding=embedding,
    )


instrument_module(globals())
