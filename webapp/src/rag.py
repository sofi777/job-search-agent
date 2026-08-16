"""RAG over the documents knowledge base (resume, cover letter sample, story bank,
chat attachments) - NOT over profile fields or writing-style preferences, which stay
fully injected elsewhere (see src/agents.py).

Chunking is document-type aware, not just token-count based - a resume role section or
a story bank entry loses exactly the context ("which role was this achievement under?")
that makes a retrieved chunk useful if it gets split mid-section on token count alone:
- cover_letter_sample: always exactly ONE chunk for the whole document. It exceeds the
  embedding model's 256-token max_seq_length (see EMBEDDING_MODEL_NAME), so the
  similarity ranking is effectively based on roughly the first 256 tokens - but the
  full text still displays/generates correctly whenever that chunk IS retrieved, and
  at this corpus size (a handful of documents total) it reliably lands in the top-k
  anyway. Trades embedding completeness for guaranteed document context.
- resume: one chunk per role, one chunk per other major section (Education,
  Volunteering, Skills, ...) - see chunk_resume(). Prefers Heading-styled .docx
  markers ("## text", same as story_bank); falls back to a keyword + date-range text
  heuristic (split_resume_sections/split_roles) for PDF-sourced resumes, which carry
  no style info pypdf can read. That heuristic depends on the resume actually using
  ALL-CAPS section titles (RESUME_SECTION_KEYWORDS) and "(Mon YYYY - Mon YYYY)"-style
  role date ranges - unusual formatting falls back further to one whole-document chunk.
  Same 256-token embedding truncation tradeoff as cover_letter_sample per chunk.
- story_bank: one chunk per story, whatever its length (same 256-token embedding
  truncation tradeoff as above).
  If the source .docx uses Heading-styled paragraphs (Heading 1-6) before each story,
  src/files.py marks them as "## text" and chunking splits on those headings -
  everything up to the next heading is one chunk, blank lines within a story don't
  matter. Falls back to one-chunk-per-blank-line-paragraph only when the document has
  no headings at all. Never merged across a story boundary, so each story stays a
  self-contained, independently retrievable/citable unit.
- everything else (chat attachments): paragraph-aware, sentence-packed chunks of ~N
  tokens (N = chunk_size_tokens setting, default 128), counted with the embedding
  model's own tokenizer. Each chunk after the first carries the last sentence of the
  previous chunk as 1-sentence overlap, so context split across a boundary still shows
  up in the next chunk too.

Storage: chunk text + metadata live in SQLite (src/db.py `chunks` table, the source of
truth and what the /chunks page reads). Embeddings live in a local Chroma collection at
data/chroma/, keyed by the same chunk id, so the two never drift out of sync as long as
every write goes through this module.

Retrieval: cosine similarity, top-k, filtered in Python (not Chroma's `where`) to the
same job-scope rule as everywhere else - profile-wide chunks always visible, job-scoped
chunks only on their own job. The corpus is small enough (single user) that fetching a
generous candidate set and filtering in Python is simpler and more robust than getting
Chroma metadata filter syntax exactly right for a nullable field.
"""
import re
from pathlib import Path

from . import db

CHROMA_PATH = Path(__file__).resolve().parent.parent / "data" / "chroma"
DEFAULT_CHUNK_SIZE = 128
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
CANDIDATE_POOL = 20  # how many nearest neighbors to pull from Chroma before job-scope filtering

_embedder = None
_collection = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_or_create_collection("knowledge_base", metadata={"hnsw:space": "cosine"})
    return _collection


def count_tokens(text):
    return len(_get_embedder().tokenizer.encode(text, add_special_tokens=False))


def embed(texts):
    """texts: list[str] -> list[list[float]]."""
    return _get_embedder().encode(list(texts), convert_to_numpy=True).tolist()


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_HEADING_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)

# Resume section-header keywords (see split_resume_sections) and the "date range in
# parens" pattern that marks a role header within an experience section (see split_roles).
RESUME_SECTION_KEYWORDS = [
    "WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE", "EMPLOYMENT HISTORY", "EXPERIENCE",
    "EDUCATION", "VOLUNTEERING", "VOLUNTEER EXPERIENCE", "SKILLS", "CERTIFICATIONS",
    "PROJECTS", "SUMMARY", "AWARDS", "PUBLICATIONS",
]
EXPERIENCE_HEADERS = {"WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE", "EMPLOYMENT HISTORY", "EXPERIENCE"}
_SECTION_HEADER = re.compile(r"\b(" + "|".join(RESUME_SECTION_KEYWORDS) + r")\b")
_ROLE_DATE_RANGE = re.compile(
    r"\([A-Z][a-z]{2,8}\.?\s+\d{4}\s*[-–]\s*(?:[A-Z][a-z]{2,8}\.?\s+\d{4}|[Pp]resent)\)"
)

