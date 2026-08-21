"""Fit scoring: run_scan() is the single entry point that scores jobs against the current
profile's resume/story bank and stated interests (see ai.score_job). Role/title, location,
remoteness and salary are already hard-filtered before a job reaches the jobs table (see
filters.py) - scoring only judges what filtering can't.

Called from onboarding completion, the dashboard's "Run scan" button, data reload (all
three via the mode="live" default) and the Score fit tool page (/tools/score_fit, with an
explicit mode/model from the run form). Every call logs a scoring_runs row regardless of
caller - same "one choke point, always logged" shape as agents.send_chat + usage.json.

Never raises: a job that keeps failing (ai.score_job already retries transient
network/SSL errors - see agents._post_with_backoff) is skipped, not fatal to the whole
run - recorded on the run (status "error" if nothing got scored, "partial" if some jobs
did, error_message naming every skipped job) and returned via the run id, not thrown - so
callers that don't inspect it (onboarding, /scan, /data/reload) can't crash on it. The
Score fit tool page is where the run log/errors are actually surfaced. Jobs scored before
or after a skipped one still reach the dashboard - see db.fetch_latest_scores.
"""
from datetime import datetime, timezone

from . import agents, ai, store


def _document_text(doc_type):
    doc = next((d for d in store.get_profile_documents() if d["type"] == doc_type), None)
    return doc["content"] if doc else ""


def run_scan(mode="live", model=None, pending_only=False):
    """Score jobs in store.jobs, via real ai.score_job calls either way - mode "test"
    isn't a fixture (a fake score would tell you nothing about whether the prompt/parsing
    actually works), it just forces the model to agents.DEFAULT_MODEL (a free OpenRouter
    model) regardless of what's passed in, so a dry run costs nothing no matter what's
    selected on the form. mode "live" uses whatever model is given and, on success,
    refreshes store.jobs so the dashboard shows the new scores; "test" never touches the
    dashboard.

    pending_only skips jobs already in a store.TERMINAL_STATUS (applied/rejected/irrelevant) -
    the user has already decided on those, so there's no fit left to (re-)judge. Jobs skipped
    this way keep their last score (see db.fetch_latest_scores, which reads each job's most
    recent live score rather than assuming one run covers every job).

    Returns the scoring_runs id either way.
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

    jobs = [j for j in store.jobs if j["status"] not in store.TERMINAL_STATUSES] if pending_only else store.jobs

    scored, skipped = 0, []
    for job in jobs:
        try:
            result = ai.score_job(job, resume_text, story_bank_text, industries, industries_text, model)
        except Exception as e:
            # ai.score_job already retried any transient network/SSL blip (see
            # agents._post_with_backoff) - a failure here is a real one, so skip just this
            # job and keep going rather than losing the rest of the batch to it.
            skipped.append(f"{job['title']}: {e}")
            continue
        store.save_scoring_result(run_id, job["id"], result["score"], result["summary"])
        scored += 1

    # "partial" (not "error") when at least one job scored despite others being skipped -
    # those results are real and must reach the dashboard (db.fetch_latest_scores reads
    # them), not get thrown away just because the run didn't finish every job. A run that
    # scored nothing (no resume, or every job failed) stays "error".
    error = f"{len(skipped)} job(s) skipped after failing: " + "; ".join(skipped) if skipped else None
    status = "ok" if not error else ("partial" if scored else "error")
    store.finish_scoring_run(run_id, status, scored, error)
    if live:
        store.last_scan = datetime.now(timezone.utc)
        store.save_last_scan()
        store.reload_jobs()  # picks up the fresh scores (no-op on a fully failed run - reload_jobs
                              # only ever reads the latest *completed* ("ok") live run, see db.fetch_latest_scores)
    return run_id
