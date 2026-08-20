"""Fit scoring: run_scan() is the single entry point that scores jobs against the current
profile's resume/story bank and stated interests (see ai.score_job). Role/title, location,
remoteness and salary are already hard-filtered before a job reaches the jobs table (see
filters.py) - scoring only judges what filtering can't.

Called from onboarding completion, the dashboard's "Run scan" button, data reload (all
three via the mode="live" default) and the Score fit tool page (/tools/score_fit, with an
explicit mode/model from the run form). Every call logs a scoring_runs row regardless of
caller - same "one choke point, always logged" shape as agents.send_chat + usage.json.

Never raises: a failure mid-run is recorded on the run (status "error", error_message set)
and returned via the run id, not thrown - so callers that don't inspect it (onboarding,
/scan, /data/reload) can't crash on it. The Score fit tool page is where the run log/errors
are actually surfaced.
"""
from datetime import datetime, timezone

from . import agents, ai, store


def _document_text(doc_type):
    doc = next((d for d in store.get_profile_documents() if d["type"] == doc_type), None)
    return doc["content"] if doc else ""


def run_scan(mode="live", model=None):
    """Score every job in store.jobs, via real ai.score_job calls either way - mode "test"
    isn't a fixture (a fake score would tell you nothing about whether the prompt/parsing
    actually works), it just forces the model to agents.DEFAULT_MODEL (a free OpenRouter
    model) regardless of what's passed in, so a dry run costs nothing no matter what's
    selected on the form. mode "live" uses whatever model is given and, on success,
    refreshes store.jobs so the dashboard shows the new scores; "test" never touches the
    dashboard. Returns the scoring_runs id either way.
    """
    live = mode == "live"
    model = (model or agents.DEFAULT_MODEL) if live else agents.DEFAULT_MODEL
    run_id = store.start_scoring_run(mode, model)

    resume_text = _document_text("resume")
    if not resume_text:
        store.finish_scoring_run(run_id, "error", 0, "No resume uploaded - can't score fit without one.")
        return run_id
    story_bank_text = _document_text("story_bank")
    industries, industries_text = store.profile["industries"], store.profile["industries_text"]

    scored, error = 0, None
    for job in store.jobs:
        try:
            result = ai.score_job(job, resume_text, story_bank_text, industries, industries_text, model)
        except Exception as e:
            error = f"Stopped after {scored} of {len(store.jobs)} jobs: {e}"
            break
        store.save_scoring_result(run_id, job["id"], result["score"], result["summary"])
        scored += 1

    store.finish_scoring_run(run_id, "error" if error else "ok", scored, error)
    if live:
        store.last_scan = datetime.now(timezone.utc)
        store.save_last_scan()
        store.reload_jobs()  # picks up the fresh scores (no-op on a fully failed run - reload_jobs
                              # only ever reads the latest *completed* ("ok") live run, see db.fetch_latest_scores)
    return run_id
