import pytest

from app.retriever import cross_encoder


class FakeCrossEncoder:
    def __init__(self, scores):
        self.scores = scores
        self.pairs = None

    def predict(self, pairs, *, batch_size, show_progress_bar):
        self.pairs = pairs
        assert batch_size == 16
        assert show_progress_bar is False
        return self.scores


def test_rerank_candidates_scores_query_passage_pairs(monkeypatch):
    model = FakeCrossEncoder([0.3, 0.9])
    monkeypatch.setattr(cross_encoder, "_load_cross_encoder", lambda: model)

    scores = cross_encoder.rerank_candidates(
        "How does the system authenticate users?",
        ["It stores documents.", "Authentication verifies user identity."],
    )

    assert scores == [0.3, 0.9]
    assert model.pairs == [
        (
            "How does the system authenticate users?",
            "It stores documents.",
        ),
        (
            "How does the system authenticate users?",
            "Authentication verifies user identity.",
        ),
    ]


def test_rerank_candidates_rejects_non_finite_scores(monkeypatch):
    monkeypatch.setattr(
        cross_encoder,
        "_load_cross_encoder",
        lambda: FakeCrossEncoder([float("nan")]),
    )

    with pytest.raises(cross_encoder.CrossEncoderUnavailable):
        cross_encoder.rerank_candidates("question", ["passage"])
