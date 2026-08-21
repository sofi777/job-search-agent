"""Sourcing components: independent, self-contained fetchers - each owns its own settings,
run history, and results (see app.py's /components/<id> routes and store.py's
get_component_config/start_run/save_run_result). See README.md for what each does and how
to get API keys.

Each module exposes:
  default_config(profile) -> dict              seed settings from the current profile
  run(config, test_mode) -> (listings, error)    listings normalized via base.normalize_listing

Deliberately not wired together yet - each runs on its own, from its own page, and nothing
here calls another component or writes to the jobs table itself (see app.py's
component_result_add route for the one explicit "add this to my dashboard" action). A
future workflow can import and call these the same way app.py does, with no changes here.

"enabled": False stops a component's live run() from being called at all (app.py's manual
run route and workflows.run_job_search_rerank both check it) - flip back to True to
re-enable. Test mode is unaffected either way; it never makes a real call.
"""
from . import ats, remoteok, serpapi

COMPONENTS = {
    "serpapi": {
        "name": "SerpAPI",
        "description": "Google Jobs search - Indeed, LinkedIn, government roles, followed companies.",
        "default_config": serpapi.default_config,
        "run": serpapi.run,
        "enabled": True,
    },
    "remoteok": {
        "name": "RemoteOK",
        "description": "Remote-tagged listings from RemoteOK's public API.",
        "default_config": remoteok.default_config,
        "run": remoteok.run,
        "enabled": False,  # paused 2026-08-20 at the user's request, to cut down on live calls
    },
    "ats": {
        "name": "ATS boards",
        "description": "Direct company career pages on Lever, Greenhouse, Ashby, and Workable.",
        "default_config": ats.default_config,
        "run": ats.run,
        "enabled": False,  # paused 2026-08-20 at the user's request, to cut down on live calls
    },
}
