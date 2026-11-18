# Chat conversations and query resolution

Chat conversations and messages are stored per authenticated user. The Streamlit
frontend loads the user's saved conversations after login; users can start a new
conversation, resume an earlier one, or delete one.

`POST /chat` accepts an optional `conversation_id`. If omitted, AtlasAI starts a
conversation and returns its ID with the answer. Use `GET /chat/conversations`
to list conversations, `GET /chat/conversations/{conversation_id}` to load one,
and `DELETE /chat/conversations/{conversation_id}` to delete one. Conversation
history is private to its owner.

Before retrieval, the query-processing pipeline uses the latest 10 exchanges
(up to 20 messages) to resolve pronouns when a prior mention is found. A reusable
`extract_keywords(query)` function separately removes English stop words and
lemmatizes terms from the unchanged original query. The original, resolved
question, and extracted keywords are stored separately. The original sentence
is used for its embedding; the lexical search combines the extracted keywords
with the separate resolved query. If no antecedent is found, the resolved query
is the original question. Candidate chunks from both retrievers are fused, then
reranked with a CPU cross-encoder before the best chunks are shown and sent to
the answer model.

Keyword extraction uses spaCy's English lemmatizer. Coreference resolution uses
FastCoref's English model on CPU. Chunk reranking uses
`cross-encoder/ms-marco-MiniLM-L-6-v2`, which is downloaded from Hugging Face
when first needed. Install the project dependencies with
`pip install -r requirements.txt`; the NLP models download their pretrained
weights on first use unless they are already cached. Apply database changes
with `alembic upgrade head`.
