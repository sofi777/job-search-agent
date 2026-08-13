"""Job scanning.

run_scan() is the single entry point for refreshing match scores against
the current profile. It's called on-demand today (a button in the
dashboard). To add daily automation later (cron, APScheduler, a GitHub
Action, whatever), call this same function on a timer; no changes needed
here or in app.py's route logic.
"""
from datetime import datetime, timezone

from . import ai, store


def run_scan():
    """Re-score every job in store.jobs against the current profile/weights.

    Placeholder: re-ranks the existing sample postings. Real version would
    also pull fresh listings from sourcing (LinkedIn, Indeed, company
    sites, niche boards) before scoring.
    """
    for job in store.jobs:
        job["match"] = ai.score_job(job, store.profile, store.priority_weights)
    store.last_scan = datetime.now(timezone.utc)
    store.save_last_scan()
    return store.jobs
