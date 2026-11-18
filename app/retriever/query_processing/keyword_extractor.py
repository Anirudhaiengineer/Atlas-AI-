from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.logging import instrument_module

if TYPE_CHECKING:
    from spacy.language import Language


class KeywordModelUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def _load_lemma_pipeline() -> "Language":
    try:
        import spacy

        return spacy.load(
            "en_core_web_sm",
            disable=["parser", "ner", "textcat"],
        )
    except (ImportError, OSError) as exc:
        raise KeywordModelUnavailable(
            "The English spaCy package or model is not installed."
        ) from exc


def extract_keywords(query: str) -> list[str]:
    document = _load_lemma_pipeline()(query)
    keywords: list[str] = []
    seen: set[str] = set()
    for token in document:
        if token.is_space or token.is_punct or token.is_stop:
            continue
        lemma = token.lemma_.casefold().strip()
        if lemma and lemma not in seen:
            seen.add(lemma)
            keywords.append(lemma)
    return keywords


instrument_module(globals())
