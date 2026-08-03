## Why

Product name: **DreamJobLanding**.

Job search is a full-time job. LinkedIn and Indeed cover most postings, but the best-fit role is often on a company site or a niche board (e.g. Wellfound) and gets missed while time goes into applying to less-relevant jobs. Need a personal, ranked job board that also reduces the effort and improves the quality of applying.

## What Changes

- Pull listings from all relevant sources — LinkedIn, Indeed, company career sites, niche boards — not just the two big ones
- Rank listings against resume + stated preferences; ranking improves from the user's own actions (applied/skipped/edited)
- Generate tailored cover letters and draft answers to application questions, grounded in the user's real achievements, in their authentic voice — voice improves from the user's edits
- Track every application and its status in one board
- Interview prep is an explicit future extension, out of scope for this change

## Capabilities

### New Capabilities
- `sourcing`: multi-source job listing ingestion (LinkedIn, Indeed, company sites, niche boards)
- `ranking`: fit-scoring against resume/preferences, with a feedback loop from user actions
- `application-assist`: tailored cover letter + application-question drafting, with a feedback loop from user edits
- `tracking`: application status board

### Modified Capabilities
- None (first OpenSpec-tracked change; supersedes the ad hoc scoring/Notion-push logic already in `job_agent.py`)

## Impact

Sets the target shape for `job-search-agent`. Sourcing already partially exists (SerpAPI, direct ATS APIs, RSS) but LinkedIn/Indeed coverage is currently too shallow — separate follow-up change. `application-assist`, `tracking` (beyond the existing raw Notion push), and the learning loops in `ranking`/`application-assist` are net-new. Started: `webapp/app.py`, a single-file Flask app (no database, no extra deps — per the `code` rule) with a landing page as the first screen of the `tracking` board. No other architecture/runtime decisions made yet (deferred).
