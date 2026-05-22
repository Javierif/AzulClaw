"""Attachment ingestion, extraction, and prompt-preparation helpers."""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path


MAX_ATTACHMENTS_PER_TURN = 10
MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENT_REQUEST_SIZE_BYTES = (MAX_ATTACHMENT_SIZE_BYTES * MAX_ATTACHMENTS_PER_TURN) + (1024 * 1024)
MAX_VISION_PDF_PAGES = 20
INLINE_ATTACHMENT_TEXT_LIMIT = 60_000
CHUNK_SIZE = 2_000
CHUNK_OVERLAP = 200

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SUPPORTED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    DOCX_MIME,
    "text/plain",
    "text/markdown",
    "text/csv",
}
TEXT_LIKE_MIME_TYPES = {"text/plain", "text/markdown", "text/csv"}
DOCUMENT_LIKE_MIME_TYPES = {"application/pdf", DOCX_MIME, *TEXT_LIKE_MIME_TYPES}
WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")

_EXTENSION_MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".docx": DOCX_MIME,
}


class AttachmentError(ValueError):
    """Raised when an attachment cannot be accepted or processed."""


@dataclass(slots=True)
class AttachmentExtractionResult:
    mime_type: str
    kind: str
    extraction_status: str
    extracted_text: str
    page_count: int
    preview_json: str


def detect_mime_type(filename: str, data: bytes) -> str:
    """Detects supported MIME types from magic bytes or filename."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) > 11 and data[8:12] == b"WEBP":
        return "image/webp"

    suffix = Path(filename or "").suffix.lower()
    guessed = _EXTENSION_MIME_OVERRIDES.get(suffix)
    if guessed:
        return guessed
    return (mimetypes.guess_type(filename or "")[0] or "application/octet-stream").lower()


def classify_attachment_kind(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type in TEXT_LIKE_MIME_TYPES:
        return "text"
    return "document"


def ensure_supported_attachment(filename: str, data: bytes) -> tuple[str, str]:
    """Validates attachment type and size."""
    if len(data) > MAX_ATTACHMENT_SIZE_BYTES:
        raise AttachmentError("Attachment exceeds the 20 MB size limit.")

    mime_type = detect_mime_type(filename, data)
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise AttachmentError(f"Unsupported attachment type: {mime_type}")
    return mime_type, classify_attachment_kind(mime_type)


def extract_attachment(filename: str, data: bytes) -> AttachmentExtractionResult:
    """Extracts text and preview metadata for supported attachments."""
    mime_type, kind = ensure_supported_attachment(filename, data)
    preview = {"kind": kind, "mime_type": mime_type}

    if mime_type.startswith("image/"):
        preview.update(_image_preview_payload(data, mime_type))
        return AttachmentExtractionResult(
            mime_type=mime_type,
            kind=kind,
            extraction_status="ready",
            extracted_text="",
            page_count=1,
            preview_json=json.dumps(preview, ensure_ascii=False),
        )

    if mime_type == "application/pdf":
        page_count, page_texts, preview_extra = _extract_pdf(data)
        chars_total = sum(len(page_text.strip()) for page_text in page_texts)
        pages_with_text = sum(1 for text in page_texts if len(text.strip()) >= 40)
        avg_chars = int(chars_total / page_count) if page_count else 0
        useful = bool(page_count) and (pages_with_text / page_count) >= 0.6 and avg_chars >= 150
        preview.update(preview_extra)
        preview["pages_with_text"] = pages_with_text
        preview["avg_chars_per_page"] = avg_chars
        extracted_text = "\n\n".join(
            f"[Page {index + 1}]\n{text.strip()}"
            for index, text in enumerate(page_texts)
            if text.strip()
        )
        return AttachmentExtractionResult(
            mime_type=mime_type,
            kind=kind,
            extraction_status="ready" if useful else "low_text_quality",
            extracted_text=extracted_text,
            page_count=page_count,
            preview_json=json.dumps(preview, ensure_ascii=False),
        )

    if mime_type == DOCX_MIME:
        text = _extract_docx(data)
        preview["snippet"] = text[:240]
        return AttachmentExtractionResult(
            mime_type=mime_type,
            kind=kind,
            extraction_status="ready",
            extracted_text=text,
            page_count=1,
            preview_json=json.dumps(preview, ensure_ascii=False),
        )

    text = _extract_text_blob(data)
    preview["snippet"] = text[:240]
    return AttachmentExtractionResult(
        mime_type=mime_type,
        kind=kind,
        extraction_status="ready",
        extracted_text=text,
        page_count=1,
        preview_json=json.dumps(preview, ensure_ascii=False),
    )


def build_attachment_context(
    attachments: list[dict],
    user_message: str,
    *,
    inline_limit: int = INLINE_ATTACHMENT_TEXT_LIMIT,
) -> tuple[str, list[dict]]:
    """Builds textual document context and selects visual attachments for the turn."""
    textish: list[dict] = []
    visual: list[dict] = []
    for attachment in attachments:
        mime_type = str(attachment.get("mime_type", "")).lower()
        status = str(attachment.get("extraction_status", "")).strip().lower()
        extracted_text = str(attachment.get("extracted_text", "") or "")
        if extracted_text.strip():
            textish.append(attachment)
        elif mime_type.startswith("image/") or status == "low_text_quality":
            visual.append(attachment)

    combined_text = []
    for attachment in textish:
        extracted_text = str(attachment.get("extracted_text", "") or "").strip()
        if not extracted_text:
            continue
        combined_text.append(
            "\n".join(
                [
                    f"Attachment: {attachment.get('filename', 'document')}",
                    extracted_text,
                ]
            ).strip()
        )

    plain_context = "\n\n".join(item for item in combined_text if item).strip()
    if len(plain_context) <= inline_limit:
        return plain_context, visual

    ranked_chunks = rank_text_chunks(plain_context, user_message)
    selected_chunks: list[str] = []
    total_chars = 0
    for chunk in ranked_chunks:
        if total_chars + len(chunk) > inline_limit:
            break
        selected_chunks.append(chunk)
        total_chars += len(chunk)
    return "\n\n".join(selected_chunks).strip(), visual


def chunk_text(text: str, *, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Splits long text into overlapping chunks."""
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def rank_text_chunks(text: str, query: str) -> list[str]:
    """Ranks chunks with a simple lexical overlap score."""
    chunks = chunk_text(text)
    if not chunks:
        return []

    query_terms = set(WORD_RE.findall((query or "").lower()))
    if not query_terms:
        return chunks

    scored: list[tuple[int, int, str]] = []
    for index, chunk in enumerate(chunks):
        chunk_terms = WORD_RE.findall(chunk.lower())
        overlap = len(query_terms.intersection(chunk_terms))
        scored.append((overlap, -index, chunk))
    scored.sort(reverse=True)
    return [chunk for overlap, _, chunk in scored if overlap > 0] or chunks[: min(len(chunks), 8)]


