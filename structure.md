# Structure

Kept concise on purpose - update this as you learn more about the code, alongside
`CLAUDE.md` whenever the structure itself actually changes (see CLAUDE.md's Rules).

## Repo root

- **`job_agent.py`** - standalone daily script: pulls listings (SerpAPI/Google Jobs,
  RSS, direct ATS APIs), scores fit via the Anthropic API against a hardcoded profile,
  pushes matches to Notion. Scheduled via `.github/workflows/job-agent.yml` (currently
  disabled).
- **`webapp/`** - Dream Job Landing, the Flask proof-of-concept app. All active
  development happens here; see below.
- **`openspec/`** - OpenSpec change history for the webapp.
- **`Prototype/`** - static design prototype `webapp/static/style.css` was ported from.

## `webapp/`

- **`app.py`** - Flask routes, entry point. Routes call into `store.py`; never touch
  SQL or the filesystem directly.
- **`src/db.py`** - SQLite persistence, the only module that touches `sqlite3`.
  Creates tables on import, parameterized queries, one transaction per write.
- **`src/store.py`** - in-memory view backed by `db.py`: user profile, jobs (DB rows
  + this user's progress merged in), priority weights. Every mutation goes through a
  `save_*()`/`update_*()` call here. Also owns the flat-file logs
  (`data/results.json` for ratings) that don't need a full table.
- **`src/ai.py`** - placeholder AI (role/location suggestions, match scoring) plus
  `extract_job_posting()`, a real LLM call via `src/agents.py`.
- **`src/agents.py`** - the one module that talks to an LLM (OpenRouter transport:
  `send_message`/`send_chat`), plus the tailoring chat + cross-job preference
  learning (`run_tailor_turn`, `revise_preferences`). Every real LLM call in the app
  goes through `send_chat` - see CLAUDE.md's LLM usage tracking rule.
- **`src/prompts/`** - tailoring chat prompt templates, one `.txt` file per artifact
  type, plus the preference-revision prompt.
- **`src/files.py`** - `extract_text()`: .pdf/.docx/.txt/.md -> plain text.
- **`src/rag.py`** - chunking + sentence-transformers embeddings + Chroma retrieval
  for the tailoring knowledge base.
- **`src/scanner.py`** - `run_scan()`: re-scores jobs against the profile.
- **`scripts/`** - quick manual scripts (`test_agents.py`, `show_last_call.py`).
- **`templates/`** - Jinja2 HTML, all extending `base.html`.
- **`static/style.css`** - design tokens + component styles.
- **`data/`** - all app data, mostly gitignored (see `.gitignore` for what's sample
  vs. user data):
  - `app.db` - SQLite: user profile, jobs, per-job progress, chat_sessions (compare
    panes), chat, artifacts, preferences, documents, chunks, citations, settings.
  - `jobs.json` - sample job catalog, seeds `app.db` on first run.
  - `chroma/` - vector store for RAG chunk embeddings.
  - `uploads/` - uploaded resume files.
  - `results.json` - one entry per rated chatbot response (see `/results`).
  - `usage.json` - one entry per LLM call: tokens, estimated cost, model, provider
    (see `/usage` and CLAUDE.md's LLM usage tracking rule).
  - `last_llm_call.json` - debug-only, overwritten every call, not read by the app.

## Key routes (`app.py`)

`/onboarding/<step>` (wizard) -> `/dashboard` -> `/jobs/<id>` -> `/jobs/<id>/tailor/<tab>`
(any number of compare panes, each fully self-contained - own chat + artifact + send
button; `POST /jobs/<id>/tailor/<tab>/session/<session_id>/message` sends to one
pane only, `POST /jobs/<id>/tailor/<tab>/sessions` adds a pane). Settings pages:
`/profile`, `/preferences`, `/priority`, `/chunks`, `/results`,
`/usage`. Rating a response: `POST /messages/<id>/rate`.
