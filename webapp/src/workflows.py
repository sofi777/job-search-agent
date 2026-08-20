"""Registry for the "Workflows" dashboard (/workflows, templates/workflows.html) - each
entry is a multi-step flow chaining existing components (src/components/) and tools
(src/tools.py) together. "status": "live" entries have a "run" callable (see
run_job_search_rerank below) and are what src/assistant.py's router can trigger by name;
"pending" entries are display-only, not yet wired up.

"uses" is a list of (kind, ref) pairs: "component"/"tool" refs must be valid ids in
src.components.COMPONENTS / src.tools.TOOLS (checked in tests/test_workflows.py);
"other" is a plain label for anything outside those two registries.
"""
from . import components as comp, scanner, store


def run_job_search_rerank(mode="live"):
    """Fetch every sourcing component -> filter/dedupe/stage -> add survivors to the
    dashboard -> rescore. Mirrors scanner.run_scan's "never raises" contract: a failure in
    one component (e.g. a missing API key) is recorded and skipped, not fatal to the other
    two or to the rescore step - a chat-triggered call must never crash the turn.

    mode="test" runs every component in its own test/preview mode (no real API spend) and
    still stages/filters/adds/scores the results, so a dry run exercises the full chain.

    Returns {"per_component": {component_id: {"fetched": n, "added": n, "error": str|None}},
             "added": total added across every component, "scan_run_id": ..., "rescored": n,
             "scan_error": str|None}.
    """
    test_mode = mode != "live"
    summary = {"per_component": {}, "added": 0, "scan_run_id": None, "rescored": 0, "scan_error": None}

    for component_id, meta in comp.COMPONENTS.items():
        config = store.get_component_config(component_id, meta["default_config"](store.profile))
        if component_id == "serpapi":
            config = {**config, "followed_companies": store.get_followed_companies()}

        run_id = store.start_run(component_id, mode)
        try:
            listings, error = meta["run"](config, test_mode)
        except Exception as e:
            listings, error = [], str(e)
        store.save_fetch_results(run_id, listings, error)
        store.apply_run_filters(run_id)

        added = 0
        for result in store.get_run_results(run_id):
            if result["status"] != "kept":
                continue
            try:
                store.add_run_result_to_dashboard(result["id"], component_id)
                added += 1
            except RuntimeError:
                pass  # already on the dashboard (e.g. another component found it first) - not a failure
        summary["per_component"][component_id] = {"fetched": len(listings), "added": added, "error": error}
        summary["added"] += added

    summary["scan_run_id"] = scanner.run_scan(mode=mode)
    scan_run = store.get_scoring_run(summary["scan_run_id"])
    summary["rescored"] = scan_run["scored_count"] if scan_run else 0
    summary["scan_error"] = scan_run["error_message"] if scan_run else None
    return summary


WORKFLOWS = {
    "job_search_rerank": {
        "name": "Job search and rerank",
        "description": "Fetches new listings from SerpAPI, RemoteOK, and ATS boards, "
                        "filters and dedupes them onto the dashboard, then reranks every "
                        "active job by fit score.",
        "uses": [
            ("component", "serpapi"), ("component", "remoteok"), ("component", "ats"),
            ("tool", "filter_dedupe"), ("tool", "score_fit"),
        ],
        "status": "live",
        "run": run_job_search_rerank,
    },
    "tailor_top_3": {
        "name": "Tailor cover letter for top 3 jobs",
        "description": "Takes the 3 highest-scored active jobs and drafts a tailored "
                        "cover letter for each with the profile's default chat model, "
                        "saving each draft on that job's page.",
        "uses": [
            ("tool", "tailored_generation"), ("other", "Default model (src/agents.py)"),
        ],
        "status": "pending",
    },
}