def render_pdf_pages_as_data_uris(path: Path, *, max_pages: int = MAX_VISION_PDF_PAGES) -> list[str]:
    """Renders PDF pages as PNG data URIs for visual models."""
    try:
        import fitz  # type: ignore
    except Exception as error:  # pragma: no cover - depends on optional dependency
        raise AttachmentError("PDF rendering is unavailable because PyMuPDF is not installed.") from error

    document = fitz.open(path)
    try:
        total_pages = len(document)
        if total_pages > max_pages:
            raise AttachmentError(
                f"This scanned PDF has {total_pages} pages. The current limit is {max_pages} rendered pages."
            )
        rendered: list[str] = []
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            payload = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
            rendered.append(f"data:image/png;base64,{payload}")
        return rendered
    finally:
        document.close()


def build_vision_capability_error() -> str:
    return (
        "The currently configured model does not support visual analysis for images or scanned PDFs. "
        "Switch to a vision-capable model and try again."
    )


def _extract_text_blob(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _extract_docx(data: bytes) -> str:
    try:
        import docx2txt  # type: ignore
    except Exception as error:  # pragma: no cover - depends on optional dependency
        raise AttachmentError("DOCX extraction is unavailable because docx2txt is not installed.") from error

    with io.BytesIO(data) as handle:
        text = docx2txt.process(handle) or ""
    return str(text).strip()


def _extract_pdf(data: bytes) -> tuple[int, list[str], dict]:
    try:
        import fitz  # type: ignore
    except Exception as error:  # pragma: no cover - depends on optional dependency
        raise AttachmentError("PDF extraction is unavailable because PyMuPDF is not installed.") from error

    document = fitz.open(stream=data, filetype="pdf")
    try:
        page_texts: list[str] = []
        preview_extra: dict[str, object] = {"page_count": len(document)}
        if len(document) > 0:
            first_page = document.load_page(0)
            pixmap = first_page.get_pixmap(matrix=fitz.Matrix(0.75, 0.75), alpha=False)
            preview_extra["thumbnail_data_uri"] = (
                "data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")
            )
        for page in document:
            page_texts.append(page.get_text("text"))
        return len(document), page_texts, preview_extra
    finally:
        document.close()


def _image_preview_payload(data: bytes, mime_type: str) -> dict:
    try:
        from PIL import Image  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return {
            "thumbnail_data_uri": f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
        }

    image = Image.open(io.BytesIO(data))
    image.load()
    width, height = image.size
    preview_image = image.copy()
    preview_image.thumbnail((256, 256))
    buffer = io.BytesIO()
    save_format = "PNG" if mime_type == "image/png" else "JPEG"
    preview_image.save(buffer, format=save_format)
    preview_mime = "image/png" if save_format == "PNG" else "image/jpeg"
    return {
        "width": width,
        "height": height,
        "thumbnail_data_uri": f"data:{preview_mime};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}",
    }