# Document types with special chunking (see module docstring for why).
ONE_CHUNK_PER_PARAGRAPH_TYPES = {"story_bank"}
WHOLE_DOCUMENT_TYPES = {"cover_letter_sample"}


def split_sentences(text):
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def split_paragraphs(text):
    return [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]


def split_by_headings(text):
    """Split into blocks starting at each markdown-style heading line ("## text", set by
    src/files.py from Heading-styled .docx paragraphs), each block running up to the next
    heading regardless of blank lines in between. Returns None if the text has no heading
    lines at all, so callers can fall back to blank-line paragraph splitting."""
    matches = list(_HEADING_LINE.finditer(text))
    if not matches:
        return None
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def split_resume_sections(text):
    """Split resume text into (label, block) major sections, using known header keywords
    (RESUME_SECTION_KEYWORDS) as boundaries - PDF resumes carry no style info, so this is a
    keyword heuristic, not a structural one. Returns None if no section header is found at
    all, so callers can fall back to treating the resume as one whole-document chunk."""
    matches = list(_SECTION_HEADER.finditer(text))
    if not matches:
        return None
    sections = []
    preamble = text[:matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            sections.append((m.group(1), block))
    return sections


def split_roles(block):
    """Split one experience-section block into one chunk per role, anchored on each role's
    "(Mon YYYY - Mon YYYY)" / "(Mon YYYY - Present)" date range. A role's "Company Title
    (dates)" header directly precedes its date range, so each boundary (after the first) is
    placed at the last sentence-ending period before that date range - keeping the previous
    role's trailing bullet with the previous role, and starting the new chunk at its header.
    Returns [block] unchanged if fewer than 2 date ranges are found (nothing to split on)."""
    matches = list(_ROLE_DATE_RANGE.finditer(block))
    if len(matches) < 2:
        return [block]

    # Reuse _SENTENCE_SPLIT (period/!/? followed by whitespace) rather than a raw rfind(".") -
    # a company name with a literal period and no following space (e.g. "Start.IO") would
    # otherwise be mistaken for the sentence boundary and get sliced in half.
    boundaries = [0]
    for m in matches[1:]:
        lookback = block[boundaries[-1]:m.start()]
        sentence_ends = list(_SENTENCE_SPLIT.finditer(lookback))
        boundaries.append(boundaries[-1] + sentence_ends[-1].end() if sentence_ends else m.start())
    boundaries.append(len(block))

    return [c for c in (block[boundaries[i]:boundaries[i + 1]].strip()
                         for i in range(len(boundaries) - 1)) if c]


def chunk_resume(text):
    """Section-aware resume chunking: one chunk per role, one chunk per other major section
    (Education, Volunteering, ...). Prefers "## "-marked headings (Heading-styled .docx, see
    split_by_headings) when present; otherwise falls back to the keyword + date-range
    heuristic above for PDF-sourced resumes, which carry no style info at all. If neither
    finds any structure, keeps the resume as one whole-document chunk (same 256-token
    embedding truncation tradeoff as cover_letter_sample - see module docstring)."""
    heading_blocks = split_by_headings(text)
    if heading_blocks is not None:
        chunks = []
        for block in heading_blocks:
            label = block.split("\n", 1)[0].lstrip("#").strip().upper()
            chunks.extend(split_roles(block) if label in EXPERIENCE_HEADERS else [block])
        return chunks

    sections = split_resume_sections(text)
    if sections is None:
        return [text.strip()] if text.strip() else []

    chunks = []
    for label, block in sections:
        chunks.extend(split_roles(block) if label in EXPERIENCE_HEADERS else [block])
    return chunks


def _pack_sentences(sentences, target_tokens, overlap):
    """Greedily pack sentences into chunks of ~target_tokens, never splitting a sentence.
    If overlap, each chunk after the first starts with the previous chunk's last sentence."""
    chunks, current, current_tokens = [], [], 0
    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if current and current_tokens + sentence_tokens > target_tokens:
            chunks.append(" ".join(current))
            if overlap:
                current, current_tokens = [current[-1]], count_tokens(current[-1])
            else:
                current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_text(text, target_tokens=DEFAULT_CHUNK_SIZE, one_chunk_per_paragraph=False):
    """Split into chunks of ~target_tokens. See module docstring for the paragraph-first
    strategy and what one_chunk_per_paragraph changes.
    """
    if one_chunk_per_paragraph:
        # No size cap here on purpose - a story staying whole is the entire point of this mode
        # (target_tokens only governs the generic chunker below). Same 256-token embedding
        # truncation tradeoff as WHOLE_DOCUMENT_TYPES: a long story's similarity ranking is
        # effectively based on its first ~256 tokens, but the full text still displays/
        # generates correctly whenever that chunk is retrieved.
        blocks = split_by_headings(text)
        if blocks is None:
            blocks = split_paragraphs(text)
        return blocks

    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []
    all_sentences = [s for para in paragraphs for s in split_sentences(para)]
    return _pack_sentences(all_sentences, target_tokens, overlap=True)


def chunk_document(document, chunk_size=None):
    """Chunk + embed one documents-table row (dict with id, job_id, type, content) and store
    both halves.

    Deletes any existing chunks for this document first, so re-chunking (or re-running
    this after an upload) never leaves duplicates.
    """
    chunk_size = chunk_size or int(db.get_setting("chunk_size_tokens", DEFAULT_CHUNK_SIZE))
    delete_document(document["id"])

    doc_type = document.get("type")
    if doc_type == "resume":
        texts = chunk_resume(document["content"])
    elif doc_type in WHOLE_DOCUMENT_TYPES:
        texts = [document["content"].strip()] if document["content"].strip() else []
    else:
        one_chunk_per_paragraph = doc_type in ONE_CHUNK_PER_PARAGRAPH_TYPES
        texts = chunk_text(document["content"], chunk_size, one_chunk_per_paragraph)
    if not texts:
        return

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    chunk_ids = []
    for i, text in enumerate(texts):
        chunk_id = db.insert_chunk(document["id"], document["job_id"], i, text, count_tokens(text), now)
        chunk_ids.append(chunk_id)

    vectors = embed(texts)
    collection = _get_collection()
    collection.add(ids=[str(cid) for cid in chunk_ids], embeddings=vectors, documents=texts)


def delete_document(document_id):
    """Remove a document's existing chunks from SQLite and Chroma (used before re-chunking, and
    after a replace-upload leaves the old document row gone - see cleanup_orphans)."""
    with db.db_transaction() as conn:
        old_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))]
    if old_ids:
        _get_collection().delete(ids=[str(i) for i in old_ids])
    db.delete_document_chunks(document_id)


