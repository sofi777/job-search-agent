"""SQLite persistence. The only module that touches sqlite3 directly.

Tables: `users` (profile/onboarding answers + settings), `jobs` (every job
posting, sample or user-added), `job_progress` (per-user, per-job
status/comments), `chat_messages` (per-job, per-tab tailoring chat threads),
`artifacts` (generated cover letters/resumes/Q&A answers), `preferences`
(learned writing-style preferences, one row per category: general,
cover_letter, resume, qa), `documents` (extracted text from uploaded
resume/cover-letter-sample/story-bank/chat-attachment files; job_id NULL means
profile-wide, reused across every job), `chunks` (documents split for RAG,
embeddings live in Chroma at data/chroma/ - see src/rag.py), `citations`
(which chunks backed a given assistant reply, for the clickable "[Source N]"
UI), and `settings` (small key/value config, currently just chunk_size_tokens).

`jobs` is the single source of truth for postings, with a UNIQUE constraint
on `url` so the same posting can never be added twice, however it got there.
`origin` marks how a row got in: 'sample' (from data/jobs.json) or 'custom'
(added via the "Add Job Posting" popup). data/jobs.json is only read to seed
`jobs` on first run and to refresh 'sample' rows when the user clicks
"Reload sample data" (see store.reload_sample_jobs()); it's never merged in
at read time.

Every write runs inside db_transaction(), which commits on success and
rolls back on any exception, and every query is parameterized (no
string-built SQL).
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

DEMO_USER_EMAIL = "jamie@example.com"
DEMO_USER_NAME = "Jamie Doe"
DEFAULT_ROLES = ["Senior Product Manager", "Product Lead", "Group Product Manager"]
DEFAULT_PRIORITY_WEIGHTS = {"role_match": 40, "location_fit": 30, "salary_fit": 20, "industry_fit": 10}
PREFERENCE_CATEGORIES = ["general", "cover_letter", "resume", "qa"]

# Seed progress for the sample jobs, so the demo dashboard isn't all "New" on first run.
SEED_PROGRESS = {
    3: {"status": "viewed", "comments": "Recruiter reached out Tuesday"},
    5: {"status": "applied", "comments": "Applied with tailored letter, waiting to hear back"},
    7: {"status": "viewed", "comments": ""},
    10: {"status": "viewed", "comments": "Job looks solid but comp band unclear"},
}

_JSON_FIELDS = {"roles", "remote_countries", "eligible_countries", "industries", "priority_weights"}


@contextmanager
def db_transaction():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                resume_filename TEXT,
                roles TEXT NOT NULL DEFAULT '[]',
                home_address TEXT NOT NULL DEFAULT '',
                commute_miles INTEGER NOT NULL DEFAULT 25,
                remote_ok INTEGER NOT NULL DEFAULT 1,
                remote_countries TEXT NOT NULL DEFAULT '[]',
                eligible_countries TEXT NOT NULL DEFAULT '[]',
                industries TEXT NOT NULL DEFAULT '[]',
                industries_text TEXT NOT NULL DEFAULT '',
                min_salary INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                onboarding_complete INTEGER NOT NULL DEFAULT 0,
                priority_weights TEXT NOT NULL DEFAULT '{}',
                last_scan TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_progress (
                user_id INTEGER NOT NULL REFERENCES users(id),
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                comments TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                remote INTEGER NOT NULL DEFAULT 0,
                posted TEXT NOT NULL,
                salary_min INTEGER NOT NULL DEFAULT 0,
                salary_max INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                url TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'custom'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                type TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                question_text TEXT,
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                category TEXT PRIMARY KEY,
                text TEXT NOT NULL DEFAULT '',
                previous_text TEXT,
                updated_at TEXT
            )
        """)
        for category in PREFERENCE_CATEGORIES:
            conn.execute("INSERT OR IGNORE INTO preferences (category, text) VALUES (?, '')", (category,))

        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id INTEGER,
                type TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                job_id INTEGER,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS citations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_message_id INTEGER NOT NULL,
                source_number INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('chunk_size_tokens', '128')")

        conn.execute("DROP TABLE IF EXISTS custom_jobs")
        # Migration: job_progress.cover_letter/tailored_resume replaced by the artifacts table.
        for column in ("cover_letter", "tailored_resume"):
            try:
                conn.execute(f"ALTER TABLE job_progress DROP COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # already migrated, or column never existed on a fresh DB


def _row_to_user(row):
    user = dict(row)
    for field in _JSON_FIELDS:
        user[field] = json.loads(user[field])
    user["remote_ok"] = bool(user["remote_ok"])
    user["onboarding_complete"] = bool(user["onboarding_complete"])
    return user


def ensure_demo_user():
    """Return the demo user's row as a dict, creating it with defaults on first run."""
    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (DEMO_USER_EMAIL,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO users
                   (email, name, roles, home_address, remote_countries, eligible_countries, priority_weights)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    DEMO_USER_EMAIL, DEMO_USER_NAME, json.dumps(DEFAULT_ROLES), "San Francisco, CA",
                    json.dumps(["United States"]), json.dumps(["United States"]),
                    json.dumps(DEFAULT_PRIORITY_WEIGHTS),
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE email = ?", (DEMO_USER_EMAIL,)).fetchone()
        return _row_to_user(row)


def update_user(user_id, **fields):
    """Update the given columns for a user. List/dict values are JSON-encoded automatically."""
    if not fields:
        return
    values = [json.dumps(v) if k in _JSON_FIELDS else v for k, v in fields.items()]
    set_clause = ", ".join(f"{key} = ?" for key in fields)
    with db_transaction() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (*values, user_id))


def reset_user(user_id):
    """Reset a user's onboarding answers to defaults. Leaves id/email/job_progress untouched."""
    with db_transaction() as conn:
        conn.execute(
            """UPDATE users SET
                 resume_filename = NULL, roles = ?, home_address = ?, commute_miles = 25,
                 remote_ok = 1, remote_countries = ?, eligible_countries = ?, industries = '[]',
                 industries_text = '', min_salary = 0, currency = 'USD', onboarding_complete = 0
               WHERE id = ?""",
            (
                json.dumps(DEFAULT_ROLES), "San Francisco, CA",
                json.dumps(["United States"]), json.dumps(["United States"]), user_id,
            ),
        )


def fetch_progress(user_id):
    """Return {job_id: {status, comments}} for a user."""
    with db_transaction() as conn:
        rows = conn.execute(
            "SELECT job_id, status, comments FROM job_progress WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {row["job_id"]: dict(row) for row in rows}


def ensure_progress_rows(user_id, job_ids):
    """Seed a job_progress row for any job_id not yet tracked for this user."""
    with db_transaction() as conn:
        existing = {r["job_id"] for r in conn.execute(
            "SELECT job_id FROM job_progress WHERE user_id = ?", (user_id,)
        )}
        for job_id in job_ids:
            if job_id in existing:
                continue
            seed = SEED_PROGRESS.get(job_id, {"status": "new", "comments": ""})
            conn.execute(
                "INSERT INTO job_progress (user_id, job_id, status, comments) VALUES (?, ?, ?, ?)",
                (user_id, job_id, seed["status"], seed["comments"]),
            )


def insert_job(fields):
    """Insert a user-added job posting (origin='custom'). Returns its id.

    Raises RuntimeError if a job with this url already exists (sample or
    custom): the UNIQUE constraint on jobs.url is what actually prevents the
    duplicate; this just turns that into a clear message for the caller.
    """
    with db_transaction() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO jobs
                   (company, title, source, location, remote, posted, salary_min, salary_max, currency, url, description, origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'custom')""",
                (
                    fields["company"], fields["title"], fields["source"], fields["location"],
                    int(fields["remote"]), fields["posted"], fields["salary_min"], fields["salary_max"],
                    fields["currency"], fields["url"], fields["description"],
                ),
            )
        except sqlite3.IntegrityError as e:
            raise RuntimeError(f"That job is already on your list: {fields['url']}") from e
        return cur.lastrowid


def upsert_sample_jobs(catalog):
    """Insert/update sample-catalog jobs (origin='sample'), matched by url.

    Used to seed `jobs` on first run and to refresh it when the user edits
    data/jobs.json and clicks "Reload sample data". Never touches 'custom'
    rows. ids are taken from the catalog dicts, so they stay stable across
    reloads and keep matching existing job_progress rows.
    """
    with db_transaction() as conn:
        for job in catalog:
            conn.execute(
                """INSERT INTO jobs
                   (id, company, title, source, location, remote, posted, salary_min, salary_max, currency, url, description, origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sample')
                   ON CONFLICT(url) DO UPDATE SET
                     company=excluded.company, title=excluded.title, source=excluded.source,
                     location=excluded.location, remote=excluded.remote, posted=excluded.posted,
                     salary_min=excluded.salary_min, salary_max=excluded.salary_max,
                     currency=excluded.currency, description=excluded.description""",
                (
                    job["id"], job["company"], job["title"], job["source"], job["location"],
                    int(job["remote"]), job["posted"], job["salary_min"], job["salary_max"],
                    job["currency"], job["url"], job["description"],
                ),
            )


def has_sample_jobs():
    with db_transaction() as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE origin = 'sample' LIMIT 1").fetchone()
    return row is not None


def fetch_jobs():
    """Return every job posting (sample + custom), ordered by id."""
    with db_transaction() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    jobs = []
    for row in rows:
        job = dict(row)
        job["remote"] = bool(job["remote"])
        jobs.append(job)
    return jobs


def update_progress(user_id, job_id, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{key} = ?" for key in fields)
    with db_transaction() as conn:
        conn.execute(
            f"UPDATE job_progress SET {set_clause} WHERE user_id = ? AND job_id = ?",
            (*fields.values(), user_id, job_id),
        )


def fetch_chat_messages(job_id, chat_type):
    """Return this job+tab's chat thread, oldest first. job_id may be None (global thread)."""
    with db_transaction() as conn:
        if job_id is None:
            rows = conn.execute(
                "SELECT id, role, content, model, created_at FROM chat_messages "
                "WHERE job_id IS NULL AND type = ? ORDER BY id",
                (chat_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, role, content, model, created_at FROM chat_messages "
                "WHERE job_id = ? AND type = ? ORDER BY id",
                (job_id, chat_type),
            ).fetchall()
    return [dict(r) for r in rows]


def add_chat_message(job_id, chat_type, role, content, model, created_at):
    with db_transaction() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (job_id, type, role, content, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, chat_type, role, content, model, created_at),
        )
        return cur.lastrowid


def get_artifact(job_id, artifact_type):
    """Return the single cover_letter/resume artifact row for a job, or None."""
    with db_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE job_id = ? AND type = ?", (job_id, artifact_type)
        ).fetchone()
    return dict(row) if row else None


def upsert_artifact(job_id, artifact_type, content, updated_at):
    """Insert or update the single cover_letter/resume artifact for a job, bumping version."""
    existing = get_artifact(job_id, artifact_type)
    with db_transaction() as conn:
        if existing:
            conn.execute(
                "UPDATE artifacts SET content = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (content, updated_at, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO artifacts (job_id, type, content, version, updated_at) VALUES (?, ?, ?, 1, ?)",
                (job_id, artifact_type, content, updated_at),
            )


def list_qa_artifacts(job_id):
    with db_transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE job_id = ? AND type = 'question' ORDER BY id", (job_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def insert_qa_artifact(job_id, question_text, content, updated_at):
    with db_transaction() as conn:
        cur = conn.execute(
            "INSERT INTO artifacts (job_id, type, question_text, content, version, updated_at) "
            "VALUES (?, 'question', ?, ?, 1, ?)",
            (job_id, question_text, content, updated_at),
        )
        return cur.lastrowid


def update_qa_artifact(artifact_id, content, updated_at):
    with db_transaction() as conn:
        conn.execute(
            "UPDATE artifacts SET content = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (content, updated_at, artifact_id),
        )


def fetch_preferences():
    """Return {category: {text, previous_text, updated_at}} for all 4 categories."""
    with db_transaction() as conn:
        rows = conn.execute("SELECT * FROM preferences").fetchall()
    return {row["category"]: dict(row) for row in rows}


def update_preference(category, text, updated_at):
    """Overwrite a preference category's text, keeping the prior value for a one-step diff."""
    current = fetch_preferences().get(category, {}).get("text", "")
    with db_transaction() as conn:
        conn.execute(
            "UPDATE preferences SET previous_text = ?, text = ?, updated_at = ? WHERE category = ?",
            (current, text, updated_at, category),
        )


def upsert_profile_document(user_id, doc_type, filename, content, created_at):
    """Insert/replace a profile-wide (job_id NULL) singleton document: resume, cover_letter_sample, story_bank.

    Re-uploading a given type replaces the previous one, same as the resume upload always has.
    Returns the new row's id (the old row's chunks, if any, are now orphaned - see
    rag.cleanup_orphans()).
    """
    with db_transaction() as conn:
        conn.execute("DELETE FROM documents WHERE user_id = ? AND job_id IS NULL AND type = ?", (user_id, doc_type))
        cur = conn.execute(
            "INSERT INTO documents (user_id, job_id, type, filename, content, created_at) VALUES (?, NULL, ?, ?, ?, ?)",
            (user_id, doc_type, filename, content, created_at),
        )
        return cur.lastrowid


def insert_document(user_id, job_id, doc_type, filename, content, created_at):
    """Insert a new document row. Used for ad-hoc chat attachments - accumulates, no replace."""
    with db_transaction() as conn:
        cur = conn.execute(
            "INSERT INTO documents (user_id, job_id, type, filename, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, job_id, doc_type, filename, content, created_at),
        )
        return cur.lastrowid


def fetch_documents(user_id, job_id):
    """Return every document visible to this job: profile-wide (job_id NULL) plus this job's own."""
    with db_transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE user_id = ? AND (job_id IS NULL OR job_id = ?) ORDER BY id",
            (user_id, job_id),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_profile_documents(user_id):
    with db_transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE user_id = ? AND job_id IS NULL ORDER BY id", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_all_documents(user_id):
    with db_transaction() as conn:
        rows = conn.execute("SELECT * FROM documents WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_document_chunks(document_id):
    with db_transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))


def delete_document(document_id):
    with db_transaction() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def delete_all_chunks():
    with db_transaction() as conn:
        conn.execute("DELETE FROM chunks")


def insert_chunk(document_id, job_id, chunk_index, text, token_count, created_at):
    with db_transaction() as conn:
        cur = conn.execute(
            "INSERT INTO chunks (document_id, job_id, chunk_index, text, token_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (document_id, job_id, chunk_index, text, token_count, created_at),
        )
        return cur.lastrowid


def fetch_chunk(chunk_id):
    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    return dict(row) if row else None


def fetch_chunks_by_ids(chunk_ids):
    if not chunk_ids:
        return {}
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(chunk_ids))
        rows = conn.execute(f"SELECT * FROM chunks WHERE id IN ({placeholders})", chunk_ids).fetchall()
    return {row["id"]: dict(row) for row in rows}


