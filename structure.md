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
- **`src/store.py`** - in-memory view backed by `db.py`: user profile, jobs (DB rows +
  this user's progress and latest fit score merged in - see `reload_jobs()`), priority
  weights (unused since fit scoring went LLM-based, see `src/scanner.py` - kept as dead
  data, not wired to anything). `JOB_STATUSES` is the full set of job statuses (dashboard
  status dropdown, `app.py`'s `/jobs/<id>/status`); `TERMINAL_STATUSES` (applied/rejected/
  irrelevant) is the subset `scanner.run_scan(pending_only=True)` skips re-scoring. Every
  mutation goes through a `save_*()`/`update_*()` call here. Also owns the flat-file logs
  (`data/results.json` for ratings) that don't need a full table.
- **`src/ai.py`** - `suggest_roles`/`suggest_home_address` are placeholders. `score_job()`
  and `extract_job_posting()` are real LLM calls via `src/agents.py`.
- **`src/agents.py`** - the one module that talks to an LLM (OpenRouter transport:
  `send_message`/`send_chat`), plus the tailoring chat + cross-job preference
  learning (`run_tailor_turn`, `revise_preferences`) and the floating assistant's own
  turn logic (`answer_assistant_message` - plain chat, grounded in profile/preferences/
  a compact jobs summary; `route_assistant_turn` - classifies one assistant turn into a
  live workflow id, `"cover_letter"`, or `"chat"`, same JSON-in/JSON-out shape as
  `classify_turn`, defaults to `"chat"` on any failure since misrouting into a paid
  workflow/unwanted draft is the unsafe direction). Every real LLM call in the app goes
  through `send_chat` - see CLAUDE.md's LLM usage tracking rule.
- **`src/tailoring.py`** - `run_turn(chat_session, job, display_message, preferences)`:
  one full tailoring-chat turn (classify -> retrieve -> generate -> persist -> learn).
  Extracted from `app.py`'s `_run_pane_turn` so it's shared verbatim by the per-job
  Tailor pane route and `src/assistant.py`'s chat-driven cover-letter drafting - one
  code path, so feedback given from either surface behaves identically.
- **`src/assistant.py`** - the floating assistant's orchestrator: one continuous global
  thread (not scoped to a job or a "pane", see `assistant_messages` below).
  `handle_turn(message, model)` calls `agents.route_assistant_turn` then dispatches to a
  live workflow (`workflows.WORKFLOWS[action]["run"]`, reply composed deterministically
  in Python from the summary, no second LLM call), `"cover_letter"` (`resolve_job` +
  `get_or_create_cover_letter_session` + `tailoring.run_turn`, mirrored into this thread
  with `linked_chat_message_id` pointing at the real `chat_messages` row so the widget's
  rating buttons hit the existing `/messages/<id>/rate` route), or plain chat
  (`agents.answer_assistant_message`). `resolve_job(job_query, jobs)` is a deterministic
  (no LLM) match against `store.jobs` - rank phrases ("top job", "#2") or a title/company
  substring match; ambiguous/no match returns candidates instead of guessing. Job
  continuity: if a turn doesn't name a job, falls back to `store.get_active_job_id()`
  (the most recent job any prior turn discussed, skipping job-agnostic turns like a
  workflow run). `get_or_create_cover_letter_session` retargets an existing session's
  model in place (`store.set_session_model`, not the fork-aware
  `store.switch_session_model` built for Tailor-page compare panes) so switching models
  in the widget never forks/resets the conversation.
- **`src/prompts/`** - tailoring chat prompt templates, one `.txt` file per artifact
  type, the preference-revision prompt, and the assistant's own (`assistant_chat.txt`,
  `route_assistant_turn.txt`).
- **`src/files.py`** - `extract_text()`: .pdf/.docx/.txt/.md -> plain text.
- **`src/rag.py`** - chunking + sentence-transformers embeddings + Chroma retrieval
  for the tailoring knowledge base.
