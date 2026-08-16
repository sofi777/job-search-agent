"""AI-shaped functions: some placeholders, some real.

suggest_roles, suggest_home_address and score_job don't call a real model
yet. Each returns a generic, deterministic hint derived from simple
string/number rules. Every function's signature (what it takes, what it
returns) is the contract a real LLM-backed implementation should keep, so
swapping the body later is a drop-in change with no caller updates.

extract_job_posting calls agents.send_message. Cover letter / resume /
Q&A generation lives in the tailoring chat (see src/agents.py
run_tailor_turn), not here - those are conversational and job-scoped, not
one-shot calls.
"""
import json
import re
import urllib.error
import urllib.request
from datetime import date

from . import agents

GENERIC_ROLE_SUGGESTIONS = ["Senior Product Manager", "Product Lead", "Group Product Manager"]


def suggest_roles(resume_filename):
    """Suggest roles to apply for, based on the uploaded resume.

    Placeholder: ignores file content and returns a fixed generic list.
    Real version: extract résumé text and infer job titles from it.
    """
    return list(GENERIC_ROLE_SUGGESTIONS)


def suggest_home_address(resume_filename):
    """Suggest a home base for commute-distance matching, based on the resume.

    Placeholder: ignores file content and returns a fixed generic city.
    Real version: extract an address/city from the résumé text.
    """
    return "San Francisco, CA"


def score_job(job, profile, weights):
    """Score how well a job fits the profile, 0-100.

    Placeholder: cheap keyword/number overlap, weighted by `weights`.
    Real version: send job + profile to an LLM and parse a fit score.
    """
    text = f"{job['title']} {job['description']}".lower()

    role_score = 100 if any(role.lower() in text for role in profile["roles"]) else 40

    if job.get("remote") and profile.get("remote_ok"):
        location_score = 100
    elif profile.get("home_address", "").split(",")[0].lower() in job.get("location", "").lower():
        location_score = 90
    else:
        location_score = 30

    job_min, job_max = job.get("salary_min", 0), job.get("salary_max", 0)
    min_salary = profile.get("min_salary", 0)
    if job_max and job_max >= min_salary:
        salary_score = 100
    elif job_min and job_min >= min_salary * 0.85:
        salary_score = 60
    else:
        salary_score = 20

    industries = [i.lower() for i in profile.get("industries", [])]
    industry_score = 100 if (not industries or any(i in text for i in industries)) else 50

    total_weight = sum(weights.values()) or 1
    weighted = (
        role_score * weights.get("role_match", 0)
        + location_score * weights.get("location_fit", 0)
        + salary_score * weights.get("salary_fit", 0)
        + industry_score * weights.get("industry_fit", 0)
    ) / total_weight

    return round(weighted)


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_PLAIN_HEADERS = {"User-Agent": "dream-job-landing/1.0"}


def _fetch_raw(url, headers=_BROWSER_HEADERS):
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise RuntimeError(f"Could not fetch {url}: {e}") from e


def _fetch_page_text(url):
    """Fetch a URL and return readable text.

    Falls back to a text-extraction proxy (r.jina.ai) if the direct request
    is blocked, common for job boards that reject non-browser requests.
    This doesn't help against interactive bot challenges (e.g. Cloudflare);
    those are caught afterward, when the LLM reports the fetched text isn't
    actually a job posting.
    """
    try:
        return _strip_html(_fetch_raw(url))
    except RuntimeError as direct_error:
        try:
            # r.jina.ai itself rejects requests carrying a spoofed browser UA; plain headers only.
            return _fetch_raw(f"https://r.jina.ai/{url}", headers=_PLAIN_HEADERS)
        except RuntimeError as proxy_error:
            raise RuntimeError(
                f"Could not fetch {url}: {direct_error}. Fallback fetch also failed: {proxy_error}"
            ) from proxy_error


def _strip_html(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_job_posting(url):
    """Fetch a job posting URL and extract structured fields via the LLM.

    Not a placeholder: uses agents.send_message for real extraction, so it
    requires OPENROUTER_API_KEY (see src/agents.py). Raises RuntimeError with
    a clear message if the fetch or extraction fails.
    """
    text = _fetch_page_text(url)[:6000]
    if not text:
        raise RuntimeError(f"No readable content found at {url}.")

    prompt = (
        "You will be given the text of a web page that is supposed to be a job "
        "posting. If it is NOT actually a job posting (for example it's a bot "
        "verification page, login wall, paywall, or error page), reply with "
        'exactly {"error": "<short reason>"} and nothing else.\n\n'
        "Otherwise, extract structured data and reply with ONLY a JSON object, "
        "no markdown fences, no commentary, with exactly these keys: company "
        "(string), title (string), location (string), remote (true or false), "
        "salary_min (integer, 0 if unknown), salary_max (integer, 0 if unknown), "
        "currency (3-letter code, USD if unknown), description (a 2-3 sentence "
        "plain-text summary of the role).\n\n"
        f"Page text:\n{text}"
    )
    reply, _used_model, _usage = agents.send_message(prompt)
    try:
        data = json.loads(agents.strip_json_fence(reply))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse job details from the model's reply: {reply[:200]}") from e

    if "error" in data:
        raise RuntimeError(f"Could not read the job posting at {url}: {data['error']}")

    return {
        "company": str(data.get("company") or "Unknown company"),
        "title": str(data.get("title") or "Unknown role"),
        "source": "Direct link",
        "location": str(data.get("location") or ""),
        "remote": bool(data.get("remote")),
        "posted": date.today().isoformat(),
        "salary_min": int(data.get("salary_min") or 0),
        "salary_max": int(data.get("salary_max") or 0),
        "currency": str(data.get("currency") or "USD"),
        "url": url,
        "description": str(data.get("description") or ""),
    }
