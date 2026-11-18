import csv
import io
import re
from statistics import median
from pathlib import Path
from typing import NotRequired, TypedDict
from uuid import UUID

from docx import Document as WordDocument
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pypdf import PdfReader

from app.core.config import settings
from app.core.logging import instrument_module, log_execution
from app.database.connection import SessionLocal
from app.repositories.document_chunk_repository import DocumentChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.services.embedding_service import generate_embeddings


class DocumentUploadedEvent(TypedDict):
    document_id: str
    owner_id: str
    document_path: NotRequired[str]


class DocumentSection(TypedDict):
    heading_path: list[str]
    text: str


class IngestionChunk(TypedDict):
    content: str
    heading_path: str | None


class IngestionState(TypedDict, total=False):
    document_id: str
    owner_id: str
    document_path: str
    original_filename: str
    file_bytes: bytes
    sections: list[DocumentSection]
    chunks: list[str]
    heading_paths: list[str | None]
    embeddings: list[list[float]]
    indexed_chunk_count: int


def _load_document(state: IngestionState) -> IngestionState:
    document_id = UUID(state["document_id"])
    owner_id = UUID(state["owner_id"])
    repository = DocumentRepository()

    with SessionLocal() as db:
        document = repository.get_for_owner(
            db,
            owner_id=owner_id,
            document_id=document_id,
        )
        if document is None:
            raise LookupError(f"Document {document_id} was not found for this owner.")
        event_path = state.get("document_path")
        if event_path is not None and event_path != document.storage_key:
            raise ValueError("Document event path does not match the stored document.")

        storage_root = repository.storage_dir.resolve()
        owner_dir = (storage_root / str(owner_id)).resolve()
        stored_file = (storage_root / document.storage_key).resolve()
        try:
            owner_dir.relative_to(storage_root)
            stored_file.relative_to(owner_dir)
        except ValueError as exc:
            raise ValueError(
                "Document storage path is outside its configured owner directory."
            ) from exc

        file_bytes = stored_file.read_bytes()
        original_filename = document.original_filename

    return {
        "document_id": str(document_id),
        "owner_id": str(owner_id),
        "document_path": document.storage_key,
        "original_filename": original_filename,
        "file_bytes": file_bytes,
    }


def _sections_from_text(
    text: str,
    *,
    detect_title_case_headings: bool = False,
    detect_numbered_headings: bool = True,
) -> list[DocumentSection]:
    return _sections_from_lines(
        [(line, None) for line in text.splitlines()],
        detect_title_case_headings=detect_title_case_headings,
        detect_numbered_headings=detect_numbered_headings,
    )


def _sections_from_lines(
    lines: list[tuple[str, int | None]],
    *,
    detect_title_case_headings: bool = False,
    detect_numbered_headings: bool = True,
) -> list[DocumentSection]:
    sections: list[DocumentSection] = []
    heading_path: list[str] = []
    body_lines: list[str] = []

    @log_execution
    def flush_body() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append({"heading_path": heading_path.copy(), "text": body})
            body_lines.clear()

    @log_execution
    def set_heading(title: str, level: int) -> None:
        flush_body()
        del heading_path[level - 1 :]
        heading_path.append(title.strip())

    markdown_heading = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
    numbered_heading = re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s+(.+?)\s*$")

    for raw_line, font_heading_level in lines:
        line = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", raw_line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            body_lines.append("")
            continue

        match = markdown_heading.match(line)
        if match:
            set_heading(match.group(2), len(match.group(1)))
            continue

        match = numbered_heading.match(line)
        if match and detect_numbered_headings:
            level = match.group(1).rstrip(".").count(".") + 1
            set_heading(f"{match.group(1)} {match.group(2)}", level)
            continue

        if font_heading_level is not None:
            set_heading(line, font_heading_level)
            continue

        if detect_title_case_headings and _is_title_case_heading(line):
            level = 2 if heading_path else 1
            set_heading(line, level)
            continue

        body_lines.append(line)

    flush_body()
    return sections


