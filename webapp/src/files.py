"""Text extraction for uploaded files (onboarding docs, chat attachments). Supported: .pdf, .docx, .txt, .md."""
import io

from docx import Document as DocxDocument
from pypdf import PdfReader

MAX_CHARS = 12000  # keep a single attachment from blowing out the prompt


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
        except Exception as e:
            raise RuntimeError(f"Could not read {filename} as a PDF: {e}") from e
    elif ext == "docx":
        try:
            doc = DocxDocument(buffer)
            text = "\n".join(p.text for p in doc.paragraphs)
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