def cleanup_orphans():
    """Delete chunks whose source document no longer exists (e.g. a resume re-upload replaced
    the old documents row via upsert_profile_document, which doesn't know about chunks)."""
    orphan_ids = db.orphaned_chunk_ids()
    if not orphan_ids:
        return
    _get_collection().delete(ids=[str(i) for i in orphan_ids])
    db.delete_chunks_by_ids(orphan_ids)


def rechunk_all(chunk_size):
    """Re-chunk and re-embed every document in the knowledge base at a new chunk size."""
    db.set_setting("chunk_size_tokens", str(chunk_size))
    db.delete_all_chunks()
    collection = _get_collection()
    try:
        existing = collection.get()["ids"]
        if existing:
            collection.delete(ids=existing)
    except Exception:
        pass  # empty/fresh collection

    user_id = db.ensure_demo_user()["id"]
    for document in db.fetch_all_documents(user_id):
        chunk_document(document, chunk_size)


def retrieve(query_text, job_id, top_k=3):
    """Return up to top_k {chunk_id, text, filename, doc_type, score} visible to this job,
    ranked by cosine similarity to query_text. Empty list if there's nothing in the knowledge
    base yet, or nothing scoped to this job - callers should handle that gracefully.
    """
    collection = _get_collection()
    if collection.count() == 0:
        return []

    query_vector = embed([query_text])[0]
    n = min(CANDIDATE_POOL, collection.count())
    results = collection.query(query_embeddings=[query_vector], n_results=n)

    candidate_ids = [int(i) for i in results["ids"][0]]
    distances = results["distances"][0]
    chunks_by_id = db.fetch_chunks_by_ids(candidate_ids)

    matches = []
    for chunk_id, distance in zip(candidate_ids, distances):
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            continue  # stale Chroma entry for a since-deleted chunk; cleanup_orphans() handles this normally
        if not (chunk["job_id"] is None or chunk["job_id"] == job_id):
            continue  # out of scope for this job
        matches.append({
            "chunk_id": chunk_id,
            "text": chunk["text"],
            "document_id": chunk["document_id"],
            "score": round(1 - distance, 4),  # cosine space: similarity = 1 - distance
        })
        if len(matches) == top_k:
            break

    doc_ids = {m["document_id"] for m in matches}
    docs = {d["id"]: d for d in db.fetch_all_documents(db.ensure_demo_user()["id"]) if d["id"] in doc_ids}
    for m in matches:
        doc = docs.get(m["document_id"], {})
        m["filename"] = doc.get("filename", "unknown")
        m["doc_type"] = doc.get("type", "unknown")

    return matches