def _sections_from_pdf(file_bytes: bytes) -> list[DocumentSection]:
    reader = PdfReader(io.BytesIO(file_bytes))
    extracted_lines: list[tuple[str, float, bool, bool]] = []

    for page in reader.pages:
        @log_execution
        def capture_text(
            text: str,
            _current_matrix: list[float],
            _text_matrix: list[float],
            font_dictionary: dict[str, object] | None,
            font_size: float,
        ) -> None:
            font_name = str(font_dictionary.get("/BaseFont", "")) if font_dictionary else ""
            is_bold = "bold" in font_name.lower()
            for line in text.splitlines():
                if line.strip():
                    has_control = any(
                        ord(character) < 32 or 127 <= ord(character) < 160
                        for character in line
                    )
                    extracted_lines.append(
                        (line.strip(), float(font_size), is_bold, has_control)
                    )

        page.extract_text(visitor_text=capture_text)

    if not extracted_lines:
        return []

    typical_font_size = median(line[1] for line in extracted_lines)
    lines_with_heading_levels: list[tuple[str, int | None]] = []
    for text, font_size, is_bold, has_control in extracted_lines:
        heading_level = None
        numbered_match = re.match(r"^\s*(\d+(?:\.\d+)*\.?)\s+\S", text)
        is_numbered = numbered_match is not None
        is_short_line = len(text) <= 60 and len(text.split()) <= 8
        is_subheading = (
            is_bold
            and typical_font_size * 1.08 <= font_size <= typical_font_size * 1.25
            and not text.endswith((".", "!", ",", ";"))
            and (not text.endswith("?") or len(text) <= 35)
        )

        if not has_control and is_short_line:
            if font_size >= typical_font_size * 1.4 and (
                text.isupper() or text.istitle() or is_numbered
            ):
                heading_level = 1
            elif is_subheading:
                heading_level = 2
            elif text.isupper() and font_size >= typical_font_size * 1.08:
                heading_level = 1

        if heading_level is not None and numbered_match is not None:
            number_depth = numbered_match.group(1).rstrip(".").count(".") + 1
            heading_level = max(heading_level, number_depth)

        lines_with_heading_levels.append((text, heading_level))

    return _sections_from_lines(
        lines_with_heading_levels,
        detect_numbered_headings=False,
    )


def _is_title_case_heading(line: str) -> bool:
    if len(line) > 80 or len(line.split()) > 10 or line.endswith((".", "!", "?", ",")):
        return False
    if re.match(r"^(?:stage|step)\s+\d+\b", line, flags=re.IGNORECASE):
        return True
    return any(character.isalpha() for character in line) and (
        line.isupper() or line.istitle()
    )


def _sections_from_docx(file_bytes: bytes) -> list[DocumentSection]:
    document = WordDocument(io.BytesIO(file_bytes))
    sections: list[DocumentSection] = []
    heading_path: list[str] = []
    body_lines: list[str] = []

    @log_execution
    def flush_body() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append({"heading_path": heading_path.copy(), "text": body})
            body_lines.clear()

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style else ""
        match = re.fullmatch(r"Heading ([1-6])", style_name)
        if style_name == "Title":
            flush_body()
            heading_path[:] = [text]
        elif match:
            flush_body()
            level = int(match.group(1))
            del heading_path[level - 1 :]
            heading_path.append(text)
        else:
            body_lines.append(text)

    for table in document.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells).strip()
            if row_text:
                body_lines.append(row_text)

    flush_body()
    return sections


def _sections_from_csv(text: str) -> list[DocumentSection]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []

    headers = [
        header.strip() or f"Column {index + 1}"
        for index, header in enumerate(rows[0])
    ]
    heading_path = [f"CSV columns: {', '.join(headers)}"]
    sections: list[DocumentSection] = []
    for row in rows[1:]:
        fields = [
            f"{headers[index]}: {value.strip()}"
            for index, value in enumerate(row)
            if index < len(headers) and value.strip()
        ]
        if fields:
            sections.append(
                {"heading_path": heading_path.copy(), "text": " | ".join(fields)}
            )
    return sections


