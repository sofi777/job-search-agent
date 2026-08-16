"""In-memory view over the SQLite-backed state for the single demo user.

`profile`, `priority_weights`, `last_scan` and `jobs` are kept as plain
Python objects for cheap reads (routes/templates access them directly, same
as before). Every mutation goes through a save_*()/update_job_progress()
call here, which is the only place outside src/db.py that talks to
persistence. Routes never touch SQL directly.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import db, rag

TAILOR_TYPES = ["cover_letter", "resume", "qa"]
PREFERENCE_CATEGORIES = db.PREFERENCE_CATEGORIES


def _now():
    return datetime.now(timezone.utc).isoformat()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
UPLOADS_DIR = DATA_DIR / "uploads"

INDUSTRY_OPTIONS = ["Climate tech", "Healthcare", "Fintech", "Developer tools", "Consumer", "No preference"]
CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "CAD"]

PROFILE_FIELDS = [
    "resume_filename", "roles", "home_address", "commute_miles", "remote_ok",
    "remote_countries", "eligible_countries", "industries", "industries_text",
    "min_salary", "currency", "onboarding_complete",
]

COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Argentina", "Armenia",
    "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados",
    "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina",
    "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia",
    "Cameroon", "Canada", "Chad", "Chile", "China", "Colombia", "Costa Rica", "Croatia",
    "Cuba", "Cyprus", "Czech Republic", "Denmark", "Djibouti", "Dominican Republic",
    "Ecuador", "Egypt", "El Salvador", "Estonia", "Eswatini", "Ethiopia", "Fiji", "Finland",
    "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Guatemala",
    "Guinea", "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia",
    "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan", "Kazakhstan",
    "Kenya", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia",
    "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar", "Malawi", "Malaysia",
    "Maldives", "Mali", "Malta", "Mauritania", "Mauritius", "Mexico", "Moldova", "Monaco",
    "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea",
    "North Macedonia", "Norway", "Oman", "Pakistan", "Panama", "Papua New Guinea",
    "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia",
    "Rwanda", "Saudi Arabia", "Senegal", "Serbia", "Singapore", "Slovakia", "Slovenia",
    "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan",
    "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand",
    "Togo", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Uganda", "Ukraine",
    "United Arab Emirates", "United Kingdom", "United States", "Uruguay", "Uzbekistan",
    "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
]
_COUNTRIES_BY_LOWER = {c.lower(): c for c in COUNTRIES}


def normalize_country(name):
    """Return the canonical country name for a case-insensitive match, or None."""
    return _COUNTRIES_BY_LOWER.get(name.strip().lower())


user_id = None
profile = {}
priority_weights = {}
last_scan = None
jobs = []
DEMO_USER = {}


def _initials(name):
    parts = name.split()
    return "".join(p[0] for p in parts[:2]).upper()


def _load_user():
    global user_id, profile, priority_weights, last_scan, DEMO_USER
    row = db.ensure_demo_user()
    user_id = row["id"]
    profile = {k: row[k] for k in PROFILE_FIELDS}
    priority_weights = row["priority_weights"]
    last_scan = datetime.fromisoformat(row["last_scan"]) if row["last_scan"] else None
    DEMO_USER = {"name": row["name"], "email": row["email"], "initials": _initials(row["name"])}


def save_profile():
    db.update_user(user_id, **{k: profile[k] for k in PROFILE_FIELDS})


def save_priority_weights():
    db.update_user(user_id, priority_weights=priority_weights)


def save_last_scan():
    db.update_user(user_id, last_scan=last_scan.isoformat())


def reset_profile():
    global profile
    db.reset_user(user_id)
    row = db.ensure_demo_user()
    profile = {k: row[k] for k in PROFILE_FIELDS}


def _load_sample_catalog():
    with open(JOBS_FILE) as f:
        return json.load(f)


def reload_sample_jobs():
    """Re-read data/jobs.json and upsert its entries into the jobs table
    (matched by url), then refresh store.jobs from the database.

    Lets the sample catalog be hand-edited on disk and picked up without
    restarting the Flask process. Call via the "Reload sample data" action.
    Only touches sample-origin rows; jobs added via "Add Job Posting" are
    untouched. Status/comments/generated docs live in SQLite too.
    """
    db.upsert_sample_jobs(_load_sample_catalog())
    return reload_jobs()


def reload_jobs():
    """Refresh store.jobs from the database (all jobs, this user's progress merged).

    Does not touch data/jobs.json; use reload_sample_jobs() for that.
    """
    global jobs
    catalog = db.fetch_jobs()

    db.ensure_progress_rows(user_id, [j["id"] for j in catalog])
    progress = db.fetch_progress(user_id)

    merged = []
    for job in catalog:
        p = progress.get(job["id"], {})
        merged.append({
            **job,
            "status": p.get("status", "new"),
            "comments": p.get("comments", ""),
        })
    jobs = merged
    return jobs


def get_job(job_id):
    return next((j for j in jobs if j["id"] == job_id), None)


def job_url_exists(url):
    """Cheap in-memory check, so a duplicate URL fails before the LLM call."""
    return any(j["url"] == url for j in jobs)


def update_job_progress(job_id, **fields):
    db.update_progress(user_id, job_id, **fields)
    job = get_job(job_id)
    if job is not None:
        job.update(fields)


def add_custom_job(fields):
    """Insert a user-added job posting and refresh store.jobs. No match score
    is set; it stays unranked (shown as "Not yet ranked") until the next scan.

    Raises RuntimeError (from db.insert_job) if this url is already present,
    whether from the sample catalog or a previous custom add.
    """
    job_id = db.insert_job(fields)
    reload_jobs()
    return job_id


def _seed_sample_jobs_if_needed():
    if not db.has_sample_jobs():
        db.upsert_sample_jobs(_load_sample_catalog())


# ---- tailoring chat / artifacts / preferences ----------------------------

def get_chat(job_id, chat_type):
    """Return this job+tab's chat thread as [{role, content}, ...] (no id/model/timestamp) - what
    the model sees. For rendering (which needs message ids to look up citations), see get_chat_for_display.
    """
    return [{"role": m["role"], "content": m["content"]} for m in db.fetch_chat_messages(job_id, chat_type)]


def get_chat_for_display(job_id, chat_type):
    """Full chat rows (with id) plus each assistant message's citations, for rendering."""
    messages = db.fetch_chat_messages(job_id, chat_type)
    citations = db.fetch_citations_for_messages([m["id"] for m in messages])
    for m in messages:
        m["citations"] = citations.get(m["id"], [])
    return messages


def add_chat_message(job_id, chat_type, role, content, model=None):
    return db.add_chat_message(job_id, chat_type, role, content, model, _now())


def get_artifact_text(job_id, artifact_type):
    """Current cover_letter/resume draft text, or '' if none yet."""
    artifact = db.get_artifact(job_id, artifact_type)
    return artifact["content"] if artifact else ""


def save_artifact(job_id, artifact_type, content):
    db.upsert_artifact(job_id, artifact_type, content, _now())


def get_qa_list(job_id):
    return db.list_qa_artifacts(job_id)


def add_qa(job_id, question, answer):
    return db.insert_qa_artifact(job_id, question, answer, _now())


def update_qa(qa_id, answer):
    db.update_qa_artifact(qa_id, answer, _now())


def get_preferences():
    """Return {category: text} for general/cover_letter/resume/qa."""
    return {cat: row["text"] for cat, row in db.fetch_preferences().items()}


def get_preferences_full():
    """Return {category: {text, previous_text, updated_at}}, for the preferences page."""
    return db.fetch_preferences()


def save_preference(category, text):
    db.update_preference(category, text, _now())


# ---- documents (RAG knowledge base) ----------------------------------------
# Chunking/embedding lives in src/rag.py; these wrappers just cover the DB side
# (chunking itself needs the new document row's id, so it happens here, right
# after the insert, rather than being the caller's job).

def save_profile_document(doc_type, filename, content):
    """Save a profile-wide document (resume, cover_letter_sample, story_bank) - reused across every job."""
    # Drop the old row's chunks BEFORE upsert deletes it - chunks.document_id has a foreign key
    # on documents.id, so deleting a document that still has chunks pointing to it errors.
    existing = next((d for d in db.fetch_profile_documents(user_id) if d["type"] == doc_type), None)
    if existing:
        rag.delete_document(existing["id"])
    doc_id = db.upsert_profile_document(user_id, doc_type, filename, content, _now())
    rag.chunk_document({"id": doc_id, "job_id": None, "type": doc_type, "content": content})


def delete_profile_document(doc_type):
    """Remove an optional profile-wide document (cover_letter_sample, story_bank) and its chunks,
    leaving that slot empty rather than replaced. No-op if there's nothing on file for doc_type."""
    existing = next((d for d in db.fetch_profile_documents(user_id) if d["type"] == doc_type), None)
    if existing:
        rag.delete_document(existing["id"])
        db.delete_document(existing["id"])


def save_chat_attachment(job_id, filename, content, global_scope):
    """Save a file attached mid-chat. global_scope=True reuses it across every job; False scopes it to this job only."""
    target_job_id = None if global_scope else job_id
    doc_id = db.insert_document(user_id, target_job_id, "attachment", filename, content, _now())
    rag.chunk_document({"id": doc_id, "job_id": target_job_id, "type": "attachment", "content": content})


def get_profile_documents():
    return db.fetch_profile_documents(user_id)


def retrieve_context(job_id, query_text, top_k=3):
    """Top-k chunks (visible to this job) most similar to query_text. See src/rag.py."""
    return rag.retrieve(query_text, job_id, top_k)


def save_citations(chat_message_id, retrieved_chunks):
    """Persist which chunks backed one assistant reply, in source-number order (1-based), so the
    "[Source N]" citations in that reply stay clickable after the page reloads."""
    for i, chunk in enumerate(retrieved_chunks, start=1):
        db.insert_citation(chat_message_id, i, chunk["chunk_id"], chunk["score"], _now())


def get_citations_for_messages(message_ids):
    return db.fetch_citations_for_messages(message_ids)


def get_all_chunks():
    """Every chunk with its source document's filename/type, for the /chunks page."""
    return db.fetch_all_chunks_with_document_info(user_id)


def get_chunk_size():
    return int(db.get_setting("chunk_size_tokens", rag.DEFAULT_CHUNK_SIZE))


def rechunk_knowledge_base(chunk_size):
    rag.rechunk_all(chunk_size)


UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_load_user()
_seed_sample_jobs_if_needed()
reload_jobs()
