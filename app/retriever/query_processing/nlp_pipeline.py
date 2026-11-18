import re
from functools import lru_cache
from threading import Lock
from typing import Sequence

import httpx

from app.core.logging import instrument_module
from app.models import ChatMessage


class CoreferenceModelUnavailable(Exception):
    pass


_PRONOUN_PATTERN = re.compile(
    r"\b(?:"
    r"i|me|my|mine|myself|you|your|yours|yourself|yourselves|"
    r"he|him|his|himself|she|her|hers|herself|it|its|itself|"
    r"they|them|their|theirs|themselves|we|us|our|ours|ourselves|"
    r"this|that|these|those|which|who|whom|whose|whoever|whomever|"
    r"whatever|whichever|one|ones|oneself|one['’]s|someone|somebody|"
    r"something|anyone|anybody|anything|everyone|everybody|everything|"
    r"no\s+one|nobody|nothing|both|either|neither|few|many|several|"
    r"all|any|none|some|such|each\s+other|one\s+another"
    r")\b",
    flags=re.IGNORECASE,
)
_POSSESSIVE_PRONOUNS = {
    "my",
    "your",
    "his",
    "her",
    "its",
    "our",
    "their",
    "whose",
    "one's",
    "one’s",
}
_MODEL_LOCK = Lock()


@lru_cache(maxsize=1)
def _load_coreference_model():
    try:
        from fastcoref import FCoref
    except ImportError as exc:
        raise CoreferenceModelUnavailable(
            "FastCoref is not installed. Install the project requirements."
        ) from exc

    try:
        return FCoref(device="cpu", enable_progress_bar=False)
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        raise CoreferenceModelUnavailable(
            "The FastCoref model could not be loaded."
        ) from exc


def process_query(
    question: str,
    history: Sequence[ChatMessage],
) -> str:
    if not history or _PRONOUN_PATTERN.search(question) is None:
        return question

    history_text = "\n".join(
        f"{message.role}: {message.content}" for message in history
    )
    prefix = f"{history_text}\nuser: "
    text = f"{prefix}{question}"

    try:
        with _MODEL_LOCK:
            prediction = _load_coreference_model().predict(texts=[text])[0]
            clusters = prediction.get_clusters(as_strings=False)
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        raise CoreferenceModelUnavailable(
            "The FastCoref model could not process the query."
        ) from exc

    replacements: list[tuple[int, int, str]] = []
    for cluster in clusters or []:
        ordered_spans = sorted(cluster, key=lambda span: span[0])
        for start, end in ordered_spans:
            if start < len(prefix) or end > len(text):
                continue
            mention = text[start:end]
            if _PRONOUN_PATTERN.fullmatch(mention.strip()) is None:
                continue

            antecedents = [
                text[previous_start:previous_end].strip(" \t\r\n.,;:!?")
                for previous_start, previous_end in ordered_spans
                if previous_end <= start
                and _PRONOUN_PATTERN.fullmatch(
                    text[previous_start:previous_end].strip()
                )
                is None
            ]
            antecedent = next(
                (candidate for candidate in reversed(antecedents) if candidate),
                None,
            )
            if antecedent is not None:
                first_word, separator, remaining_words = antecedent.partition(" ")
                if first_word.lower() in {"a", "an", "the"}:
                    antecedent = first_word.lower() + separator + remaining_words
                trailing_text = text[end : len(text)]
                if (
                    mention.lower() in _POSSESSIVE_PRONOUNS
                    and re.match(r"\s+\w", trailing_text)
                ):
                    antecedent += "'" if antecedent.endswith("s") else "'s"
                replacements.append(
                    (start - len(prefix), end - len(prefix), antecedent)
                )

    resolved_question = question
    for start, end, antecedent in sorted(replacements, reverse=True):
        resolved_question = (
            resolved_question[:start] + antecedent + resolved_question[end:]
        )
    return resolved_question


instrument_module(globals())