def _extract_text(state: IngestionState) -> IngestionState:
    extension = Path(state["original_filename"]).suffix.lower()
    file_bytes = state["file_bytes"]

    if extension == ".pdf":
        sections = _sections_from_pdf(file_bytes)
    elif extension == ".docx":
        sections = _sections_from_docx(file_bytes)
    elif extension == ".csv":
        sections = _sections_from_csv(file_bytes.decode("utf-8-sig"))
    elif extension in {".md", ".txt"}:
        text = file_bytes.decode("utf-8-sig")
        sections = _sections_from_text(
            text,
            detect_title_case_headings=extension == ".txt",
            detect_numbered_headings=extension == ".txt",
        )
    else:
        raise ValueError(f"Unsupported document extension: {extension}")

    if not sections:
        raise ValueError("The uploaded document contains no extractable text.")
    return {"sections": sections}


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[str]:
    if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError(
            "Chunk overlap must be non-negative and smaller than chunk size."
        )

    normalized_text = re.sub(r"\s+", " ", text).strip()
    words = normalized_text.split()
    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = start
        current_size = 0
        while end < len(words):
            next_size = len(words[end]) + (1 if end > start else 0)
            if end > start and current_size + next_size > chunk_size:
                break
            current_size += next_size
            end += 1

        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break

        next_start = end
        overlap_size = 0
        while next_start > start:
            next_word_size = len(words[next_start - 1]) + (1 if overlap_size else 0)
            if overlap_size + next_word_size > chunk_overlap:
                break
            overlap_size += next_word_size
            next_start -= 1
        start = max(next_start, start + 1)

    return chunks


def _chunk_document(state: IngestionState) -> IngestionState:
    structured_chunks = chunk_sections(state["sections"])
    if not structured_chunks:
        raise ValueError("The uploaded document produced no text chunks.")
    return {
        "chunks": [chunk["content"] for chunk in structured_chunks],
        "heading_paths": [chunk["heading_path"] for chunk in structured_chunks],
    }


def chunk_sections(
    sections: list[DocumentSection],
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[IngestionChunk]:
    if chunk_size <= 1 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError(
            "Chunk overlap must be non-negative and smaller than chunk size."
        )

    chunks: list[IngestionChunk] = []
    for section in sections:
        heading_path = " > ".join(section["heading_path"]) or None
        prefix = f"{heading_path}\n" if heading_path else ""
        if len(prefix) >= chunk_size:
            prefix = f"{prefix[: chunk_size - 2]}\n"
        body_size = chunk_size - len(prefix)
        body_overlap = min(chunk_overlap, body_size - 1)
        for body_chunk in chunk_text(
            section["text"],
            chunk_size=body_size,
            chunk_overlap=body_overlap,
        ):
            chunks.append(
                {
                    "content": f"{prefix}{body_chunk}",
                    "heading_path": heading_path,
                }
            )
    return chunks


def _embed_chunks(state: IngestionState) -> IngestionState:
    return {"embeddings": generate_embeddings(state["chunks"])}


def _index_chunks(state: IngestionState) -> IngestionState:
    with SessionLocal.begin() as db:
        count = DocumentChunkRepository().replace_for_document(
            db,
            document_id=UUID(state["document_id"]),
            chunks=state["chunks"],
            heading_paths=state["heading_paths"],
            embeddings=state["embeddings"],
        )
    return {"indexed_chunk_count": count}


def build_ingestion_graph() -> CompiledStateGraph:
    graph = StateGraph(IngestionState)
    graph.add_node("load_document", _load_document)
    graph.add_node("extract_text", _extract_text)
    graph.add_node("chunk_document", _chunk_document)
    graph.add_node("embed_chunks", _embed_chunks)
    graph.add_node("index_chunks", _index_chunks)
    graph.add_edge(START, "load_document")
    graph.add_edge("load_document", "extract_text")
    graph.add_edge("extract_text", "chunk_document")
    graph.add_edge("chunk_document", "embed_chunks")
    graph.add_edge("embed_chunks", "index_chunks")
    graph.add_edge("index_chunks", END)
    return graph.compile()


ingestion_graph = build_ingestion_graph()


def process_ingestion_event(event: DocumentUploadedEvent) -> int:
    state = {
        "document_id": event["document_id"],
        "owner_id": event["owner_id"],
    }
    if "document_path" in event:
        state["document_path"] = event["document_path"]
    result = ingestion_graph.invoke(state)
    return result["indexed_chunk_count"]
