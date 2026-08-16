# Dream Job Landing

A proof-of-concept web app: upload your resume, tell it your preferences, and get a ranked,
personal job board. Tailor each application (cover letter, resume, Q&A answers) via a
per-job chat, with placeholder AI for suggestions and match scoring.

## Run it locally

```bash
cd webapp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:8014. Log in with anything (single demo user, no real auth), then
walk through the onboarding wizard. A resume upload is required the first time; a past
cover letter and a "story bank" (achievements/STAR stories) are optional there too.

## Project structure

```
webapp/
  app.py              # Flask routes, entry point
  src/
    db.py               # SQLite persistence, the only module that touches sqlite3.
                         #   Creates tables on import, parameterized queries, one
                         #   transaction per write
    store.py           # in-memory view backed by db.py: user profile, jobs
                         #   (from the DB, this user's progress merged in),
                         #   priority weights
    ai.py               # placeholder AI: role/location suggestions, match scoring
                         #   (swap these for real LLM calls later without touching
                         #   callers); extract_job_posting() is real, via agents.py
    agents.py            # OpenRouter transport (send_message/send_chat) plus the
                         #   tailoring chat + cross-job preference learning
                         #   (run_tailor_turn, revise_preferences) - see AI below
    prompts/              # tailoring chat prompt templates, one per artifact type
                         #   plus the preference-revision prompt
    files.py             # extract_text(): .pdf/.docx/.txt/.md -> plain text, used for
                         #   resume/cover-letter-sample/story-bank/chat-attachment uploads
    rag.py               # chunking + sentence-transformers embeddings + Chroma retrieval
                         #   for the tailoring knowledge base (documents only) - see AI below
    scanner.py          # run_scan(): re-scores jobs against the profile; called
                         #   on-demand today, wire to a scheduler later unchanged
  scripts/
    test_agents.py       # quick manual check: calls agents.send_message and prints
  data/
    jobs.json           # 10 sample job postings; seeds the jobs table on first
                         #   run. Edit this file, then click "Reload sample data"
                         #   in the dashboard to upsert your changes
    app.db               # SQLite database: user profile, all jobs (sample +
                         #   user-added), and per-job progress (status, comments,
                         #   generated docs). Created automatically on first run;
                         #   gitignored
    chroma/               # Chroma vector store for RAG chunk embeddings, gitignored;
                         #   rebuilds from app.db's documents table via the /chunks page
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
  applied/not applied, link to "Tailor my application"
- **Status tracking**: new to viewed (automatic on open) to applied, plus a manual
  status dropdown on every dashboard row
- **Comments**: free-text notes per job, saved inline
- **Tailor my application** (`/jobs/<id>/tailor`): one page per job with three tabs -
  Cover Letter, Resume, Q&A - each its own persistent chat thread and generated
  artifact. Quick-generate buttons for the first draft; after that, any chat message
  is feedback that updates the artifact in place. Q&A has no button - paste a
  question directly in chat; the model decides (from context) whether it's a new
  question or feedback on the last answer. Model is selectable per message from a
  dropdown (free + paid OpenRouter options), with a link to browse the full catalog.
- **Knowledge base chunks** (`/chunks`): every chunk the uploaded documents were split
  into, grouped by source file, with token counts. A chunk-size field + "Re-run
  chunking" button re-chunks and re-embeds the entire knowledge base at a new size.
- **Editable ranking**: the weights behind the match score (role/location/salary/
  industry fit) are visible and editable at `/priority`
- **On-demand scan**: "Run scan now" re-scores all jobs against the current profile
  and weights. Daily automation is intentionally not wired up yet (see below).
- **Add Job Posting**: paste a URL on the dashboard and the app fetches the page and
  uses an LLM (via `src/agents.py`) to fill in company, title, location, salary, and
  a description. Added with no match score ("Not yet ranked") until the next scan.
  Duplicate URLs are rejected with a clear message; a job can't be added twice.
  If the site blocks a plain fetch, falls back to a text-extraction proxy
  (r.jina.ai); if the page is still just a bot check or login wall, the model
  reports that clearly instead of adding a garbage entry. Sites with an
  interactive bot challenge (e.g. Cloudflare, some Indeed pages) aren't
  fetchable at all without a real browser, which this app doesn't run.

