"""Registry for the "Tools" cards on /components (templates/components.html) and their
admin pages (templates/tool_detail.html, app.py's tool_detail() route).

Unlike src/components/, these aren't independent fetchers with their own run() - they're
either a step in the sourcing pipeline (filter_dedupe -> src/filters.py, applied via
store.apply_run_filters - a separate stage a component's own run() never triggers, run
explicitly per run once it's fetched) or an existing flow, so this is just display
metadata; app.py assembles each page's live data itself.

filter_dedupe and the old separate "save to database" tool are one merged tool now -
filtering a run and then adding what survives to the dashboard are two steps of the same
review flow, never done independently, so splitting them across two pages made no sense.
"""

TOOLS = {
    "filter_dedupe": {
        "name": "Filter, dedupe & save",
        "description": "Hard-preference gate (role/title, location, remoteness, work "
                        "eligibility, salary floor) + dedup, run explicitly per fetch "
                        "(a component's run() only fetches - see src/components/README.md) "
                        "- then \"Add to dashboard\" on what survives inserts it into the "
                        "jobs table. See src/filters.py.",
        "status": "live",
    },
    "extract_structure": {
        "name": "Extract & structure",
        "description": "Fetch a raw listing and parse it into job fields.",
        "status": "pending",
        "blocked_reason": "No raw-text source feeds this yet - SerpAPI/RemoteOK/ATS "
                           "already return structured listings. Needed once Gmail (or "
                           "another raw-text source) is connected.",
    },
    "score_fit": {
        "name": "Score fit",
        "description": "LLM scoring against the profile's soft preferences (industries, "
                        "free text).",
        "status": "pending",
        "blocked_reason": "Needs richer per-job context extraction first, not yet built. "
                           "Today, src/scanner.py's run_scan() does a placeholder "
                           "keyword/number score against jobs already on the dashboard - "
                           "see /dashboard's \"Run scan\".",
    },
    "update_applied_status": {
        "name": "Update applied status",
        "description": "Match an applied-confirmation email to its job and mark it "
                        "Applied, or insert a new row.",
        "status": "pending",
        "blocked_reason": "Needs the Gmail connector (not built) to read confirmation emails.",
    },
}
