# Dream Job Landing

A proof-of-concept web app: upload your resume, tell it your preferences, and get a ranked,
personal job board. Tailor each application (cover letter, resume, Q&A answers) via a
per-job chat, with placeholder AI for suggestions and real LLM-based match scoring.

## Run tests

```bash
cd webapp
python -m unittest discover -s tests -p "test_*.py"
```

Stdlib `unittest`, no new dependency. Currently covers `src/components/` (SerpAPI,
RemoteOK, ATS boards) and `src/filters.py` - fast, all network calls mocked (filters.py
needs no mocking - it's pure).

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
                         #   (from the DB, this user's progress and latest fit
                         #   score merged in)
    ai.py               # suggest_roles/suggest_home_address are placeholders;
                         #   score_job() and extract_job_posting() are real LLM
                         #   calls, via agents.py
    agents.py            # OpenRouter transport (send_message/send_chat) plus the
                         #   tailoring chat + cross-job preference learning
                         #   (run_tailor_turn, revise_preferences) - see AI below
    prompts/              # tailoring chat prompt templates, one per artifact type
                         #   plus the preference-revision prompt
    files.py             # extract_text(): .pdf/.docx/.txt/.md -> plain text, used for
                         #   resume/cover-letter-sample/story-bank/chat-attachment uploads
    rag.py               # chunking + sentence-transformers embeddings + Chroma retrieval
                         #   for the tailoring knowledge base (documents only) - see AI below
    scanner.py          # run_scan(mode, model): fit-scores jobs against the
                         #   profile's resume/story bank/industries/free text;
                         #   called on-demand today, wire to a scheduler later
                         #   unchanged - every call logs a scoring_runs row
    learning.py          # run_learning(scope, mode, model): on-demand replay of
                         #   unchecked tailoring-chat feedback through the same
                         #   preference-learning check the live chat makes per turn
    tailoring.py          # run_turn(): one tailoring-chat turn, shared by the per-job
                         #   Tailor page and the floating assistant's cover-letter drafting
    assistant.py          # floating assistant orchestrator: routes each chat turn to a
                         #   live workflow, cover-letter drafting, or plain chat
  scripts/
    test_agents.py       # quick manual check: calls agents.send_message and prints
  data/
    jobs.json           # 10 sample job postings; seeds the jobs table on first
                         #   run. Edit this file, then click "Reload sample data"
                         #   in the dashboard to upsert your changes
    app.db               # SQLite database: user profile, all jobs (sample +
                         #   user-added), per-job progress (status, comments,
                         #   generated docs), and the floating assistant's single
                         #   global chat thread (assistant_messages). Created
                         #   automatically on first run; gitignored
    chroma/               # Chroma vector store for RAG chunk embeddings, gitignored;
                         #   rebuilds from app.db's documents table via the /chunks page
    uploads/             # uploaded resume files (gitignored)
  templates/            # Jinja2 HTML templates; base.html includes chat_widget.html
                         #   (the floating assistant) on every logged-in page
  static/style.css       # design tokens + component styles, ported from the Claude
                         #   design prototype in ../Prototype/
  static/chat_widget.js  # floating assistant's JS - fetch()+JSON turns, no page reload
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
  Cover Letter, Resume, Q&A. Each tab holds any number of independent compare "panes"
  (a `chat_sessions` row - see Persistence below) so you can run different models side
  by side - no cap on how many. Each pane is fully self-contained: its own model
  dropdown (or N/A - no call is made for that pane until one is picked), its own
  message box and send button, its own thread, and its own generated document (cover
  letter/résumé) or, for Q&A, its answers shown inline in that pane's own chat rather
  than a separate document box. Sending a message only affects that one pane - panes
  don't share an input, so you can give each model different feedback rather than
  always broadcasting the same message to all of them. "+ Add pane" sits inline at the
  end of the row as its own tile. Writing-style preferences stay global across every
  pane and model regardless - feedback in one pane improves every future generation,
  no matter which model produced the feedback or which model generates next.
- **Knowledge base chunks** (`/chunks`): every chunk the uploaded documents were split
  into, grouped by source file, with token counts. A chunk-size field + "Re-run
  chunking" button re-chunks and re-embeds the entire knowledge base at a new size.
- **Profile** (`/profile`): every onboarding answer, editable after the fact on one
  page - roles, location/commute/remote/countries, industries + free-text
  preferences, salary, and the resume/cover-letter-sample/story-bank documents.
  Re-uploading a document replaces it as the knowledge base source of truth (same
  behavior as onboarding); cover letter sample and story bank can also be removed
  outright with no replacement. Re-uploading the resume asks whether to keep the
  current roles/home address or regenerate them from the new resume.
- **On-demand scan**: "Run scan now" on the dashboard fit-scores every job against the
  current profile (resume/story bank skill fit + industries/free-text alignment - role/
  title, location, remoteness, and salary are already hard-filtered before a job reaches
  the dashboard, so scoring never re-checks them). Daily automation is intentionally not
  wired up yet (see below). Model choice, test-mode dry runs, and the full run log live
  at the Score fit tool page (`/tools/score_fit`, see below).
- **Add Job Posting**: paste a URL on the dashboard and the app fetches the page and
  uses an LLM (via `src/agents.py`) to fill in company, title, location, salary, and
  a description. Added with no match score ("Not yet ranked") until the next scan.
  Duplicate URLs are rejected with a clear message; a job can't be added twice.
  If the site blocks a plain fetch, falls back to a text-extraction proxy
  (r.jina.ai); if the page is still just a bot check or login wall, the model
  reports that clearly instead of adding a garbage entry. Sites with an
  interactive bot challenge (e.g. Cloudflare, some Indeed pages) aren't
  fetchable at all without a real browser, which this app doesn't run.
- **Response ratings** (thumbs up/down): Q&A rates each answer inline in its pane's
  chat; cover letter/résumé panes rate the document itself (under the document box,
  not each chat bubble - see Tailor my application above). Rating is one-shot (both
  buttons disable immediately via a fetch call, and stay disabled after a reload -
  the rating is persisted on the message). Each rated response - question, response
  text, the resulting document, model actually used (not just requested; see below),
  timestamp, response time, and input/output token counts - is appended to
  `data/results.json`.
- **Results** (`/results`): every rated response as a row (rating icon, model badge,
  input, output, and a "Generated document" column - fixed-height, scrollable, full
  text on hover, with a link back to that response's pane); click a row for the full
  record in a dialog. Top of the page shows overall and per-model rated counts and %
  positive.
- **Usage** (`/usage`): every LLM call (not just rated ones) logged to
  `data/usage.json` - timestamp, model, provider, prompt/completion/total tokens,
  estimated cost. Logged once, centrally, in `src/agents.py`'s `send_chat` (the only
  function that talks to an LLM), so every caller (tailoring chat, classification,
  preference learning, job extraction) is covered automatically. Top of the page
  shows total and per-model cost/token/call counts.
- **System Components** (`/components`): architecture overview of the planned
  job-scan agent (Gmail, SerpAPI, ATS boards, RemoteOK feeding a LangGraph agent
  that filters, extracts, scores, saves, and updates applied status). SerpAPI,
  RemoteOK, and ATS boards are live and link to a real settings + run + results page
  (`src/components/`, see below); of the Tools cards, Filter, dedupe & save (wired into
  every component run), Score fit, Tailored generation, and Preference learning are
  live; Extract & structure and Update applied status stay Pending until a raw-text
  source (Gmail) exists.
- **Sourcing components** (`src/components/`, linked from System Components):
  SerpAPI (Google Jobs), RemoteOK, and ATS boards (Lever/Greenhouse/Ashby/Workable)
  each get their own page to configure search criteria (seeded from your profile,
  freely editable/extendable there), run in test mode (fixture data, no real call)
  or live mode, and immediately show every listing the run returned, unfiltered,
  each with its own "Add to dashboard" button. Each component is independent - a run
  never applies filtering/dedup and never writes to the jobs table on its own, and no
  component calls another; a future workflow can wire them together without
  changing this code. A shared "followed companies" list lives on your profile
  (`store.followed_companies`) and can be extended directly from the SerpAPI/ATS
  settings pages. See `src/components/README.md` for what each needs (SerpAPI
  needs a free API key; RemoteOK and ATS boards need nothing).
- **Filter & dedupe** (`src/filters.py`, `/tools/filter_dedupe` - never a component's
  own page, which stays filter-agnostic): run explicitly per fetch against a run's
  already-staged listings, gating them against your profile's hard preferences -
  role/title, location, remoteness, work eligibility, salary floor - and deduping
  (within the batch, against the jobs table, and against anything already kept from
  an earlier run). There's no separate settings screen for this: it reads your profile
  live, so editing it on `/profile` is how you change what gets filtered. Nothing is
  removed - a filtered-out listing just flips to status "filtered" with a reason, still
  visible (and still addable) on its component's own page; running the tool page's Run
  log shows an expandable per-job "Kept" vs. "Filtered out + reason" breakdown per run,
  not just the aggregate count. Soft preferences
  (resume/story-bank skill fit, industries, free text) aren't applied here - that's the
  Score fit tool (`src/scanner.py`'s `run_scan()`), a separate step that scores jobs
  already saved to the dashboard.
- **Score fit** (`/tools/score_fit`): LLM scoring of every job on the dashboard against
  your resume, story bank, and stated industries/free-text preferences (`ai.score_job`,
  one call per job). A settings form picks the model (any of `agents.MODEL_OPTIONS`) and
  test vs live mode; both make real LLM calls (test mode is a genuine dry run, not a
  fixture, so it actually validates the prompt/parsing), but test mode always forces the
  free default model regardless of what's picked and never touches the dashboard. "Run"
  scores every job and, in live mode, updates the dashboard's match % + summary
  immediately. Every run - however triggered,
  including the dashboard's own "Run scan now" - is logged with its mode, model, job
  count, and any error, and this run's own results table (score + why, per job) plus the
  full run history are both shown on this page.
- **Tool admin pages** (`/tools/<id>`, linked from every card in System Components'
  Tools section, `src/tools.py` registry): what that tool does, and for the live ones,
  its current live state. Filter, dedupe & save shows the hard-preference values it's
  reading right now plus a combined log of every source run's filter/dedup outcome
  across all components (with an expandable per-job kept/filtered breakdown once a run's
  been filtered), split into Test/Live tabs, staged (kept) results awaiting "Add to
  dashboard", and the saved-jobs log. Score fit is described above. Tailored
  generation is a job picker that opens that job's existing `/jobs/<id>/tailor` page
  (no new generation logic - see Tailoring chat below). Preference learning has its
  own Run: pick a job or "All jobs", a model, and test/live mode, then it replays
  tailoring-chat feedback that hasn't been checked yet through the same reveals-a-
  preference judgment the tailoring chat already makes automatically per turn (see
  Preference learning below) - test mode previews what it would learn without touching
  `/preferences`; live mode applies it and marks that feedback checked, so a repeat run
  only ever looks at what's new. Below the run log, each category's current learned
  text and when it last changed is still shown read-only, with a link to `/preferences`
  to edit directly. The two pending tools just explain what's blocking them.
- **Workflows** (`/workflows`, linked from the Admin menu): dashboard of multi-step
  flows chaining the above components/tools, one card per `src/workflows.py` entry with
  a chip per component/tool it will use (linking to that component's/tool's own page).
  - Job search and rerank: SerpAPI + RemoteOK + ATS boards -> Filter, dedupe & save ->
    Score fit -> dashboard. Has a real runner (`workflows.run_job_search_rerank`) and
    can be triggered from the floating assistant chat (see below); this page itself
    still has no manual "Run now" button.
  - Tailor cover letter for top 3 jobs: not implemented yet - display only, no runner.
- **Floating assistant** (bottom-right bubble, every logged-in page - `src/assistant.py`,
  `templates/chat_widget.html`): one continuous chat, not scoped to any single job or
  page, grounded in your full profile/preferences/dashboard (cross-session memory - it
  persists forever, same DB as everything else). Understands three kinds of requests:
  - **Trigger a live workflow** in plain language ("run job search and rerank") - runs
    the same chain as above and replies with an exact, non-hallucinated summary (jobs
    added per source, rescored count, any per-source errors) composed in Python from the
    real result, not restated by the model.
  - **Draft or revise a cover letter** for a job ("draft a cover letter for the Notion
    role", or just "make it shorter" once one's already the topic) - runs through the
    exact same turn logic as the per-job Tailor page (`src/tailoring.run_turn`, see
    Tailoring chat above), so the draft, the learned-preference feedback loop, and the
    thumbs up/down rating are all the *same* record shown on that job's own `/jobs/<id>/
    tailor` page - the chat is just an additional front door onto it, not a separate
    copy. Preview and give feedback right in the chat; no need to navigate away.
  - **Answer questions or just talk** using your profile/preferences/dashboard as
    context, when the message isn't asking for either of the above.

  Tracks which job is under discussion across turns without you repeating it (falls
  back to whichever job the last relevant turn was about; asks for clarification rather
  than guessing if a name matches more than one job, or none). A model dropdown at the
  bottom of the panel switches models mid-conversation without losing the thread or
  forking a new chat - unlike the Tailor page's compare panes, which are deliberately
  one independent thread per model.

## What's a placeholder (by design)

`suggest_roles` and `suggest_home_address` in `src/ai.py` are still generic,
deterministic stand-ins, not real LLM calls. Each function's docstring says what a real
implementation should do; swapping the body for a real model call requires no changes to
any caller. `ai.score_job()`, `ai.extract_job_posting()`, and the tailoring chat
(`src/agents.py`) are real LLM calls.

`src/scanner.run_scan()` is the single entry point for refreshing job matches. It runs
on-demand only for now; daily automation (cron, APScheduler, a GitHub Action, etc.) can
call this same function on a timer without any other changes.

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
sends one pane's own chat history plus the new message, and parses a structured
JSON reply (`{reply, artifact}` for cover_letter/resume; `{reply, action, question,
answer}` for qa - the model decides new-question vs. feedback from context, no
manual toggle needed). `app.py`'s `_run_pane_turn()` persists the chat turn and
artifact via `store.py`, once per active pane. The user's message is saved to that
pane's chat immediately, before any of the LLM calls that could fail - if one does
(rate limit, JSON parse error), the message stays visible with the error shown in
that pane, instead of silently vanishing and forcing a retype - and other panes
aren't affected, since each pane's turn runs independently (see Compare panes
below).

**Compare panes**: `chat_sessions` (`src/db.py`) is one row per pane - job, tab,
model, own thread, own artifact/Q&A list, no cap on how many can exist for one
job+tab. Each pane is its own `<form>` posting to
`/jobs/<id>/tailor/<tab>/session/<session_id>/message` - a message only ever affects
the one pane it was sent from, on purpose: panes used to share one input that
broadcast the same message to every active one, which meant you couldn't give
different models different feedback. `session_message()` syncs `chat_sessions.model`
if the pane's dropdown changed, then runs `_run_pane_turn()` for that pane alone. A
`RuntimeError` from it (rate limit, JSON parse error) is caught and shown as that
pane's own error (`session["tailor_errors"]`, keyed by session id), same as before.
Removing a pane (`POST .../session/<id>/remove`) hides it (`chat_sessions.hidden`)
rather than deleting it - its chat/artifact history stays in the DB. If a still-empty
pane's model dropdown is then switched to that same model, `store.switch_session_model()`
resurfaces the hidden session (and deletes the empty one) instead of starting a fresh
blank thread, so re-adding a removed model picks up where it left off.
`src/rag.py`'s lazily-initialized embedder/Chroma client are still guarded by a lock
(`_init_lock`) - Flask's dev server can still handle more than one request at a time
(e.g. two pane sends fired close together in separate tabs), and two threads hitting
a cold start at once previously crashed chromadb's client construction outright, not
just raced it. Writing-style preferences (`revise_preferences`, below) are re-fetched
fresh on every send and never scoped per pane, so feedback in any one pane's chat
improves every pane's future output, regardless of which model produced the feedback
or which model generates next. Existing job data from before this feature predates
`chat_sessions`; `src/db.py`'s migration backfills one session per pre-existing (job,
tab) thread, using whatever model that thread's last message actually used, so it
picks up as that pane's Session 1.

**Cross-job preference learning**: `revise_preferences()` makes a second, separate
call asking whether a turn's feedback revealed a durable style preference (vs. being
specific to that one document), and if so returns the revised text for the right
category. Preferences are stored as four plain-text fields (general, cover_letter,
resume, qa - general applies everywhere, a category overrides it on conflict),
editable directly at `/preferences`, which also shows the prior value after an
auto-update. Runs on whichever model the user picked for the main generation (not a
fixed model) - so preference-learning never adds a paid call the user didn't choose;
staying on free models keeps this free too. Skipped entirely on the first message for
a tab (nothing to give feedback on yet) or when `classify_turn()` (see below) says
this message doesn't look like it reveals a preference. Every feedback message this
runs on gets marked checked (`chat_messages.preference_checked_at`) right after, live
or not - see `src/learning.py` below for the on-demand version of this same check.

**Preference learning, on demand** (`src/learning.py`, `/tools/preference_learning`):
the live check above only ever sees a turn as it happens. `run_learning()` replays
whatever feedback hasn't been checked yet - across every job, or one - through the
exact same `classify_turn`/`revise_preferences` judgment, for backfilling older chats
or catching anything the live hook missed. Incremental: a message is marked checked
the moment it's looked at (live or here), so re-running only ever processes what's
new. Test mode previews for real (genuine LLM calls, logged to
`preference_learning_run_results`) without touching `/preferences` or marking
anything checked; live mode does both.

**Cost-saving on regenerate turns**: iterating on a draft ("make it shorter",
"regenerate") is the most common action in this app, so a few things are skipped
when a turn doesn't need the full treatment. `agents.classify_turn()` makes a single
free-tier classification call on `DEFAULT_MODEL` per turn, answering two questions
at once (one call, not two, to minimize API calls): does this message need fresh RAG
retrieval, and does it look like it might reveal a durable preference? Neither is
answerable from word count: a brand-new qa question ("Why us?") and pure feedback
("make it shorter") are both commonly short, but only one needs new facts or could
reveal a preference - a real semantic read gets both right, for all three tabs.
Always runs, even on an obvious first message, for consistency - free-tier, so the
cost is latency, not money. Defaults both answers to "do the real thing" if the call
fails or is ambiguous (the safe direction: doing it when unsure never hurts
correctness, it just costs a bit more).
- Retrieval is skipped when `needs_retrieval` is false - saves ~550 tokens/turn on
  the main call.
- `revise_preferences()` is skipped when `reveals_preference` is false (or there's
  no existing draft/Q&A yet) - the bigger saving, since it's a full second API call.
- Chat history sent to the model is capped to the last `HISTORY_MAX_MESSAGES` (~3
  exchanges) - the current draft/Q&A list already carries the full up-to-date state
  into every prompt, so older exchanges are mostly redundant for continuing edits.
- For `anthropic/*` models, the system prompt is split into a stable prefix
  (instructions/job/profile - identical across every turn of one pane's thread) and
  a per-turn dynamic suffix (current draft + this turn's retrieved context), with the
  prefix marked via `cache_control` for Anthropic's prompt caching (`_build_system_content`)
  - OpenRouter passes this through, discounting repeat input tokens ~90% within the
    cache's TTL. Anthropic-only since `cache_control` is an Anthropic-specific extension;
    other providers get the plain single-string prompt as before, unaffected.

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

**Usage tracking**: `MODEL_PRICING` in `src/agents.py` holds rough public per-1M-token
USD pricing (free models are $0); OpenRouter doesn't return actual cost in the
response, so `estimated_cost_usd` is an estimate, not a bill. Unlisted models default
to $0 rather than guessing. Update `MODEL_PRICING` alongside `MODEL_OPTIONS`.

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
compare panes (`chat_sessions` - job, tab, model, one per pane) with their chat
history + citations, generated artifacts (cover letters/résumés/Q&A answers, each
scoped to its pane via `session_id`), writing preferences, uploaded-document text,
its chunks, and small app settings (currently just chunk size) all persist in
`data/app.db` (SQLite, via
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

`data/results.json` is the one exception to "everything lives in `app.db`": rated
chat responses (see Response ratings above) are appended there as plain JSON, one
entry per rating, since it's meant to be a flat, easy-to-inspect export rather than
a queryable table. `chat_messages.rating` in the DB is the source of truth for
whether a given message has been rated (so buttons stay disabled after a reload);
`results.json` is written once, the first time a message is rated - see
`store.rate_chat_message()`.

## Notes

- Single hardcoded demo user: the `users` table has one row. Multi-user support
  would mean adding real auth and keying everything off the logged-in user's id
  instead of the module-level `store.user_id`.
- `app.secret_key` is a hardcoded dev value, fine for local POC use, not for any real
  deployment.
