# job-search-agent

Personal job-search automation. The repo currently holds two things at different stages
of the same goal — a ranked, personal job board that reduces time spent on low-fit
applications:

- **`job_agent.py`** — a working daily script: pulls listings (SerpAPI/Google Jobs, RSS,
  direct ATS APIs), filters and scores fit via Claude, and pushes matches to Notion.
- **`webapp/`** — **Dream Job Landing**, a Flask proof-of-concept rebuilding this as a
  proper app: resume-driven onboarding, a ranked/filterable dashboard, and placeholder
  AI hooks ready to swap in real model calls. See [`webapp/README.md`](webapp/README.md)
  for the full writeup.

The target direction that unifies both is tracked in
[`openspec/changes/2026-07-30-job-search-copilot/proposal.md`](openspec/changes/2026-07-30-job-search-copilot/proposal.md).

## Features

**`job_agent.py`**
- Multi-source listing ingestion (SerpAPI/Google Jobs, RSS, direct ATS APIs)
- Fit scoring against a hardcoded profile via the Anthropic API
- Pushes matches to a Notion database
- Scheduled via GitHub Actions (currently disabled — see Known limitations)

**`webapp/` (Dream Job Landing)**
- Resume upload → onboarding wizard (roles, location/commute/remote, industries,
  minimum salary) → immediate scan
- Sortable, filterable dashboard with editable job status, notes, and match ranking
- Placeholder cover letter / tailored résumé generation, structured for a real LLM swap
- SQLite persistence (user profile + per-job progress); sample job catalog on disk

## Technology

Python 3, Flask, SQLite (`sqlite3`, built-in), Anthropic API, Notion API, SerpAPI,
GitHub Actions.

## Setup

**`webapp/` (Dream Job Landing) — run locally:**
```bash
cd webapp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://localhost:8014. Full details in [`webapp/README.md`](webapp/README.md).

**`job_agent.py` — run locally:**
```bash
pip install anthropic "notion-client==2.2.1" feedparser requests google-search-results
export ANTHROPIC_API_KEY=...
export NOTION_TOKEN=...
export NOTION_DATABASE_ID=...
export SERP_API=...
python job_agent.py
```
These same variables must be set as GitHub Actions secrets for the scheduled workflow
in [`.github/workflows/job-agent.yml`](.github/workflows/job-agent.yml).

## Project structure

```
job-search-agent/
  job_agent.py          # daily scoring/Notion-push script (see above)
  .github/workflows/     # scheduled GitHub Action for job_agent.py (currently disabled)
  webapp/                # Dream Job Landing — Flask POC (see webapp/README.md)
  openspec/               # change proposals/specs tracking the target direction
  Prototype/              # exported UI design prototype the webapp was built from
  CLAUDE.md               # project status notes for AI-assisted development
```

## Known limitations

- The GitHub Actions schedule for `job_agent.py` is disabled (ran out of Actions quota)
  — it must be triggered manually (`workflow_dispatch`) or run locally until re-enabled.
- `job_agent.py`'s LinkedIn/Indeed coverage via SerpAPI is shallow; a follow-up change
  (reading LinkedIn job-alert emails via Gmail API) is planned but not built.
- `webapp/` and `job_agent.py` are not yet integrated — two separate implementations
  toward the same goal, per the OpenSpec proposal linked above.
- `webapp/` has no real AI integration yet; all suggestions/scoring/generation are
  deterministic placeholders (see `webapp/src/ai.py`).
- `webapp/` is single-user (one hardcoded demo profile) with no real authentication —
  a proof of concept, not production-ready.