## What's a placeholder (by design)

`suggest_roles`, `suggest_home_address`, and `score_job` in `src/ai.py` are still
generic, deterministic stand-ins, not real LLM calls. Each function's docstring says
what a real implementation should do; swapping the body for a real model call
requires no changes to any caller. `ai.extract_job_posting()` and the tailoring chat
(`src/agents.py`) are real LLM calls.

Similarly, `src/scanner.run_scan()` is the single entry point for refreshing job
matches. It runs on-demand only for now; daily automation (cron, APScheduler, a
GitHub Action, etc.) can call this same function on a timer without any other
changes.

## AI (OpenRouter)

`src/agents.py` calls an LLM through [OpenRouter](https://openrouter.ai) and returns
its reply. OpenRouter is used instead of calling a provider directly so the model can
be swapped without touching callers: `send_message(message, model=)` for a one-shot
call, `send_chat(messages, model=)` for a full message history. No third-party HTTP
library: it's a plain `urllib.request` call, so `requirements.txt` doesn't grow.
`MODEL_OPTIONS` lists the models offered in the UI dropdown (edit to add/remove);
users can also browse the full catalog via the link next to the dropdown.
`DEFAULT_MODEL` is currently `google/gemma-4-26b-a4b-it:free`.

Free-tier models share a rate-limited pool across all OpenRouter users, so 429s
happen under load. `send_chat()`/`send_message()` retry once, waiting however long
OpenRouter says to (capped at 30s), before raising a readable error. Reasoning
models (e.g. `openai/gpt-oss-20b:free`) can also return `content: null` on longer
creative-writing tasks - they burn their token budget "thinking" and never emit a
final answer; `send_chat()` raises a clear error in that case rather than crashing,
naming the model and suggesting a different one from the dropdown.

**Tailoring chat**: `run_tailor_turn()` builds a system prompt per turn from
`src/prompts/<type>.txt` (job + profile + preferences + current draft/answers),
sends the job's chat history plus the new message, and parses a structured JSON
reply (`{reply, artifact}` for cover_letter/resume; `{reply, action, question,
answer}` for qa - the model decides new-question vs. feedback from context, no
manual toggle needed). `app.py` persists the chat turn and artifact via `store.py`.

**Cross-job preference learning**: after each feedback-driven turn,
`revise_preferences()` makes a second, separate call asking whether the feedback
revealed a durable style preference (vs. being specific to that one document), and
if so returns the revised text for the right category. Preferences are stored as
four plain-text fields (general, cover_letter, resume, qa - general applies
everywhere, a category overrides it on conflict), editable directly at
`/preferences`, which also shows the prior value after an auto-update.

**Knowledge base documents**: come from three places - onboarding uploads (resume,
cover letter sample, story bank - always profile-wide, reused on every job) and files
attached mid-chat via the paperclip button, which are job-scoped by default or
promoted to profile-wide with the "Save for all jobs" checkbox. Profile fields
(target roles, salary, location) and writing-style preferences are NOT part of this -
they're small enough to stay always-injected in full, separately (see above);
chunking/retrieval only applies to these prose documents.

**RAG** (`src/rag.py`): every document is chunked (sentence-packed groups of ~128
tokens, default - see `/chunks` to change it), embedded locally with
`sentence-transformers` (`all-MiniLM-L6-v2`, no OpenRouter cost), and stored in a
Chroma collection at `data/chroma/` (`chunks` table in `app.db` is the source of
truth for chunk text/metadata; Chroma only holds the vectors, keyed by the same
chunk id). Chunking/embedding happens automatically the moment a document is
uploaded or replaced.

On each chat turn, `agents.build_retrieval_query()` embeds the **job title +
description + the user's message** together, not just the message alone - a generic
instruction like "generate the cover letter" carries almost no signal on its own, so
folding in what the job actually needs is what makes retrieval find something
relevant instead of near-random chunks. The top 3 matches (cosine similarity,
filtered in Python to the same job-scope rule as everywhere else: profile-wide docs
everywhere, job-scoped docs only on their own job) go into the prompt as a numbered,
scored source list, replacing the old full-text-dump approach entirely - the model
only ever sees these 3 chunks, never the whole knowledge base.

The model is instructed to cite sources inline as `[Source N]` **in its
conversational reply only, never inside the generated artifact/answer** (a real
cover letter must never contain a bracket citation). `app.py` renders `[Source N]`
in assistant messages as a clickable button (`src/db.py` `citations` table links
each chat message to the chunk+score it cited); clicking it opens a `<dialog>`
showing the actual chunk text and similarity score. Citation compliance depends on
the model actually following the instruction - weaker free models may occasionally
skip or malform it; the button link just won't render in that case, no error.

Free-tier model slugs on OpenRouter change over time; if `DEFAULT_MODEL` starts
returning a 404, check the current list at
`curl -s https://openrouter.ai/api/v1/models | grep ':free'`.

Setup:
1. Get an API key at https://openrouter.ai/keys
2. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY`
3. Run `python scripts/test_agents.py` from `webapp/` to send "say hello" and print
   the reply

`.env` is gitignored; `agents.py` loads it itself (no `python-dotenv` dependency).
`ai.extract_job_posting()` (used by "Add Job Posting" on the dashboard) is the
first caller; the other `src/ai.py` functions are still placeholders.

`sentence-transformers`/`chromadb` are a real departure from this project's
otherwise-stdlib-plus-a-couple-small-libs footprint (they pull in `torch` and
friends - hundreds of MB). The embedding model (`all-MiniLM-L6-v2`, ~80MB) downloads
from Hugging Face on first use and is cached after that, so the very first chat
message or onboarding upload needs internet access beyond just OpenRouter.

## Persistence

User profile/settings, every job posting, per-job progress (status, comments),
tailoring chat history + citations, generated artifacts (cover letters/résumés/Q&A
answers), writing preferences, uploaded-document text, its chunks, and small app
settings (currently just chunk size) all persist in `data/app.db` (SQLite, via
Python's built-in `sqlite3`) - chunk *embeddings* are the one thing that live outside
it, in Chroma at `data/chroma/`. `src/db.py` creates the schema automatically on
first run and is the only module that writes SQL: every write runs in its own
transaction with parameterized queries. `src/store.py` keeps an in-memory copy of
profile/jobs for cheap reads (everything else is read straight from `db.py` since
it's not needed on every request) and pushes every mutation back through `db.py`, so
routes never touch SQL directly. `chunks.document_id` and `citations.chunk_id` are
plain columns, not foreign keys on purpose: replacing a document (e.g. a resume
re-upload) drops its old chunks, and a citation on an old chat message pointing at a
now-gone chunk should just stop being clickable, not block the upload or crash the
page - see `store.save_profile_document()`.

Jobs (sample and user-added) live together in one `jobs` table, with a UNIQUE
constraint on `url`: the same posting can never exist twice, regardless of whether
it came from `data/jobs.json` or was added by URL. `origin` ('sample' or 'custom')
tracks how each row got there. `data/jobs.json` is only read to seed that table on
first run and to upsert 'sample' rows (matched by url) when "Reload sample data" is
clicked, it's never merged in at read time, and editing it can't create duplicates
or touch 'custom' rows.

## Notes

- Single hardcoded demo user: the `users` table has one row. Multi-user support
  would mean adding real auth and keying everything off the logged-in user's id
  instead of the module-level `store.user_id`.
- `app.secret_key` is a hardcoded dev value, fine for local POC use, not for any real
  deployment.