def fetch_all_chunks_with_document_info(user_id):
    """For the /chunks page: every chunk, joined with its source document's filename/type."""
    with db_transaction() as conn:
        rows = conn.execute(
            """SELECT chunks.*, documents.filename, documents.type AS doc_type
               FROM chunks JOIN documents ON chunks.document_id = documents.id
               WHERE documents.user_id = ?
               ORDER BY documents.id, chunks.chunk_index""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def orphaned_chunk_ids():
    """Chunks whose document no longer exists (e.g. after a resume re-upload replaced the old row)."""
    with db_transaction() as conn:
        rows = conn.execute(
            "SELECT chunks.id FROM chunks LEFT JOIN documents ON chunks.document_id = documents.id "
            "WHERE documents.id IS NULL"
        ).fetchall()
    return [r["id"] for r in rows]


def delete_chunks_by_ids(chunk_ids):
    if not chunk_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(chunk_ids))
        conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders})", chunk_ids)


def insert_citation(chat_message_id, source_number, chunk_id, score, created_at):
    with db_transaction() as conn:
        conn.execute(
            "INSERT INTO citations (chat_message_id, source_number, chunk_id, score, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_message_id, source_number, chunk_id, score, created_at),
        )


def fetch_citations_for_messages(message_ids):
    """Return {chat_message_id: [{source_number, chunk_id, score, text, filename}, ...]}."""
    if not message_ids:
        return {}
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(message_ids))
        rows = conn.execute(
            f"""SELECT citations.chat_message_id, citations.source_number, citations.score,
                       chunks.id AS chunk_id, chunks.text, documents.filename
                FROM citations
                JOIN chunks ON citations.chunk_id = chunks.id
                JOIN documents ON chunks.document_id = documents.id
                WHERE citations.chat_message_id IN ({placeholders})
                ORDER BY citations.source_number""",
            message_ids,
        ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["chat_message_id"], []).append(dict(row))
    return result


def get_setting(key, default=None):
    with db_transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db_transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


init_db()
