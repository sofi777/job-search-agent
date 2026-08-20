"""ATS boards - direct company career-page APIs. No key required for any of the four
platforms below. See README.md.

Each company row in config["companies"] is {"name", "platform", "slug"} - platform is one
of FETCHERS' keys, slug is that platform's own board identifier (visible in the company's
careers URL, e.g. jobs.lever.co/<slug>). A row with no platform/slug yet is skipped, not
errored - it just isn't configured to run yet.
"""
from datetime import datetime, timezone

from . import base

DEFAULT_PM_KEYWORDS = [
    "product manager", "product lead", "head of product", "director of product",
    "vp of product", "vp product", "product owner", "group product manager",
]
DEFAULT_SENIORITY_KEYWORDS = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]

FAKE_RESULTS = [
    base.normalize_listing(
        title="Senior Product Manager (TEST)", company="Test ATS Co", source="Lever / Test ATS Co (TEST)",
        url="https://example.com/ats-test-1", location="Remote", remote=True, posted="2026-08-18",
        description="Fixture result - test mode only, no real API call made.",
    ),
]


def default_config(profile):
    return {
        "companies": [{"name": c, "platform": "", "slug": ""} for c in profile.get("followed_companies", [])],
        "keywords": list(DEFAULT_PM_KEYWORDS),
        "seniority_keywords": list(DEFAULT_SENIORITY_KEYWORDS),
        "location_filter": list(profile.get("eligible_countries", [])),
    }


def _matches(title, keywords, seniority_keywords):
    return base.keyword_match(title, keywords) and (
        not seniority_keywords or base.keyword_match(title, seniority_keywords)
    )


def _fetch_lever(slug, company_name, keywords, seniority, location_filter):
    jobs = base.fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    listings = []
    for job in jobs:
        title = job.get("text", "")
        if not _matches(title, keywords, seniority):
            continue
        location = job.get("categories", {}).get("location", "Not specified")
        if not base.location_match(location, location_filter):
            continue
        posted = "Unknown"
        if job.get("createdAt"):
            posted = datetime.fromtimestamp(job["createdAt"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        listings.append(base.normalize_listing(
            title=title, company=company_name, source=f"Lever / {company_name}",
            url=job.get("hostedUrl", ""), location=location, remote=base.looks_remote(location), posted=posted,
            description=job.get("descriptionPlain", ""),
        ))
    return listings


def _fetch_greenhouse(slug, company_name, keywords, seniority, location_filter):
    jobs = None
    for endpoint in (
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        f"https://job-boards.greenhouse.io/api/v1/boards/{slug}/jobs?content=true",
    ):
        try:
            jobs = base.fetch_json(endpoint).get("jobs", [])
            break
        except RuntimeError:
            continue
    if jobs is None:
        raise RuntimeError(f"Greenhouse board '{slug}' not found on either endpoint")

    listings = []
    for job in jobs:
        title = job.get("title", "")
        if not _matches(title, keywords, seniority):
            continue
        location = job.get("location", {}).get("name", "Not specified")
        if not base.location_match(location, location_filter):
            continue
        listings.append(base.normalize_listing(
            title=title, company=company_name, source=f"Greenhouse / {company_name}",
            url=job.get("absolute_url", ""), location=location, remote=base.looks_remote(location),
            posted=(job.get("updated_at") or "Unknown")[:10],
            description=job.get("content", ""),
        ))
    return listings


def _fetch_ashby(slug, company_name, keywords, seniority, location_filter):
    jobs = base.fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").get("jobPostings", [])
    listings = []
    for job in jobs:
        title = job.get("title", "")
        if not _matches(title, keywords, seniority):
            continue
        location = job.get("location", "Not specified")
        if isinstance(location, dict):
            location = location.get("name", "Not specified")
        if not base.location_match(location, location_filter):
            continue
        listings.append(base.normalize_listing(
            title=title, company=company_name, source=f"Ashby / {company_name}",
            url=job.get("jobUrl", ""), location=location, remote=base.looks_remote(location),
            posted=(job.get("publishedDate") or "Unknown")[:10],
            description=job.get("descriptionPlain") or job.get("description", ""),
        ))
    return listings


def _fetch_workable(slug, company_name, keywords, seniority, location_filter):
    jobs = base.fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}").get("jobs", [])
    listings = []
    for job in jobs:
        title = job.get("title", "")
        if not _matches(title, keywords, seniority):
            continue
        loc = job.get("location", {})
        city, remote = loc.get("city", ""), job.get("remote", False)
        location = city if city else ("Remote" if remote else "Not specified")
        if not base.location_match(location, location_filter):
            continue
        listings.append(base.normalize_listing(
            title=title, company=company_name, source=f"Workable / {company_name}",
            url=f"https://apply.workable.com/{slug}/j/{job.get('shortcode', '')}",
            location=location, remote=remote,
            posted=(job.get("published_on") or "Unknown")[:10],
            description=job.get("description", ""),
        ))
    return listings


FETCHERS = {"lever": _fetch_lever, "greenhouse": _fetch_greenhouse, "ashby": _fetch_ashby, "workable": _fetch_workable}


def run(config, test_mode):
    if test_mode:
        return list(FAKE_RESULTS), None

    keywords = config.get("keywords", [])
    seniority = config.get("seniority_keywords", [])
    location_filter = config.get("location_filter", [])

    listings, errors = [], []
    for company in config.get("companies", []):
        platform, slug, name = company.get("platform", ""), company.get("slug", ""), company.get("name", "")
        if not platform or not slug:
            continue  # not configured yet - needs a platform + slug before it's fetchable
        fetcher = FETCHERS.get(platform)
        if fetcher is None:
            errors.append(f"{name}: unknown platform '{platform}'")
            continue
        try:
            listings += fetcher(slug, name or slug, keywords, seniority, location_filter)
        except RuntimeError as e:
            errors.append(f"{name or slug}: {e}")

    return listings, "; ".join(errors) if errors else None
