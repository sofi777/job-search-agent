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

from . import db, filters, rag

TAILOR_TYPES = ["cover_letter", "resume", "qa"]
PREFERENCE_CATEGORIES = db.PREFERENCE_CATEGORIES

# "rejected"/"irrelevant" are set by the user to mark a job they don't want - kept (not
# deleted) so get_known_urls still dedupes it out of future searches. Both are excluded,
# along with "applied", from re-scoring (see scanner.run_scan's pending_only) - there's no
# more fit to judge once the user has already decided.
JOB_STATUSES = ["new", "viewed", "applied", "rejected", "irrelevant"]
TERMINAL_STATUSES = {"applied", "rejected", "irrelevant"}


def _now():
    return datetime.now(timezone.utc).isoformat()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
UPLOADS_DIR = DATA_DIR / "uploads"

INDUSTRY_OPTIONS = [
    "Climate tech", "Healthcare", "Fintech", "Developer tools", "Consumer",
    "Mobility", "Govtech", "SaaS", "AI/ML", "Cybersecurity", "E-commerce",
    "Edtech", "Proptech", "Gaming", "Biotech", "No preference",
]
CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "CAD"]

PROFILE_FIELDS = [
    "resume_filename", "roles", "home_address", "commute_miles", "remote_ok",
    "remote_countries", "eligible_countries", "industries", "industries_text",
    "min_salary", "currency", "onboarding_complete", "followed_companies",
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
    scores = db.fetch_latest_scores()

    merged = []
    for job in catalog:
        p = progress.get(job["id"], {})
        s = scores.get(job["id"])
        merged.append({
            **job,
            "status": p.get("status", "new"),
            "comments": p.get("comments", ""),
            "match": s["score"] if s else None,
            "match_summary": s["summary"] if s else "",
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


def get_followed_companies():
    return list(profile["followed_companies"])


def save_followed_companies(names):
    """Overwrite the profile-wide followed-companies list (plain names, no ATS-specific
    metadata - see src/components/ats.py for the platform+slug that lives alongside it in
    that component's own config). Used both by /profile and by "apply to profile" on the
    SerpAPI/ATS component pages."""
    profile["followed_companies"] = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    save_profile()


def add_followed_companies(names):
    """Merge new names into the existing list, no duplicates, order preserved."""
    save_followed_companies(profile["followed_companies"] + list(names))


# ---- tailoring chat sessions ("compare panes") -----------------------------
# Each session is one independent thread + model + artifact/qa-list - no cap on how
# many can exist per job+tab - see db.py's chat_sessions table docstring.

def get_chat_sessions(job_id, chat_type):
    """This job+tab's panes, oldest/leftmost first. Does not auto-create one if none exist -
    that bootstrap is app.py's job (tailor()), keeping this a plain read."""
    return db.fetch_chat_sessions(job_id, chat_type)


def get_chat_session(session_id):
    return db.get_chat_session(session_id)


def create_chat_session(job_id, chat_type, model=None):
    return db.create_chat_session(job_id, chat_type, model, _now())


def set_session_model(session_id, model):
    """model=None means N/A - no call is made for this pane until one is picked again."""
    db.update_chat_session_model(session_id, model)


def switch_session_model(session_id, job_id, chat_type, model):
    """Change a pane's model - normally just updates it in place. But if this pane is still
    empty (no messages sent yet) and a previously removed pane for this same job+tab+model
    still has history sitting hidden, resurface that pane instead of starting a fresh blank
    one - see remove_chat_session. Returns the session id the caller should actually use for
    this turn (usually session_id itself, unchanged)."""
    if model:
        hidden_match = db.find_hidden_chat_session(job_id, chat_type, model)
        if hidden_match and not db.fetch_chat_messages(session_id):
            db.delete_chat_session(session_id)
            db.unhide_chat_session(hidden_match)
            return hidden_match
    db.update_chat_session_model(session_id, model)
    return session_id


def remove_chat_session(session_id):
    """Hide a pane rather than deleting it - its chat/artifact history is kept, and picking
    the same model again in a new pane resurfaces it (see switch_session_model)."""
    db.hide_chat_session(session_id)


# ---- tailoring chat / artifacts / preferences ----------------------------

def get_chat(session_id):
    """Return this pane's chat thread as [{role, content}, ...] (no id/model/timestamp) - what
    the model sees. For rendering (which needs message ids to look up citations), see get_chat_for_display.
    """
    return [{"role": m["role"], "content": m["content"]} for m in db.fetch_chat_messages(session_id)]


def get_chat_for_display(session_id):
    """Full chat rows (with id) plus each assistant message's citations, for rendering."""
    messages = db.fetch_chat_messages(session_id)
    citations = db.fetch_citations_for_messages([m["id"] for m in messages])
    for m in messages:
        m["citations"] = citations.get(m["id"], [])
    return messages


def add_chat_message(
    session_id, job_id, chat_type, role, content, model=None,
    response_time_seconds=None, input_tokens=None, output_tokens=None, artifact_text=None,
):
    return db.add_chat_message(
        session_id, job_id, chat_type, role, content, model, _now(),
        response_time_seconds, input_tokens, output_tokens, artifact_text,
    )


def get_chat_message(message_id):
    return db.get_chat_message(message_id)


def rate_chat_message(message_id, rating):
    """Record a thumbs up/down on one assistant chat response (per-pane now - see
    get_chat_sessions - rather than per job+tab).

    Persists to chat_messages.rating (so the buttons show as already-rated after a reload)
    and appends a full record - question, response, the resulting artifact (cover letter/
    résumé/Q&A answer, not just the chat reply), model, timestamp, response time, token
    counts - to data/results.json for the Results tab. A message can only be rated once: if
    it already has a rating, this is a no-op (the UI disables both buttons after the first
    click, so this only matters against a direct API call) - it never overwrites the DB
    rating or duplicates the results.json entry out of sync with each other. Raises
    ValueError if message_id doesn't exist or isn't an assistant reply.
    """
    message = db.get_chat_message(message_id)
    if message is None or message["role"] != "assistant":
        raise ValueError("Message not found.")
    if message["rating"]:
        return

    db.set_chat_message_rating(message_id, rating)

    question = db.get_preceding_chat_message(message["session_id"], message_id, "user")
    chat_session = db.get_chat_session(message["session_id"])
    job = get_job(chat_session["job_id"]) if chat_session else None
    _append_result({
        "id": message_id,
        "session_id": message["session_id"],
        "job_id": chat_session["job_id"] if chat_session else None,
        "job_company": job["company"] if job else "",
        "job_title": job["title"] if job else "",
        "tab": chat_session["type"] if chat_session else "",
        "question": question["content"] if question else "",
        "response": message["content"],
        # None (not "") means this message predates artifact_text tracking - see db.py's
        # backfill migration and results.html, which render that case differently from a
        # genuine "" (this turn really didn't produce/change anything).
        "artifact": message["artifact_text"],
        "rating": rating,
        "model": message["model"],
        "timestamp": message["created_at"],
        "response_time_seconds": message["response_time_seconds"],
        "input_tokens": message["input_tokens"],
        "output_tokens": message["output_tokens"],
    })


RESULTS_FILE = DATA_DIR / "results.json"


def load_results():
    if not RESULTS_FILE.exists():
        return []
    try:
        return json.loads(RESULTS_FILE.read_text())
    except json.JSONDecodeError:
        return []


def _append_result(entry):
    results = load_results()
    results.append(entry)
    RESULTS_FILE.write_text(json.dumps(results, indent=2))


def results_stats(results):
    """{"overall": bucket, "by_model": {model: bucket}}, bucket = {up, down, total, percent_positive}.
    percent_positive is None (not 0) when total is 0, so the template can show "-" instead of a
    misleading 0%."""
    def bucket(rows):
        up = sum(1 for r in rows if r["rating"] == "up")
        down = sum(1 for r in rows if r["rating"] == "down")
        total = up + down
        return {
            "up": up, "down": down, "total": total,
            "percent_positive": round(100 * up / total) if total else None,
        }

    by_model = {}
    for r in results:
        by_model.setdefault(r["model"], []).append(r)
    return {
        "overall": bucket(results),
        "by_model": {model: bucket(rows) for model, rows in by_model.items()},
    }


USAGE_FILE = DATA_DIR / "usage.json"


def load_usage():
    """Every logged LLM call - see src/agents.py's _log_usage, the only writer of this file."""
    if not USAGE_FILE.exists():
        return []
    try:
        return json.loads(USAGE_FILE.read_text())
    except json.JSONDecodeError:
        return []


def usage_stats(usage):
    """{"total_calls", "total_tokens", "total_cost_usd", "by_model": {model: {calls, tokens, cost_usd}}}."""
    by_model = {}
    for u in usage:
        m = by_model.setdefault(u["model"], {"calls": 0, "tokens": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["tokens"] += u.get("total_tokens", 0)
        m["cost_usd"] += u.get("estimated_cost_usd", 0)
    for m in by_model.values():
        m["cost_usd"] = round(m["cost_usd"], 4)
    return {
        "total_calls": len(usage),
        "total_tokens": sum(u.get("total_tokens", 0) for u in usage),
        "total_cost_usd": round(sum(u.get("estimated_cost_usd", 0) for u in usage), 4),
        "by_model": by_model,
    }


def get_artifact_text(session_id):
    """This pane's current cover_letter/resume draft text, or '' if none yet."""
    artifact = db.get_artifact(session_id)
    return artifact["content"] if artifact else ""


def save_artifact(session_id, job_id, artifact_type, content):
    db.upsert_artifact(session_id, job_id, artifact_type, content, _now())


def get_qa_list(session_id):
    return db.list_qa_artifacts(session_id)


def add_qa(session_id, job_id, question, answer):
    return db.insert_qa_artifact(session_id, job_id, question, answer, _now())


def update_qa(qa_id, answer):
    db.update_qa_artifact(qa_id, answer, _now())


# ---- preferred cover letter -------------------------------------------------
# One cover_letter chat_session per job can be marked "ready to send" - see db.py's
# preferred_cover_letters. Its content is read live from that session's own artifact, so a
# later revision to the same session (a Tailor-page pane edit, or chat feedback after "show
# me the preferred letter") stays the preferred version automatically - no separate refresh.

def mark_preferred_cover_letter(job_id, session_id):
    db.set_preferred_cover_letter(job_id, session_id, _now())


def unmark_preferred_cover_letter(job_id):
    db.clear_preferred_cover_letter(job_id)


def get_preferred_cover_letter(job_id):
    """{"session_id", "model", "content", "marked_at"} for this job's cover letter marked
    ready to send, or None if none is marked."""
    pointer = db.get_preferred_cover_letter(job_id)
    if pointer is None:
        return None
    chat_session = db.get_chat_session(pointer["session_id"])
    return {
        "session_id": pointer["session_id"],
        "model": chat_session["model"] if chat_session else None,
        "content": get_artifact_text(pointer["session_id"]),
        "marked_at": pointer["marked_at"],
    }


def get_preferences():
    """Return {category: text} for general/cover_letter/resume/qa."""
    return {cat: row["text"] for cat, row in db.fetch_preferences().items()}


def get_preferences_full():
    """Return {category: {text, previous_text, updated_at}}, for the preferences page."""
    return db.fetch_preferences()


def save_preference(category, text):
    db.update_preference(category, text, _now())


# ---- global assistant chat --------------------------------------------------
# One continuous thread, not scoped to a job or a "pane" - see db.py's assistant_messages
# docstring. src/assistant.py owns the turn logic; this is just the persistence wrapper.

def add_assistant_message(role, content, **kwargs):
    return db.insert_assistant_message(role, content, _now(), **kwargs)


def get_assistant_messages(limit=200):
    return db.fetch_assistant_messages(limit)


def get_assistant_message(message_id):
    return db.get_assistant_message(message_id)


def get_active_job_id():
    """The most recently discussed job in this thread, across every kind of turn - the
    fallback when a new message doesn't name a job (see src/assistant.py.resolve_job)."""
    return db.fetch_last_assistant_job_id()


ASSISTANT_MODEL_SETTING = "assistant_model"


def get_assistant_model(default):
    return db.get_setting(ASSISTANT_MODEL_SETTING, default)


def set_assistant_model(model):
    db.set_setting(ASSISTANT_MODEL_SETTING, model)


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


# ---- sourcing components (SerpAPI, RemoteOK, ATS boards - see src/components/) --------

def get_component_config(component_id, default_config):
    """This component's explicitly saved settings, or default_config (built live from the
    current profile) if nothing's been saved yet - never persisted here. Persisting the
    default on first read would freeze it as of whatever moment the page was first opened,
    so later profile edits (roles, eligible countries, ...) would stop reaching it; only
    save_component_config (the "Save settings" action) should ever write a row."""
    config = db.get_component_config(component_id)
    return config if config is not None else default_config


def save_component_config(component_id, config):
    db.save_component_config(component_id, config)


def start_run(component_id, mode):
    return db.insert_run(component_id, _now(), mode)


def finish_run(run_id, status, fetched_count, error_message=None, filtered_count=0, filtered_reasons=None):
    db.finish_run(run_id, _now(), status, fetched_count, error_message, filtered_count, filtered_reasons)


def save_run_result(run_id, listing):
    db.insert_run_result(run_id, listing)


def save_fetch_results(run_id, listings, error_message=None):
    """The fetch stage's only write: stage every listing a component's run() returned as its
    own row (status "kept" by default - see src/db.py's component_run_results), so a
    component's own page can show and add-to-dashboard all of them immediately, with no
    filtering involved. Also keeps the raw JSON on component_runs for the audit trail
    (db.fetch_raw_results). status "fetched" means "awaiting an explicit filter pass" (see
    apply_run_filters) or "error" (nothing came back and the fetch itself failed) -
    decoupled from filtering on purpose; a component's run() only needs to reach this far."""
    status = "error" if error_message and not listings else "fetched"
    db.save_raw_results(run_id, listings, status, error_message)
    for listing in listings:
        save_run_result(run_id, listing)


def apply_run_filters(run_id):
    """The filter, dedupe & save stage: run this run's already-staged results (see
    save_fetch_results) through the hard-preference gate + dedup (src/filters.py). Whatever
    doesn't survive flips to status "filtered" with why, staged for manual review ("add
    anyway" - see get_staged_results_for_review); whatever survives is saved straight to the
    jobs table - it already cleared every gate, so there's nothing left to review. Explicit,
    separate from fetching, and run from the Filter & dedupe tool (not a component's own
    page - see src/components/README.md). No-op (returns 0) if this run has nothing pending
    (already filtered, or never fetched).

    Returns how many results were actually saved to the jobs table - the single source of
    truth for "added" counts, used by both the tool page and src/workflows.py's
    run_job_search_rerank so neither has to re-implement the save step."""
    run = db.fetch_run(run_id)
    if run is None or run["status"] != "fetched":
        return 0

    rows = db.fetch_run_results(run_id)
    kept, dropped = filters.filter_and_dedupe(rows, profile, known_urls=get_known_urls(exclude_run_id=run_id))
    for row, reason in dropped:
        db.mark_run_result_filtered(row["id"], reason)
    saved_count = 0
    for row in kept:
        try:
            db.insert_job({**row, "origin": run["component_id"]})
            saved_count += 1
        except RuntimeError as e:
            # Cleared every gate but couldn't actually be inserted (e.g. two results in this
            # batch share a blank url, or a race with something else adding the same url) -
            # fall back to staging it for manual review instead of losing it.
            db.mark_run_result_filtered(row["id"], str(e))
    if saved_count:
        reload_jobs()

    finish_run(run_id, "error" if run["error_message"] else "ok", run["fetched_count"],
               run["error_message"], filtered_count=len(dropped), filtered_reasons=filters.summarize_drops(dropped))
    db.clear_raw_results(run_id)  # display-only copy, superseded by the staged rows above
    return saved_count


def get_known_urls(exclude_run_id=None):
    """Every url already on the dashboard or already kept (passed filtering) from a
    previous run - one query per run instead of one per listing. Used by src/filters.py's
    cross-run dedup when a run is filtered. exclude_run_id leaves out the run being
    filtered itself, so its own (not-yet-reviewed) rows never count as "already known"."""
    return {j["url"] for j in jobs} | db.fetch_staged_urls(exclude_run_id)


def get_component_runs(component_id):
    return db.fetch_runs(component_id)


def get_all_component_runs():
    return db.fetch_all_runs()


def get_run_modes_for_urls(urls):
    return db.fetch_run_modes_for_urls(urls)


def get_run(run_id):
    return db.fetch_run(run_id)


def get_latest_run(component_id):
    runs = db.fetch_runs(component_id, limit=1)
    return runs[0] if runs else None


def get_run_results(run_id):
    return db.fetch_run_results(run_id)


def get_run_result(result_id):
    return db.fetch_run_result(result_id)


def get_staged_results_for_review(limit=200):
    """Every result the filter/dedupe gate dropped and that isn't already on the dashboard
    some other way, most recent first, across every component and run - what the Filter,
    dedupe & save tool page shows for manual override ("add anyway" - see
    /tools/filter_dedupe). Results that survived the gate are saved automatically
    (apply_run_filters) and never appear here."""
    known = {j["url"] for j in jobs}
    return [r for r in db.fetch_recent_filtered_results(limit) if r["url"] not in known]


def add_run_result_to_dashboard(result_id, component_id):
    """Insert one previewed result into the real jobs table, tagged with which component
    found it. Raises RuntimeError (from db.insert_job) if that URL is already on the board."""
    result = db.fetch_run_result(result_id)
    if result is None:
        raise RuntimeError("That result no longer exists.")
    fields = {**result, "origin": component_id}
    job_id = db.insert_job(fields)
    reload_jobs()
    return job_id


# ---- fit scoring (see src/scanner.py) --------------------------------------

def start_scoring_run(mode, model):
    return db.insert_scoring_run(_now(), mode, model)


def finish_scoring_run(run_id, status, scored_count, error_message=None):
    db.finish_scoring_run(run_id, _now(), status, scored_count, error_message)


def save_scoring_result(run_id, job_id, score, summary):
    db.insert_scoring_result(run_id, job_id, score, summary)


def get_scoring_runs():
    return db.fetch_scoring_runs()


def get_scoring_run(run_id):
    return db.fetch_scoring_run(run_id)


def get_latest_scoring_run():
    runs = db.fetch_scoring_runs(limit=1)
    return runs[0] if runs else None


def get_scoring_run_results(run_id):
    return db.fetch_scoring_run_results(run_id)


# ---- preference learning (see src/learning.py) -----------------------------

def start_preference_learning_run(scope_job_id, mode, model):
    return db.insert_preference_learning_run(_now(), scope_job_id, mode, model)


def finish_preference_learning_run(run_id, status, processed_count, updated_count, error_message=None):
    db.finish_preference_learning_run(run_id, _now(), status, processed_count, updated_count, error_message)


def get_unchecked_feedback_messages(scope_job_id=None):
    return db.fetch_unchecked_feedback_messages(scope_job_id)


def get_artifact_text_before(session_id, message_id):
    """The artifact/answer text as it stood right before message_id was sent - i.e. whether
    there was already something to react to. None if this was the first message in the
    session. Mirrors the pre-turn `current_text` gate in app.py's _run_pane_turn (used there
    to decide whether classify_turn/revise_preferences run at all)."""
    preceding = db.get_preceding_chat_message(session_id, message_id, "assistant")
    return preceding["artifact_text"] if preceding else None


def get_artifact_text_after(session_id, message_id):
    """The artifact/answer text that resulted from message_id's own turn - i.e. what the
    feedback actually produced. None if this message's turn never got a resolving assistant
    reply (a crashed/incomplete turn). Mirrors the post-turn `artifact_text` app.py's
    _run_pane_turn actually passes to revise_preferences (the result, not the stale draft
    the feedback was reacting to)."""
    following = db.get_following_chat_message(session_id, message_id, "assistant")
    return following["artifact_text"] if following else None


def mark_message_preference_checked(message_id):
    db.set_message_preference_checked(message_id, _now())


def save_preference_learning_result(run_id, job_id, category, message_excerpt):
    db.insert_preference_learning_result(run_id, job_id, category, message_excerpt)


def get_preference_learning_runs():
    return db.fetch_preference_learning_runs()


def get_preference_learning_run(run_id):
    return db.fetch_preference_learning_run(run_id)


def get_latest_preference_learning_run():
    runs = db.fetch_preference_learning_runs(limit=1)
    return runs[0] if runs else None


def get_preference_learning_run_results(run_id):
    return db.fetch_preference_learning_run_results(run_id)


UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_load_user()
reload_jobs()
