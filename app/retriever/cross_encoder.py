from functools import lru_cache
import math
from typing import TYPE_CHECKING

from app.core.logging import instrument_module

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def _load_cross_encoder() -> "CrossEncoder":
    try:
        from sentence_transformers import CrossEncoder

        return CrossEncoder(_MODEL_NAME, device="cpu")
    except (ImportError, OSError, RuntimeError) as exc:
        raise CrossEncoderUnavailable(
            "The cross-encoder model could not be loaded."
        ) from exc


def rerank_candidates(query: str, passages: list[str]) -> list[float]:
    if not passages:
        return []

    try:
        raw_scores = _load_cross_encoder().predict(
            [(query, passage) for passage in passages],
            batch_size=16,
            show_progress_bar=False,
        )
        scores = [float(score) for score in raw_scores]
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CrossEncoderUnavailable(
            "The cross-encoder could not score the retrieved chunks."
        ) from exc

    if len(scores) != len(passages) or not all(
        math.isfinite(score) for score in scores
    ):
        raise CrossEncoderUnavailable(
            "The cross-encoder returned invalid scores."
        )
    return scores


instrument_module(globals())
