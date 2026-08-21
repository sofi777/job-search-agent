"""Hard-preference gate + dedup, applied to freshly fetched listings before they're staged
for review (and, from there, before anything reaches the jobs table). Single entry point:
filter_and_dedupe(). Pure functions - no store/db import - so app.py passes in the real
known-urls set (store.get_known_urls()) and this stays unit-testable without a database.

Hard preferences (from the profile) gate a listing out entirely: role/title, location,
remoteness, work eligibility, salary floor. Soft signals (industries, free-text
preferences) are NOT applied here - see ai.score_job for scoring against those, which runs
later as its own step once a job is already saved (src/scanner.py).
"""
from .components import base

# Location text sources commonly use for a remote posting with no country restriction
# stated - a bare "Remote" shouldn't be hard-excluded by remote_countries the way a blank
# onsite location is by eligible_countries, since it's normal, not missing/bad data.
_GENERIC_REMOTE_LOCATIONS = {"remote", "anywhere", "worldwide", "global", "not specified", ""}

# Still just a text match, not real geocoding (commute_miles itself is never read - see
# ISSUES.md's commute-radius gap) - but a bare city-name match calls every neighbouring
# suburb "outside commute range" even when it's a normal commute in the same metro area.
# Keyed by home city (lowercase); add more metros here as they come up.
_METRO_AREA_SUBURBS = {
    "vancouver": {
        "richmond", "surrey", "burnaby", "coquitlam", "port coquitlam", "port moody",
        "north vancouver", "west vancouver", "new westminster", "delta", "langley", "white rock",
    },
}


def _home_city(profile):
    """First segment of home_address ("San Francisco, CA" -> "San Francisco"), or "" if unset.
    Matches the same city-only heuristic ai.score_job already uses for its soft location score."""
    return profile.get("home_address", "").split(",")[0].strip()


def _city_match(home_city, location):
    """True if home_city and the listing's location plausibly name the same city - checked both
    ways since either side can carry extra words the other doesn't ("Vancouver, Canada" home
    address vs a bare "Vancouver" listing, or the reverse: "Greater Vancouver Area" vs
    "Vancouver, BC") - or name a known suburb of it (see _METRO_AREA_SUBURBS). A one-directional
    substring check misses whichever case is backwards."""
    home_city, location = home_city.lower(), location.lower()
    listing_city = location.split(",")[0].strip()
    if home_city in location or (listing_city and listing_city in home_city):
        return True
    return listing_city in _METRO_AREA_SUBURBS.get(home_city, set())


def dedupe(listings):
    """Drop listings whose url repeats an earlier one in this batch. Listings with no url
    can't be identified, so they're never treated as duplicates of each other."""
    seen, kept, dropped = set(), [], []
    for listing in listings:
        url = (listing.get("url") or "").strip()
        if url and url in seen:
            dropped.append((listing, "duplicate in this run"))
            continue
        if url:
            seen.add(url)
        kept.append(listing)
    return kept, dropped


def _hard_filter_reason(listing, profile):
    if not base.keyword_match(listing.get("title", ""), profile.get("roles", [])):
        return "role/title"

    if listing.get("remote"):
        if not profile.get("remote_ok", True):
            return "remote not wanted"
        location = listing.get("location", "")
        remote_countries = profile.get("remote_countries", [])
        is_generic = location.strip().lower() in _GENERIC_REMOTE_LOCATIONS
        if remote_countries and not is_generic and not base.location_match(location, remote_countries):
            return "not eligible to work remotely from this location"
    else:
        location = listing.get("location", "")
        home_city = _home_city(profile)
        in_home_city = home_city and location and _city_match(home_city, location)
        # A listing in the user's own home city is commutable by definition - don't also demand
        # the country name literally appear in the listing text (many sources give city only,
        # e.g. a bare "Vancouver" - see ISSUES.md's exact-substring country-matching gap).
        # Otherwise fall back to the country-eligibility check, then the commute-city gate -
        # onsite means commutable, not "eligible to relocate" (no real geocoding/radius yet,
        # city-text match only - see ISSUES.md).
        if not in_home_city:
            if not base.location_match(location, profile.get("eligible_countries", [])):
                return "not eligible to work in this location"
            if home_city and location:
                return "outside commute range"

    min_salary = profile.get("min_salary", 0)
    if min_salary:
        known_ceiling = listing.get("salary_max", 0) or listing.get("salary_min", 0)
        if known_ceiling and known_ceiling < min_salary:
            return "below minimum salary"

    return None


def apply_hard_filters(listings, profile):
    """profile: the current user profile dict (store.profile) - roles, remote_ok,
    remote_countries, eligible_countries, min_salary. Returns (kept, dropped) where dropped
    is a list of (listing, reason) tuples."""
    kept, dropped = [], []
    for listing in listings:
        reason = _hard_filter_reason(listing, profile)
        if reason:
            dropped.append((listing, reason))
        else:
            kept.append(listing)
    return kept, dropped


def summarize_drops(dropped):
    """[(listing, reason), ...] -> {reason: count}, for the run summary."""
    reasons = {}
    for _listing, reason in dropped:
        reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


def describe_active_filters(profile):
    """The hard-preference values currently in effect, read live from the profile - for
    the Filter & dedupe tool page. There's no separate config: editing the profile is the
    only way to change these."""
    return [
        {"label": "Roles / title", "value": ", ".join(profile.get("roles", [])) or "Any (no filter)"},
        {"label": "Remote OK", "value": "Yes" if profile.get("remote_ok", True) else "No"},
        {"label": "Remote countries", "value": ", ".join(profile.get("remote_countries", [])) or "Any (no filter)"},
        {"label": "Eligible countries (onsite)", "value": ", ".join(profile.get("eligible_countries", [])) or "Any (no filter)"},
        {"label": "Commute city (onsite)", "value": _home_city(profile) or "Any (no filter)"},
        {"label": "Minimum salary", "value": f"{profile.get('currency', 'USD')} {profile['min_salary']:,}" if profile.get("min_salary") else "Any (no floor)"},
    ]


def filter_and_dedupe(listings, profile, known_urls=None):
    """Full gate: dedupe within this batch, drop anything already known (known_urls - a
    set of urls already on the dashboard or staged from a prior run, e.g.
    store.get_known_urls()), then apply the hard preference filters.

    Returns (kept, dropped) - kept is a plain list of listings ready to stage; dropped is
    [(listing, reason), ...] for reporting on the run.
    """
    kept, dropped = dedupe(listings)

    if known_urls:
        still_kept = []
        for listing in kept:
            if listing.get("url") and listing["url"] in known_urls:
                dropped.append((listing, "already on your list"))
            else:
                still_kept.append(listing)
        kept = still_kept

    filtered, filtered_out = apply_hard_filters(kept, profile)
    dropped += filtered_out
    return filtered, dropped
