"""Registry for the "Tools" cards on /components (templates/components.html) and their
admin pages (templates/tool_detail.html, app.py's tool_detail() route).

Unlike src/components/, these aren't independent fetchers - they're a step in the sourcing
pipeline (filter_dedupe -> src/filters.py, applied via store.apply_run_filters - a separate
stage a component's own run() never triggers, run explicitly per run once it's fetched), a
scorer (score_fit -> src/scanner.run_scan, its own run/mode/model form + log on this same
page, see app.py's score_fit_run()), or an existing flow - so this is just display metadata;
app.py assembles each page's live data itself.

filter_dedupe and the old separate "save to database" tool are one merged tool now -
filtering a run and then adding what survives to the dashboard are two steps of the same
review flow, never done independently, so splitting them across two pages made no sense.
"""

TOOLS = {
    "filter_dedupe": {
        "name": "Filter, dedupe & save",
        "description": "Hard-preference gate (role/title, location, remoteness, work "
                        "eligibility, salary floor) + dedup, run explicitly per fetch "
                        "(a component's run() only fetches - see src/components/README.md). "
                        "Whatever survives is saved to the jobs table automatically; "
                        "whatever's dropped shows up for manual \"Add to dashboard\" if you "
                        "want to override it. See src/filters.py.",
        "status": "live",
    },
    "extract_structure": {
        "name": "Extract & structure",
        "description": "Parses a raw-text listing (e.g. a Gmail alert email) into job "
                        "fields, including description - for sources that don't already "
                        "return structured data.",
        "status": "pending",
        "blocked_reason": "No raw-text source feeds this yet - SerpAPI/RemoteOK/ATS "
                           "already return structured listings. Needed once Gmail (or "
                           "another raw-text source) is connected.",
    },
    "score_fit": {
        "name": "Score fit",
        "description": "LLM scoring of resume/story-bank skill fit and industries/free-text "
                        "alignment against jobs already on the dashboard - see src/scanner.py, "
                        "src/ai.score_job.",
        "status": "live",
    },
    "update_applied_status": {
        "name": "Update applied status",
        "description": "Match an applied-confirmation email to its job and mark it "
                        "Applied, or insert a new row.",
        "status": "pending",
        "blocked_reason": "Needs the Gmail connector (not built) to read confirmation emails.",
    },
    "tailored_generation": {
        "name": "Tailored generation",
        "description": "Cover letter, resume, and Q&A drafting for one job, grounded in the "
                        "profile/resume/story bank via retrieval - see src/agents.run_tailor_turn. "
                        "One tool: all three are tabs of the same chat, picked per job below.",
        "status": "live",
    },
    "preference_learning": {
        "name": "Preference learning",
        "description": "Reads feedback given in a tailoring chat and decides whether it reveals "
                        "a durable writing-style preference (general, or specific to cover "
                        "letter/resume/Q&A) - see src/agents.revise_preferences. Runs "
                        "automatically after a tailoring turn; edit or clear what it's learned "
                        "on /preferences.",
        "status": "live",
    },
}
