"""Shared helpers for sourcing components. See README.md in this folder for what each
component does and how to get its API key, if it needs one.

Every component module exposes the same shape:
  default_config(profile) -> dict            seed settings from the current profile
  run(config, test_mode) -> (listings, error)  listings are already normalized (see
                                                normalize_listing) to the same shape as a
                                                jobs-table row, so app.py can store/insert
                                                them with no component-specific mapping.
error is None on full success, or a readable string describing what went wrong (a
component can still return partial listings alongside an error - e.g. one bad company in
an otherwise-working ATS run).
"""
import json
import urllib.error
import urllib.request

USER_AGENT = "Mozilla/5.0 job-search-agent/1.0"
TIMEOUT_SECONDS = 20


def fetch_json(url, headers=None):
    """GET url, parse JSON. Raises RuntimeError with a readable message on any failure -
    no silent fallback, per project convention."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} returned HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{url} didn't return valid JSON") from e


def normalize_listing(*, title, company, source, url, location="Not specified", remote=False,
                       posted="Unknown", salary_min=0, salary_max=0, currency="USD", description=""):
    return {
        "title": title, "company": company, "source": source, "url": url,
        "location": location, "remote": remote, "posted": posted,
        "salary_min": salary_min, "salary_max": salary_max, "currency": currency,
        "description": (description or "")[:2000],
    }


def keyword_match(text, keywords):
    """True if any keyword appears in text (case-insensitive). Empty keywords = match everything."""
    if not keywords:
        return True
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def location_match(location, allowed):
    """True if location contains one of the allowed strings, or allowed is empty (no filter)."""
    if not allowed:
        return True
    lowered = (location or "").lower()
    return any(a.lower() in lowered for a in allowed)


def looks_remote(location):
    """True if the location text itself says remote (e.g. "Remote", "Remote - US") - a
    fallback for sources whose API doesn't expose an explicit remote flag."""
    return "remote" in (location or "").lower()