- **`src/scanner.py`** - `run_scan(mode, model, pending_only=False)`: the single entry point
  for fit-scoring jobs already in the DB (soft signals only - resume/story-bank skill fit and
  industries/free-text alignment via `ai.score_job`; role/title, location, remoteness,
  salary are already hard-filtered before a job reaches the jobs table, see
  `src/filters.py`, so scoring never re-checks them). Called from onboarding completion,
  the dashboard's "Run scan" button, data reload (all via the `mode="live"` default), and
  the Score fit tool page (`/tools/score_fit`, explicit mode/model/pending_only from the run
  form). `pending_only` skips jobs in `store.TERMINAL_STATUSES` (applied/rejected/irrelevant)
  - the user has already decided on those, so nothing left to re-judge; skipped jobs keep
  their last score (`db.fetch_latest_scores` reads each job's most recent live score, not one
  run's full result set, so a partial rerun can't blank out jobs it didn't touch).
  Every call logs a `scoring_runs` row regardless of caller, and never raises - a failure
  mid-run is recorded on the run (status `"error"`) and returned via the run id, not
  thrown. `mode="test"` still makes a real LLM call per job (not a fixture, unlike
  `src/components/`'s test mode - a fake score wouldn't exercise the prompt/parsing path)
  but forces the model to `agents.DEFAULT_MODEL` (free) regardless of what's requested,
  and never touches the dashboard.
- **`src/filters.py`** - `filter_and_dedupe()`: hard-preference gate (role/title,
  location, remoteness, work eligibility, salary floor - all read live from the profile,
  no separate override copy) + dedup (within a fetch batch, against the jobs table, and
  against anything already staged from a prior run). Pure, no store/db import - called by
  `store.apply_run_filters()`, the filter stage of the fetch/filter pipeline (see below),
  so what a user reviews is already scoped to their hard preferences with duplicates
  dropped. No scoring here - that's `scanner.py`'s job, runs later, only after a job is
  actually saved.
- **`src/tools.py`** - `TOOLS` registry (name/description/status/blocked_reason) for the
  six "Tools" cards on `/components` and their `/tools/<id>` admin pages (see
  `templates/tool_detail.html`, `app.py`'s `tool_detail()`). Display metadata only - each
  live tool's actual data is assembled in `app.py`, not here: `filter_dedupe` (active
  profile values, combined run log, staged-for-review results, added-jobs log; merged
  with the old separate "save to database" tool, see `webapp/` section), `score_fit`
  (model + mode picker, latest run summary/results, full run history - backed by
  `scoring_runs`/`scoring_run_results`, see `src/db.py` and `src/scanner.py`),
  `tailored_generation` (job picker -> opens that job's existing `/jobs/<id>/tailor`
  page - no new generation logic, just a dashboard-wide entry point into
  `agents.run_tailor_turn`), and `preference_learning` (own Run form - scope/model/mode -
  plus run log/results, backed by `src/learning.py`; see below).
- **`src/learning.py`** - `run_learning(scope_job_id, mode, model)`: an explicit, on-demand
  version of the preference learning `app.py`'s `_run_pane_turn` already does live per
  turn - walks tailoring-chat feedback messages not yet checked (`chat_messages.role =
  'user'` with `preference_checked_at IS NULL` - see `src/db.py` migration), across every
  job or scoped to one, and for each: gates on whether there was already a draft to react
  to (`store.get_artifact_text_before`, mirrors the live pre-turn `current_text` check),
  then `agents.classify_turn` + `agents.revise_preferences` against the resulting draft
  (`store.get_artifact_text_after` - the post-turn artifact, same as what the live hook
  actually passes, not the pre-turn one). `mode="test"` makes real LLM calls and logs a
  preview to `preference_learning_run_results`, but never calls `store.save_preference`
  and never marks a message checked, so it's safely repeatable. `mode="live"` does both -
  every message it looked at gets marked checked (whether or not it changed anything), so
  a repeat run only ever processes feedback that arrived since - same incremental contract
  the live per-turn hook already keeps (it marks its own message checked too, right after
  its `check_preferences` block - see `_run_pane_turn`). Run log: `preference_learning_runs`
  / `preference_learning_run_results` (`src/db.py`), same shape as `scoring_runs` /
  `scoring_run_results`.
- **`src/workflows.py`** - `WORKFLOWS` registry (name/description/`uses`/`status`) for
  the `/workflows` cards. `status: "live"` entries carry a `"run"` callable and are what
  `src/assistant.py`'s router can trigger by name; `"pending"` entries are still
  display-only. `run_job_search_rerank(mode)` (the `job_search_rerank` entry's runner):
  for each `src/components/` entry, fetch -> `store.save_fetch_results` ->
  `store.apply_run_filters` -> `store.add_run_result_to_dashboard` on every kept result,
  then `scanner.run_scan(mode)`. Never raises - mirrors `scanner.run_scan`'s contract, a
  failure in one component (or an already-on-the-dashboard race) is recorded/skipped, not
  fatal to the others. `tailor_top_3` stays `"pending"` (no runner) - not yet built.
- **`scripts/`** - quick manual scripts (`test_agents.py`, `show_last_call.py`).
- **`templates/`** - Jinja2 HTML, all extending `base.html`. `base.html` also includes
  `chat_widget.html` on every logged-in page (the floating assistant bubble/panel - see
  `src/assistant.py`), gated on `session.logged_in` so it's absent from `login.html`.
  Pages with file uploads (`onboarding_resume.html`, `profile.html`) mark their `<form>`
  `data-upload-form` and each dropzone `data-field="..."` to opt into `static/upload.js`.
- **`static/style.css`** - design tokens + component styles. `.msg-row`/`.msg-bubble`/
  `.chat-inputbar`/`.send-btn`/`.typing-dot`/`.citation`/`.msg-rating`/`.rate-btn` are
  shared between the per-job Tailor panes (`tailor.html`) and the floating assistant
  widget (`chat_widget.html`) - moved here from `tailor.html`'s own `<style>` once a
  second feature needed them; `.assistant-*` classes are the widget's own (bubble,
  fixed-position panel, artifact preview, model select).
- **`static/upload.js`** - progressive enhancement for `data-upload-form` forms: hides the
  native file input behind a styled button, shows a checkmark once a file is
  staged/on-file, disables the submit button and shows per-file upload progress (via one
  XHR `progress` event, split across files by byte range) instead of a page that looks
  frozen mid-submit, then either follows the server's redirect or swaps in its re-rendered
  HTML (e.g. on a warning) via `document.write`. No build step, no bundler - included with
  a plain `<script src>` in a template's `scripts` block.
- **`static/chat_widget.js`** - the floating assistant's JS (vanilla, `data-*` hooks, same
  convention as `upload.js`). Unlike every other chat surface in the app, turns are
  `fetch()`+JSON against `/assistant/*` (no full-page POST/redirect) since the widget
  persists across whatever page the user is on and can't safely reload it. Lazy-loads
  `GET /assistant/history` on first open rather than being injected into every page
  render. Optimistic user-message echo + typing-dot "thinking" bubble, same trick as
  `tailor.html`'s inline script. Its rating-button listener is scoped to the widget panel
  (not `document`-wide) so it never double-fires alongside `tailor.html`'s identical
  listener when both are present on the same page.
