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
- **`src/scanner.py`** - `run_scan()`: re-scores jobs already in the DB against the
  profile (soft signals: industries, free text). Separate from and later than filtering.
- **`src/filters.py`** - `filter_and_dedupe()`: hard-preference gate (role/title,
  location, remoteness, work eligibility, salary floor - all read live from the profile,
  no separate override copy) + dedup (within a fetch batch, against the jobs table, and
  against anything already staged from a prior run). Pure, no store/db import - called by
  `store.apply_run_filters()`, the filter stage of the fetch/filter pipeline (see below),
  so what a user reviews is already scoped to their hard preferences with duplicates
  dropped. No scoring here - that's `scanner.py`'s job, runs later, only after a job is
  actually saved.
- **`src/tools.py`** - `TOOLS` registry (name/description/status/blocked_reason) for the
  four "Tools" cards on `/components` and their `/tools/<id>` admin pages (see
  `templates/tool_detail.html`, `app.py`'s `tool_detail()`). Display metadata only - the
  one live tool's actual data (`filter_dedupe` - active profile values, combined run log,
  staged-for-review results, added-jobs log; merged with the old separate "save to
  database" tool, see `webapp/` section) is assembled in `app.py`, not here.
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
pane only, `POST /jobs/<id>/tailor/<tab>/sessions` adds a pane, `POST
/jobs/<id>/tailor/<tab>/session/<session_id>/remove` hides one - its history can be
resurfaced by re-adding the same model). Settings pages:
`/profile`, `/preferences`, `/priority`, `/chunks`, `/results`,
`/usage`. Rating a response: `POST /messages/<id>/rate`.

`/components` (`templates/components.html`, linked from the Admin menu as "System
Components") is an architecture overview of the planned job-scan agent - Data
Connectors, Agent, Tools, one card per component. SerpAPI/RemoteOK/ATS boards are live
(link to `/components/<id>`); the rest are still just cards tagged "Not connected"/
"Pending". See `~/.claude/plans/inherited-bubbling-honey.md` for the design this
mirrors.

`/components/<id>` (`templates/component_detail.html`) is each live sourcing
component's own page: one settings form (shape differs per component, see
`webapp/src/components/`) with two submit buttons - "Save settings" persists it,
"Run" (via `formaction`) parses the same posted fields straight into a config and runs
with it immediately, without saving - so trying values out never requires saving first,
and Run never silently uses stale saved settings instead of what's on screen. Routes
in `app.py`'s "sourcing components" section; `src/components/__init__.py`'s
`COMPONENTS` registry maps each id to its `default_config(profile)`/`run(config,
test_mode)` functions - see that package's own `README.md` for what each component
does and its API key setup. `store.get_component_config` returns the saved config if
one exists, otherwise `default_config(profile)` computed live on every read (never
persisted) - so an unsaved page keeps tracking profile edits (roles, eligible
countries, ...) instead of freezing as of whenever it was first opened; only "Save
settings" writes a row, and once saved a config stops tracking the profile.

Fetching and filtering are two decoupled stages - a component's own `run()` only ever
fetches (it works off its own query params - role terms, location, date posted, ... - not
the profile's hard preferences, and never touches the jobs table), filtering is a
distinct, deliberate step against just a `run_id`, never auto-chained:
`store.save_fetch_results(run_id, listings, error)` (fetch stage - a component's raw
`run()` output goes straight onto `component_runs.raw_results_json`, status `"fetched"`)
and `store.apply_run_filters(run_id)` (filter stage - loads that raw JSON, runs it through
`src/filters.py`, stages what survives into `component_run_results`, clears the raw JSON,
status `"ok"`/`"error"`; no-op unless status is `"fetched"`). `POST
/components/<id>/runs/<run_id>/filter` (`component_run_filter()`) is the filter stage's
route - a "Filter & dedupe now" button surfaces on the run summary (and, per row, on the
`/tools/filter_dedupe` run log) whenever a run is sitting in `"fetched"` (raw fetched, not
yet filtered). `component_runs.filtered_count`/`filtered_reasons` record what got dropped
and why, shown on the run summary and run history.
Settings, run history, and run results persist in three tables (`component_settings`,
`component_runs` - `raw_results_json` holds the pending-filter payload, cleared once
filtered - `component_run_results` - `src/db.py`); `users.followed_companies` (JSON list,
same pattern as `roles`) is a profile-wide company watchlist the SerpAPI/ATS pages can
extend directly. A result added to the dashboard gets `origin` set to the component's id
(`serpapi`/`remoteok`/`ats`), not `'custom'`.

`/tools/<id>` (`templates/tool_detail.html`) is each Tools-card's admin page - what it
does, and for the live one, its live state. `filter_dedupe` ("Filter, dedupe & save" -
merged with the old separate "save to database" tool, since filtering a run and then
saving what survives are two steps of one review flow, never done independently) shows:
the hard-preference values currently read from the profile
(`filters.describe_active_filters`); every source run's filter/dedup outcome across all
components with a "Run" button on any still `"fetched"` (`store.get_all_component_runs`);
staged results not yet on the dashboard, each with "Add to dashboard"
(`store.get_staged_results_for_review` - every `component_run_results` row whose url isn't
already in `jobs`); and the saved-jobs log, tagged with the mode of the run that staged it
(`store.get_run_modes_for_urls`). The three pending tools just show their
`blocked_reason`.

`webapp/tests/` - stdlib `unittest`, one file per `src/components/` module, all network
calls mocked (`unittest.mock.patch` on `base.fetch_json`/`urlopen`). Run via `python -m
unittest discover -s tests -p "test_*.py"` from `webapp/`.
