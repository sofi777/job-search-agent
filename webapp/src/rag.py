"""RAG over the documents knowledge base (resume, cover letter sample, story bank,
chat attachments) - NOT over profile fields or writing-style preferences, which stay
fully injected elsewhere (see src/agents.py).

Chunking is document-type aware, not just token-count based - a resume role section or
a story bank entry loses exactly the context ("which role was this achievement under?")
that makes a retrieved chunk useful if it gets split mid-section on token count alone:
- resume, cover_letter_sample: always exactly ONE chunk for the whole document. Both
  exceed the embedding model's 256-token max_seq_length (see EMBEDDING_MODEL_NAME),
  so the similarity ranking is effectively based on roughly the first 256 tokens - but
  the full text still displays/generates correctly whenever that chunk IS retrieved,
  and at this corpus size (a handful of documents total) it reliably lands in the
  top-k anyway. Trades embedding completeness for guaranteed role/document context.
- story_bank: one chunk per paragraph (blank-line-separated block), full stop
  (sub-split only if a single paragraph alone exceeds target_tokens) - never merged
  with a neighboring story, so each story stays a self-contained, independently
  retrievable/citable unit. Add a heading or blank line before each new story in the
  source file; either works as long as a blank line separates one story from the next.
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

# Document types with special chunking (see module docstring for why).
ONE_CHUNK_PER_PARAGRAPH_TYPES = {"story_bank"}
WHOLE_DOCUMENT_TYPES = {"resume", "cover_letter_sample"}


def split_sentences(text):
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def split_paragraphs(text):
    return [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]


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
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []

    if one_chunk_per_paragraph:
        chunks = []
        for para in paragraphs:
            if count_tokens(para) <= target_tokens:
                chunks.append(para)
            else:
                chunks.extend(_pack_sentences(split_sentences(para), target_tokens, overlap=False))
        return chunks

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
    if doc_type in WHOLE_DOCUMENT_TYPES:
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
