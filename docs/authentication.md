# Authentication setup

The authentication API uses the existing `DATABASE_URL` and SQLAlchemy base.
Its migrations add the `users`, `user_sessions`, and `documents` tables; they
do not recreate the database, modify the pgvector extension, or touch Docker
volumes.

## Configuration

Add these values to the local `.env` file:

```env
AUTH_JWT_SECRET=<a random secret of at least 32 bytes>
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30
REFRESH_COOKIE_SECURE=false
CORS_ALLOWED_ORIGINS=["http://localhost:8501"]
```

Generate a secret locally with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep `.env` out of source control. For production, provide secrets through a
secret manager or environment variables, set `REFRESH_COOKIE_SECURE=true`, use
HTTPS, and set `CORS_ALLOWED_ORIGINS` to the exact trusted browser origins.
Do not use wildcard origins with credentialed CORS.

The refresh cookie is HttpOnly, SameSite=Lax, and scoped to `/auth`. The API
also returns refresh tokens in JSON for non-browser API clients; clients should
prefer the cookie and protect any token they handle.

Registration and login are rate limited using an in-memory SlowAPI backend.
This limits requests per application process and is not shared across workers;
use a trusted reverse proxy or shared rate-limit storage for multi-worker
production deployments. Client IPs are used only as rate-limit keys and are
not stored in the session table. User-Agent values are stored for session
display and can contain identifying information.

## Streamlit limitation

Streamlit calls FastAPI from its Python server, so cookies set on those
server-to-server responses do not become browser cookies. The Streamlit UI
keeps only the short-lived access token in server-side `st.session_state` and
discards the refresh token. When the access token expires, the user must log
in again. The UI does not place refresh tokens in Streamlit session state or
browser storage. A production browser experience with silent refresh needs a
browser-to-API cookie integration (for example, a custom Streamlit component
or same-origin reverse-proxy flow).

## Run

Apply the auth migration without changing existing data:

```powershell
python -m alembic upgrade head
```

Start the API and frontend in separate terminals:

```powershell
python -m uvicorn app.main:app --reload
```

```powershell
streamlit run frontend/streamlit_app.py
```

Set `ATLASAI_API_URL` if the API is not available at `http://localhost:8000`.
Run the automated tests with:

```powershell
python -m pytest
```

Tests use an isolated in-memory SQLite database and do not connect to the
development PostgreSQL database.

## Document uploads

`POST /documents` accepts an authenticated multipart request with a required
`upload` file and optional `title` and `description` form fields. Supported
extensions are `.csv`, `.docx`, `.md`, `.pdf`, and `.txt`; the default maximum
upload size is 20 MiB. The authenticated user ID is taken from the validated
access token, not from request input.

Files are streamed to `storage/documents/<user-id>/` under generated file
names. Document metadata and the relative storage key are saved in PostgreSQL.
Set `DOCUMENT_STORAGE_DIR` to use another local directory and
`MAX_DOCUMENT_UPLOAD_BYTES` to change the size limit. Uploaded files are
ignored by Git. After the database transaction commits, the API publishes a
`document inserted` event to the `document-events` Kafka topic. The event
contains `document_id`, `owner_id`, and `document_path` (the storage-relative
key, such as `<user-id>/<generated-id>.pdf`). The ingestion consumer verifies
that key against the database record before loading the file.

## Document ingestion worker

`app.workers.ingestion_worker.process_ingestion_event` accepts an event with
`document_id`, `owner_id`, and optional `document_path`. It loads the uploaded
file, extracts text from CSV, DOCX, Markdown, PDF, or TXT, chunks the text,
requests embeddings from Ollama, and stores the chunks and vectors in
PostgreSQL with pgvector. The worker uses PDF font size/bold metadata, Word
heading styles, and Markdown heading markers to group content; each resulting
chunk keeps its heading path in the text and database metadata. TXT headings
use a text heuristic, while CSV rows retain their column names as context. The
LangGraph workflow is called by `app.workers.kafka_consumer` after it receives
a `document inserted` event. Processing is acknowledged to Kafka only after
ingestion succeeds; failed events remain uncommitted for retry when the
consumer restarts.

The default embedding model is `nomic-embed-text` with 768 dimensions. Pull
that model in Ollama and apply database migrations with `alembic upgrade head`
before running the worker. If changing the embedding model or dimensions,
update the database vector column with a migration and keep
`EMBEDDING_DIMENSIONS` in sync with the model and column.

The asynchronous Kafka client dependency is pinned in
`requirements-kafka.txt`. Install it once with:

```powershell
python -m pip install -r requirements-kafka.txt
```

Start PostgreSQL and the local Kafka broker with:

```powershell
docker compose up -d postgres kafka
```

Set `KAFKA_BOOTSTRAP_SERVERS=localhost:9092` for a host-run API (this is the
default). The API starts the ingestion consumer automatically, so keep Kafka
running before starting the API; no separate consumer command is needed.

## Upload and chat frontend

The Streamlit dashboard provides a document uploader and a chat box. Upload
CSV, DOCX, Markdown, PDF, or TXT files; after Kafka ingestion indexes their
chunks, ask questions across all your documents or select one document.
`POST /chat` accepts an authenticated JSON request with `question` and an
optional `document_id`. It retrieves the five closest chunks belonging to the
signed-in user, uses `OLLAMA_MODEL` to answer from those excerpts, and returns
the answer with source titles, headings, and excerpts. The chat endpoint uses
`OLLAMA_EMBEDDING_MODEL` for question embeddings. If ingestion is still running,
the frontend tells you to wait and retry.

The API publishes to Kafka after document metadata and file storage have
committed. If Kafka is unavailable, the upload endpoint returns `503` and
states that the document was saved but the event was not published. The
consumer and API must use the same database and document storage directory.

## Application logs

Application functions log execution start/completion at `DEBUG` level and
failures with the exception type. Arguments, return values, tokens, and document
contents are not written to logs. `DEBUG=true` enables function-level tracing;
set `DEBUG=false` to reduce verbosity while keeping failures and
application-level messages visible.
