"""SerpAPI - Google Jobs search, one call per configured query. See README.md for how to
get a SERP_API key and what it costs; test mode never calls SerpAPI at all.
"""
import os
import urllib.parse

from . import base

SERPAPI_URL = "https://serpapi.com/search"

# (technical value, human label) - shared with component_detail.html so the dropdowns and
# the API params never drift out of sync.
DATE_POSTED_OPTIONS = [
    ("any", "Any time"), ("today", "Past 24 hours"), ("3days", "Past 3 days"),
    ("week", "Past week"), ("month", "Past month"),
]
EMPLOYMENT_TYPE_OPTIONS = [
    ("FULLTIME", "Full-time"), ("PARTTIME", "Part-time"),
    ("CONTRACTOR", "Contract"), ("INTERN", "Internship"),
]
MATCH_OPTIONS = [("OR", "Any of these terms"), ("AND", "All of these terms")]
REMOTE_ONLY_OPTIONS = [("", "Any location"), ("1", "Remote only")]

DATE_POSTED_CHIPS = {opt: f"date_posted:{opt}" for opt, _ in DATE_POSTED_OPTIONS if opt != "any"}

# Low-quality aggregator domains - never worth surfacing as an apply link.
BLOCKED_AGGREGATORS = (
    "sercanto.com", "jooble.org", "talent.com", "jobrapido.com", "neuvoo.com",
    "adzuna.com", "jobsora.com", "joblist.com", "whatjobs.com", "jobted.com",
    "trovit.com", "mitula.com", "jobbird.com", "jobtome.com", "jobsincanada.one",
)

FAKE_RESULTS = [
    base.normalize_listing(
        title="Senior Product Manager - Digital Health (TEST)", company="Test Health Co",
        source="SerpAPI (TEST)", url="https://example.com/serpapi-test-1",
        location="United States", remote=True, posted="2026-08-18", salary_min=140000, salary_max=170000,
        description="Fixture result - test mode only, no real API call made.",
    ),
    base.normalize_listing(
        title="Group Product Manager, Platform (TEST)", company="Test Platform Inc",
        source="SerpAPI (TEST)", url="https://example.com/serpapi-test-2",
        location="Not specified", posted="2026-08-17",
        description="Fixture result - test mode only, no real API call made.",
    ),
]


def default_config(profile):
    role_terms = profile.get("roles") or ["Senior Product Manager"]
    location = (profile.get("eligible_countries") or ["United States"])[0]
    return {
        "queries": [{
            "label": "Primary roles", "terms": role_terms, "match": "OR",
            "location": location, "date_posted": "week",
            "employment_types": [], "remote_only": False,
        }],
        "use_followed_companies": True,
        "followed_companies_filters": {
            "location": "", "date_posted": "month", "employment_types": [], "remote_only": False,
        },
    }


def _best_url(job):
    """First apply link not from a known low-quality aggregator, falling back to share_link."""
    for option in job.get("apply_options", []):
        link = option.get("link", "")
        if link and not any(b in link for b in BLOCKED_AGGREGATORS):
            return link
    return job.get("share_link", "")


def _to_listing(job, label):
    detected = job.get("detected_extensions", {})
    location = job.get("location", "Not specified")
    return base.normalize_listing(
        title=job.get("title", ""), company=job.get("company_name", "Unknown"),
        source=f"SerpAPI / {label}", url=_best_url(job),
        location=location, remote=bool(detected.get("work_from_home")) or base.looks_remote(location),
        posted=detected.get("posted_at", "Unknown"),
        description=job.get("description", ""),
    )


def _fetch_query(q, api_key):
    """q: {"label", "terms", "match", "location", "date_posted", "employment_types",
    "remote_only"} - see default_config()."""
    joiner = f' {q.get("match", "OR")} '
    query_str = joiner.join(f'"{t}"' for t in q.get("terms", []))

    chips = [DATE_POSTED_CHIPS[q["date_posted"]]] if q.get("date_posted") in DATE_POSTED_CHIPS else []
    chips += [f"employment_type:{et}" for et in q.get("employment_types", [])]

    params = {"engine": "google_jobs", "q": query_str, "hl": "en", "api_key": api_key}
    if chips:
        params["chips"] = ",".join(chips)
    if q.get("location"):
        params["location"] = q["location"]
    if q.get("remote_only"):
        params["ltype"] = "1"

    url = SERPAPI_URL + "?" + urllib.parse.urlencode(params)
    data = base.fetch_json(url)
    return [_to_listing(job, q.get("label", "Search")) for job in data.get("jobs_results", [])]


def run(config, test_mode):
    """config additionally accepts "followed_companies" (a plain name list) merged in by
    app.py at call time, since that list lives on the profile, not in this component's own
    saved config - see store.get_followed_companies()."""
    if test_mode:
        return list(FAKE_RESULTS), None

    api_key = os.environ.get("SERP_API", "")
    if not api_key:
        return [], "SERP_API not set in webapp/.env - see src/components/README.md."

    listings, errors = [], []
    for q in config.get("queries", []):
        try:
            listings += _fetch_query(q, api_key)
        except RuntimeError as e:
            errors.append(f"{q.get('label', 'Search')}: {e}")

    if config.get("use_followed_companies") and config.get("followed_companies"):
        batch = {"label": "Followed companies", "terms": config["followed_companies"], "match": "OR",
                 **config.get("followed_companies_filters", {"date_posted": "month"})}
        try:
            listings += _fetch_query(batch, api_key)
        except RuntimeError as e:
            errors.append(f"Followed companies: {e}")

    return listings, "; ".join(errors) if errors else None