- **`data/`** - all app data, mostly gitignored (see `.gitignore` for what's sample
  vs. user data):
  - `app.db` - SQLite: user profile, jobs, per-job progress, chat_sessions (compare
    panes), chat, artifacts, preferences, documents, chunks, citations, settings,
    scoring_runs/scoring_run_results (fit-scoring run log, see `src/scanner.py`),
    assistant_messages (the floating assistant's single global thread - `job_id`
    nullable, set per-message to whichever job that turn concerned;
    `linked_chat_message_id` points at the real `chat_messages` row when a turn
    drafted/revised a cover letter; current model persisted in `settings` under key
    `"assistant_model"`, not its own column, since it's one durable value not per-message
    state).
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
`/profile`, `/preferences`, `/chunks`, `/results`,
`/usage`. Rating a response: `POST /messages/<id>/rate`.

The floating assistant (every logged-in page, see `src/assistant.py`/
`templates/chat_widget.html`): `POST /assistant/message` (JSON, one turn - `{message}`
-> `{user_message, assistant_message}`), `GET /assistant/history` (`{messages, model}`,
fetched by the widget's JS on first open), `POST /assistant/model` (`{model}`, validated
against `agents.MODEL_OPTIONS`, persisted to `settings`). All JSON, not redirects -
unlike every other chat surface in the app, this one can't safely reload whatever page
it's floating over.

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
distinct, deliberate step against just a `run_id`, never auto-chained, and never triggered
from a component's own page (only from the Filter & dedupe tool - see below):
`store.save_fetch_results(run_id, listings, error)` (fetch stage - every listing a
component's `run()` returns is staged as its own `component_run_results` row immediately,
status `"kept"` by default, addable via "Add to dashboard" right away; a display-only copy
also lands on `component_runs.raw_results_json`; run status `"fetched"`) and
`store.apply_run_filters(run_id)` (filter stage - re-evaluates that run's already-staged
rows through `src/filters.py` and flips whatever doesn't survive to status `"filtered"` +
a `filter_reason` - kept rows are untouched, nothing is removed or re-inserted; clears the
now-redundant raw JSON; run status `"ok"`/`"error"`; no-op unless status is `"fetched"`).
`POST /components/<id>/runs/<run_id>/filter` (`component_run_filter()`) is the filter
stage's route - its "Filter & dedupe now" button lives only on the `/tools/filter_dedupe`
run log (per row, whenever a run is sitting in `"fetched"`), never on a component's own
page: a component's page (`templates/component_detail.html`) shows every fetched listing
- kept or filtered, it doesn't care - with "Add to dashboard" on each, so it stays
filter-agnostic like every other component concern. `component_runs.filtered_count`/
`filtered_reasons` record the aggregate (reason -> count) shown on the run summary and run
history; the per-job verdict - which listing was kept vs. filtered, and why - lives on the
same `component_run_results` row (`status`/`filter_reason` columns), rendered as an
expandable "Per-job verdict" table per run on `/tools/filter_dedupe`
(`templates/tool_detail.html`).
Settings, run history, and run results persist in three tables (`component_settings`,
`component_runs` - `raw_results_json` holds a display-only fetch copy, cleared once
filtered - `component_run_results`, one row per listing with its `status`/`filter_reason`
- `src/db.py`); `users.followed_companies` (JSON list,
same pattern as `roles`) is a profile-wide company watchlist the SerpAPI/ATS pages can
extend directly. A result added to the dashboard gets `origin` set to the component's id
(`serpapi`/`remoteok`/`ats`), not `'custom'`.

