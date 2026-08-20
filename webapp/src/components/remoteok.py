"""RemoteOK - public JSON API, no key required. See README.md.
"""
from datetime import datetime, timedelta, timezone

from . import base

REMOTEOK_URL = "https://remoteok.com/api"

DEFAULT_SENIORITY_KEYWORDS = ["senior", "lead", "group", "director", "vp", "principal", "head", "staff"]

FAKE_RESULTS = [
    base.normalize_listing(
        title="Senior Product Manager (TEST)", company="Test Remote Co", source="RemoteOK (TEST)",
        url="https://example.com/remoteok-test-1", location="Remote", remote=True, posted="2026-08-18",
        description="Fixture result - test mode only, no real API call made.",
    ),
]


def default_config(profile):
    return {
        "keywords": (profile.get("roles") or ["Product Manager"]) + ["Product Owner"],
        "seniority_keywords": list(DEFAULT_SENIORITY_KEYWORDS),
        "posted_within_days": None,
    }


def _within_days(date_str, days):
    """True if date_str is within the last `days` days, or days is falsy (no filter), or
    date_str can't be parsed - a formatting quirk shouldn't silently drop a real listing."""
    if not days:
        return True
    try:
        posted = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    return posted >= datetime.now(timezone.utc) - timedelta(days=days)


def run(config, test_mode):
    if test_mode:
        return list(FAKE_RESULTS), None

    try:
        raw = base.fetch_json(REMOTEOK_URL)
    except RuntimeError as e:
        return [], str(e)

    keywords = config.get("keywords", [])
    seniority = config.get("seniority_keywords", [])
    posted_within_days = config.get("posted_within_days")

    listings = []
    for job in raw:
        if not isinstance(job, dict):
            continue  # RemoteOK's first array item is a legal notice, not a job
        title = job.get("position", "")
        if not title:
            continue
        tags = " ".join(job.get("tags", []))
        if not base.keyword_match(f"{title} {tags}", keywords):
            continue
        if seniority and not base.keyword_match(title, seniority):
            continue
        date_str = job.get("date", "")
        if not _within_days(date_str, posted_within_days):
            continue
        listings.append(base.normalize_listing(
            title=title, company=job.get("company", "Unknown"), source="RemoteOK",
            url=job.get("url") or f"https://remoteok.com/l/{job.get('id', '')}",
            location="Remote", remote=True, posted=date_str or "Unknown",
            description=job.get("description", ""),
        ))
    return listings, None
