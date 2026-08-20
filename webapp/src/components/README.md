# Sourcing components

Independent job-listing fetchers, each with its own settings, test mode, and run
history - see `/components` in the app (Admin menu -> System Components) to configure
and run them. None of these write to the main jobs table automatically; each result on a
run's results page has its own "Add to dashboard" button. Nothing here is scheduled -
every run is manually triggered.

A component's own run only fetches - every listing it returns is staged immediately (kept,
addable) and shown on that component's own page, unfiltered. Filtering is a separate,
explicit step, only run from the Filter, dedupe & save tool page (`/tools/filter_dedupe`,
`store.apply_run_filters`) against a `run_id` - a hard-preference gate (role/title,
location, remoteness, work eligibility, salary floor, all read live from the profile) plus
dedup (within the batch, against the jobs table, and against anything already kept from a
prior run). It flips whatever doesn't survive to "filtered" with a reason, shown per job
on that tool page - it never removes anything from a component's own page. See
`../filters.py`'s docstring for the full rule set. Nothing here calls that module directly
- it's cross-cutting, not a source.

Each module (`serpapi.py`, `remoteok.py`, `ats.py`) is self-contained on purpose - no
component calls another, and none of them import `store.py` or touch the database
directly. `app.py` is the only thing that wires a component's `run()` output into
persistence. That's so a future orchestrator (the LangGraph agent from the architecture
sketch) can call these same functions directly without any changes here.

## SerpAPI

Runs one or more configured Google Jobs searches, plus an optional extra search across
your followed companies by name (its own SerpAPI call, kept separate so it isn't bound to
your role queries' filters). Each query block, including the followed-companies one, has
the same filter fields: location, date posted, employment type (multi-select), and work
mode (any / local only / remote only); the query blocks additionally take job
titles/keywords (comma-separated, combined with "any"/"all" matching) - followed
companies always searches by company name instead. The default query seeds location from
your profile's home address (not a work-eligibility country) and defaults to local only,
so a fresh setup searches near you instead of defaulting to a whole country. Field values
map to SerpAPI's `google_jobs` engine params - see
`DATE_POSTED_OPTIONS`/`EMPLOYMENT_TYPE_OPTIONS`/`WORK_MODE_OPTIONS` in `serpapi.py` for
the full technical-value <-> label mapping. SerpAPI itself only supports a remote-only
filter (`ltype=1`); "local only" is done by fetching normally and dropping any result
flagged remote.

**Getting a key:**
1. Sign up at [serpapi.com](https://serpapi.com) - the free plan includes 250 searches/month.
2. Copy your API key from the dashboard.
3. Add it to `webapp/.env`: `SERP_API=your-key-here`.

No key needed for test mode - it returns fixture data and never calls SerpAPI.

**Cost:** each query in your settings (plus the followed-companies query, if enabled) is
one real SerpAPI search per run in live mode. Free tier is 250/month - keep an eye on
how many queries you've configured before running live often.

## RemoteOK

Fetches RemoteOK's public listings API and filters by keyword/seniority. No account, no
key, no cost - live and test mode both just skip the real network call in test mode.

## ATS boards

Fetches each configured company's public job board directly - Lever, Greenhouse, Ashby,
or Workable. None of these four require a key or account; they're public,
unauthenticated endpoints. You just need to know which platform a company uses and its
board slug (visible in that company's careers URL, e.g. `jobs.lever.co/<slug>`,
`boards.greenhouse.io/<slug>`, `jobs.ashbyhq.com/<slug>`, `apply.workable.com/<slug>`).

A company with no platform/slug set yet is skipped silently (not an error) - add both
before it'll actually run.