`/tools/<id>` (`templates/tool_detail.html`) is each Tools-card's admin page - what it
does, and for the live one, its live state. `filter_dedupe` ("Filter, dedupe & save" -
merged with the old separate "save to database" tool, since filtering a run and then
saving what survives are two steps of one review flow, never done independently) shows:
the hard-preference values currently read from the profile
(`filters.describe_active_filters`); every source run's filter/dedup outcome across all
components with a "Run" button on any still `"fetched"`, and an expandable per-job
kept/filtered breakdown on any already filtered (`store.get_all_component_runs`, each row
enriched with `store.get_run_results(id)` in `app.py`'s `tool_detail()`); staged (kept)
results not yet on the dashboard, each with "Add to dashboard"
(`store.get_staged_results_for_review` - every `component_run_results` row with
`status = "kept"` whose url isn't already in `jobs`); and the saved-jobs log, tagged with
the mode of the run that staged it (`store.get_run_modes_for_urls`). The three pending
tools just show their
`blocked_reason`. `tailored_generation` shows a job `<select>` + Open button that
navigates to `/jobs/<id>/tailor` (JS-built URL, no new route). `preference_learning` has
its own Run form (scope job or "All jobs", model, test/live mode -> `POST
/tools/preference_learning/run` -> `src/learning.run_learning`), this run's
summary/revised-preferences table, full run history, and - unchanged - each preference
category's current text/last-updated (`store.get_preferences_full`) below that.

`/workflows` (`templates/workflows.html`, linked from the Admin menu as "Workflows") is a
dashboard of multi-step flows, one card per `src/workflows.py` entry - description plus a
chip per component/tool it will use, linking to that component's/tool's own page
(`component_detail`/`tool_detail`). `job_search_rerank` now has a real runner
(`workflows.run_job_search_rerank`, triggerable from the floating assistant chat - see
above) but this page itself still has no manual "Run now" button/route; `tailor_top_3`
has neither a runner nor a trigger yet.

`webapp/tests/` - stdlib `unittest`, one file per `src/components/` module, all network
calls mocked (`unittest.mock.patch` on `base.fetch_json`/`urlopen`). Run via `python -m
unittest discover -s tests -p "test_*.py"` from `webapp/`.
