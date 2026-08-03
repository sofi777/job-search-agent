"""SQLite persistence — the only module that touches sqlite3 directly.

Two tables: `users` (profile/onboarding answers + settings) and
`job_progress` (per-user, per-job status/comments/generated docs). The job
catalog itself (company, title, description, ...) stays in data/jobs.json —
that's sample data meant to be hand-edited on disk, not user state.

Every write runs inside db_transaction(), which commits on success and
rolls back on any exception, and every query is parameterized — no
string-built SQL.
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

# Seed progress for the sample jobs, so the demo dashboard isn't all "New" on first run.
SEED_PROGRESS = {
    3: {"status": "viewed", "comments": "Recruiter reached out Tuesday"},
    5: {"status": "applied", "comments": "Applied with tailored letter — waiting to hear back"},
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
                cover_letter TEXT,
                tailored_resume TEXT,
                PRIMARY KEY (user_id, job_id)
            )
        """)


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
    """Return {job_id: {status, comments, cover_letter, tailored_resume}} for a user."""
    with db_transaction() as conn:
        rows = conn.execute(
            "SELECT job_id, status, comments, cover_letter, tailored_resume FROM job_progress WHERE user_id = ?",
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


def update_progress(user_id, job_id, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{key} = ?" for key in fields)
    with db_transaction() as conn:
        conn.execute(
            f"UPDATE job_progress SET {set_clause} WHERE user_id = ? AND job_id = ?",
            (*fields.values(), user_id, job_id),
        )


init_db()
