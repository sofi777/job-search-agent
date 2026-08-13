# Dream Job Landing

A proof-of-concept web app: upload your resume, tell it your preferences, and get a ranked,
personal job board, with placeholder AI for suggestions, match scoring, cover letters, and
tailored resumes.

## Run it locally

```bash
cd webapp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:8014. Log in with anything (single demo user, no real auth), then
walk through the onboarding wizard. A resume upload is required the first time.

## Project structure

```
webapp/
  app.py              # Flask routes, entry point
  src/
    db.py               # SQLite persistence, the only module that touches sqlite3.
                         #   Creates tables on import, parameterized queries, one
                         #   transaction per write
    store.py           # in-memory view backed by db.py: user profile, jobs
                         #   (catalog + progress merged), priority weights
    ai.py               # placeholder AI: role/location suggestions, match scoring,
                         #   cover letter + resume generation (swap these for real
                         #   LLM calls later without touching callers)
    scanner.py          # run_scan(): re-scores jobs against the profile; called
                         #   on-demand today, wire to a scheduler later unchanged
  data/
    jobs.json           # 10 sample job postings (catalog only), edit this file
                         #   directly, then click "Reload sample data" in the
                         #   dashboard to pick it up
    app.db               # SQLite database: user profile + per-job progress
                         #   (status, comments, generated docs). Created
                         #   automatically on first run; gitignored
    uploads/             # uploaded resume files (gitignored)
  templates/            # Jinja2 HTML templates
  static/style.css       # design tokens + component styles, ported from the Claude
                         #   design prototype in ../Prototype/
```

## Implemented features

- **Login**: single hardcoded demo user, session-based, no real auth (POC only)
- **Onboarding wizard**: resume upload, suggested roles (editable), location,
  commute radius, remote preference, eligible/remote countries, industries + free-text
  preferences, minimum salary/currency, then runs an immediate scan
- **Dashboard**: sortable, filterable job table (employer, role, posted date, match %,
  status, notes). Search by company/title, filter by status, sort any column. Default
  sort: newest posted first.
- **Job detail page**: full description, apply link to the source posting, mark
  applied/not applied, generate cover letter / tailored resume
- **Status tracking**: new to viewed (automatic on open) to applied, plus a manual
  status dropdown on every dashboard row
- **Comments**: free-text notes per job, saved inline
- **Cover letter / tailored resume generation**: placeholder text, editable, with
  a "Regenerate" action
- **Editable ranking**: the weights behind the match score (role/location/salary/
  industry fit) are visible and editable at `/priority`
- **On-demand scan**: "Run scan now" re-scores all jobs against the current profile
  and weights. Daily automation is intentionally not wired up yet (see below).

## What's a placeholder (by design)

Per the project brief, no LLM is integrated yet. `src/ai.py` has generic,
deterministic stand-ins for every AI-shaped function (resume parsing, role/location
suggestions, match scoring, cover letter and resume generation). Each function's
docstring says what a real implementation should do; swapping the body for a real
model call requires no changes to any caller.

Similarly, `src/scanner.run_scan()` is the single entry point for refreshing job
matches. It runs on-demand only for now; daily automation (cron, APScheduler, a
GitHub Action, etc.) can call this same function on a timer without any other
changes.

## Persistence

User profile/settings and per-job progress (status, comments, generated cover
letters/résumés) persist in `data/app.db` (SQLite, via Python's built-in `sqlite3`).
`src/db.py` creates the schema automatically on first run and is the only module
that writes SQL: every write runs in its own transaction with parameterized
queries. `src/store.py` keeps an in-memory copy for cheap reads and pushes every
mutation back through `db.py`, so routes never touch SQL directly.

The job catalog (company, title, description, ...) intentionally stays in
`data/jobs.json`, not the database. It's sample data meant to be hand-edited on
disk, not user state.

## Notes

- Single hardcoded demo user: the `users` table has one row. Multi-user support
  would mean adding real auth and keying everything off the logged-in user's id
  instead of the module-level `store.user_id`.
- `app.secret_key` is a hardcoded dev value, fine for local POC use, not for any real
  deployment.
