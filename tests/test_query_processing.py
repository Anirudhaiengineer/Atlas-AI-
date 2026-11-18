from types import SimpleNamespace

from app.retriever.query_processing import hybrid_query, keyword_extractor, nlp_pipeline


class FakeCoreferencePrediction:
    def __init__(self, clusters):
        self.clusters = clusters

    def get_clusters(self, *, as_strings):
        assert as_strings is False
        return self.clusters


class FakeCoreferenceModel:
    def __init__(self, clusters):
        self.clusters = clusters
        self.text = None

    def predict(self, *, texts):
        self.text = texts[0]
        return [FakeCoreferencePrediction(self.clusters(self.text))]


def test_query_processing_resolves_pronoun_from_saved_context(monkeypatch):
    history = [
        SimpleNamespace(
            role="assistant",
            content="The Wi-Fi standard is IEEE 802.11.",
        )
    ]
    model = FakeCoreferenceModel(
        lambda text: [
            [
                (text.index("IEEE 802.11"), text.index("IEEE 802.11") + 11),
                (text.rindex("its"), text.rindex("its") + 3),
            ]
        ]
    )
    monkeypatch.setattr(nlp_pipeline, "_load_coreference_model", lambda: model)

    rewritten = nlp_pipeline.process_query("What about its security?", history)

    assert rewritten == "What about IEEE 802.11's security?"
    assert "assistant: The Wi-Fi standard is IEEE 802.11." in model.text


def test_query_processing_keeps_query_when_no_antecedent_is_found(monkeypatch):
    model = FakeCoreferenceModel(
        lambda text: [[(text.rindex("it"), text.rindex("it") + 2)]]
    )
    monkeypatch.setattr(nlp_pipeline, "_load_coreference_model", lambda: model)

    question = "What does it mean?"
    rewritten = nlp_pipeline.process_query(
        question,
        [SimpleNamespace(role="user", content="Hello.")],
    )

    assert rewritten == question


def test_query_without_history_skips_coreference_model(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("The coreference model should not be loaded.")

    monkeypatch.setattr(nlp_pipeline, "_load_coreference_model", fail_if_loaded)

    assert nlp_pipeline.process_query("What is Wi-Fi?", []) == "What is Wi-Fi?"


def test_keyword_extraction_removes_stop_words_and_lemmatizes():
    query = "The routers are running."

    keywords = keyword_extractor.extract_keywords(query)

    assert query == "The routers are running."
    assert keywords == ["router", "run"]


def test_hybrid_query_uses_original_text_for_embedding(monkeypatch):
    query = "What about its security?"
    embedding_inputs = []
    monkeypatch.setattr(
        hybrid_query,
        "extract_keywords",
        lambda raw_query: ["security"],
    )
    monkeypatch.setattr(
        hybrid_query,
        "process_query",
        lambda raw_query, _history: "What about the Wi-Fi standard's security?",
    )

    processed = hybrid_query.process_hybrid_query(
        query,
        [SimpleNamespace(role="assistant", content="Wi-Fi uses IEEE 802.11.")],
        embed_query=lambda raw_query: (
            embedding_inputs.append(raw_query) or [0.1, 0.2]
        ),
    )

    assert processed.original_query == query
    assert processed.resolved_query == "What about the Wi-Fi standard's security?"
    assert processed.keywords == ["security"]
    assert processed.embedding == [0.1, 0.2]
    assert embedding_inputs == [query]
