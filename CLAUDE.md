## Project status

Daily job-search agent: fetches listings (SerpAPI/Google Jobs, direct ATS APIs, RSS), filters, scores fit via Claude, pushes matches to Notion. GitHub Actions schedule is currently **disabled** (out of Actions quota) — do not re-enable without asking first.

Read `openspec/specs/*/spec.md` for current behavior and `openspec/changes/archive/*/design.md` for why things were built this way before making changes.

**Active direction:** Indeed/LinkedIn coverage via SerpAPI is too shallow. Moving toward: read the user's native LinkedIn job-alert emails via Gmail API, then fetch full descriptions directly from `linkedin.com/jobs/view/...` (confirmed publicly accessible without login) before scoring.

**Next decision needed from user:** where the agent runs now that GitHub Actions is out (scheduled Claude task vs local cron vs prototype-only for now).
