"""Text extraction for uploaded files (onboarding docs, chat attachments). Supported: .pdf, .docx, .txt, .md."""
import io
import re

from docx import Document as DocxDocument
from pypdf import PdfReader

MAX_CHARS = 500000  # sanity ceiling against pathological uploads - RAG chunking (src/rag.py)
                     # bounds what actually reaches a prompt, this just stops runaway extraction


def extract_text(file_storage):
    """Return readable text from an uploaded file. Raises RuntimeError on anything unsupported or unreadable."""
    filename = file_storage.filename or "attachment"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Flask's upload stream is a tempfile.SpooledTemporaryFile, which on Python <3.11 doesn't
    # implement .seekable() - pypdf/python-docx both need to seek around (a .docx is a zip
    # archive), so wrap in BytesIO first, which fully implements the file interface.
    if ext in ("pdf", "docx"):
        buffer = io.BytesIO(file_storage.stream.read())

    if ext == "pdf":
        try:
            reader = PdfReader(buffer)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            # pypdf emits one newline per positioned text run, not per real line - common with
            # justified text / design-tool-generated PDFs, and produces a near one-word-per-line
            # mess. Collapse all whitespace runs to a single space so it reads as normal prose.
            # This also erases any genuine blank-line paragraph gaps, so a PDF-sourced story_bank
            # won't get per-story chunks the way a Heading-styled .docx does (see src/rag.py) -
            # use .docx with Heading styles for that.
            text = re.sub(r"\s+", " ", text)
        except Exception as e:
            raise RuntimeError(f"Could not read {filename} as a PDF: {e}") from e
    elif ext == "docx":
        try:
            doc = DocxDocument(buffer)
            # Mark Heading-styled paragraphs as markdown headings ("## text") so downstream
            # chunking (src/rag.py split_by_headings) can use them as section boundaries -
            # plain .text loses all style info, headings would otherwise be indistinguishable
            # from body text.
            lines = []
            for p in doc.paragraphs:
                stripped = p.text.strip()
                if not stripped:
                    continue
                if p.style.name.startswith("Heading"):
                    lines.append(f"## {stripped}")
                else:
                    lines.append(stripped)
            text = "\n\n".join(lines)
        except Exception as e:
            raise RuntimeError(f"Could not read {filename} as a .docx: {e}") from e
    elif ext in ("txt", "md"):
        text = file_storage.stream.read().decode("utf-8", errors="ignore")
    else:
        raise RuntimeError(f"Unsupported file type: .{ext or '?'}. Supported: .pdf, .docx, .txt, .md")

    text = text.strip()
    if not text:
        raise RuntimeError(f"No readable text found in {filename}.")
    return text[:MAX_CHARS]
