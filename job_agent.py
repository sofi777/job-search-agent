import anthropic
import json
import requests
import feedparser
from datetime import datetime, timezone
from notion_client import Client
import os
# VERSION 6 — 2026-04-10 — PRODUCTIONimport anthropic
import json
import requests
import feedparser
import urllib.parse
from datetime import datetime, timezone
from notion_client import Client
import os
# VERSION 7 — 2026-04-10 — PRODUCTION
# Changes: LinkedIn search URL for all sources, not just SerpAPI

# ─────────────────────────────────────────────
# TEST MODE
# True  = no SerpAPI calls, no Claude calls, Notion push still runs
# False = full production run
# ─────────────────────────────────────────────

TEST_MODE = False  # ← set to True to test without spending API credits

# ─────────────────────────────────────────────
# PROFILE & SCORING CRITERIA
# ─────────────────────────────────────────────

SOFIA_PROFILE = """
Name: Sofia Blaunshteyn
Current location: Vancouver, BC, Canada

Background:
- 15+ years in tech: started as Android/Software Engineer (Samsung, Start.IO), transitioned to PM
- Lead PM at Match Group (GenAI features, 0→1 LLM agent with cross-session memory)
- Group PM at BC Government (led 18 engineers, rebuilt failing digital forms platform, $2.5M savings)
- Senior PM at Hello Heart (0→1 ML-powered mobile health feature, 1.5x engagement boost)
- PM Team Lead at Gett (ML marketplace algorithms, last-mile delivery, +70% daily deliveries)
- MBA, Tel Aviv University; BSc Computer Science

Core strengths: 0→1 product builds, AI/ML/GenAI product ownership, platform strategy,
stakeholder management, cross-functional leadership, data-driven decisions, consumer + B2B

Target roles: Senior PM, Lead PM, Group PM, Director of Product, VP of Product
Minimum salary: CAD $120,000 (no ceiling)
Location: Remote Canada, remote US (if open to Canadian applicants), or hybrid/in-office in BC

Industry preferences (for scoring):
- TOP TIER: Healthtech, wellness, longevity, senior care
- SECOND TIER: Any consumer-facing product
- MEDIUM: Govtech, civic tech
- NEUTRAL: B2B SaaS, enterprise (score on other criteria)

Government role equivalents to treat as Senior PM:
Director of Digital Services, Senior Business Analyst (technology),
IT Project Manager (digital/product focus), Service Design Lead,
Director of Product and Technology
"""

SCORING_CRITERIA = """
Score this job listing for Sofia on a scale of 1-10 using these weighted criteria:

1. SCOPE & OWNERSHIP (highest weight, 35%):
   - Owns 0→1 or major product area (not incremental features) → high score
   - Direct impact on company-level metrics (growth, revenue, adoption) → high score
   - Opportunity to define strategy, not just execute roadmap → high score
   - Space to shape new product paradigms, not maintain legacy → high score

2. INDUSTRY FIT (25%):
   - Healthtech, wellness, longevity, senior care → top score
   - Consumer-facing product → second tier score
   - Govtech/civic tech → medium score
   - B2B SaaS/enterprise → neutral, score on other criteria

3. AI/ML/GENAI EXPOSURE (20%):
   - Meaningful work with GenAI, agents, personalization → high score
   - General AI/ML product work → medium score
   - No AI component → lower score

4. USER IMPACT & PROBLEM DEPTH (20%):
   - Solves real meaningful user problems (health, behavior change, social impact) → high score
   - Ability to influence end-to-end user journey → high score

HARD EXCLUSIONS — return score 0 and action "exclude" ONLY if one of these is clearly and explicitly true:
- Explicitly requires relocation (not just "office available")
- Explicitly states US citizenship required, US work permit required, US work visa required, or security clearance required
- Title is clearly below Senior PM level (e.g. "Product Manager", "Associate PM", "Junior PM") — NOT government equivalents
- Salary is explicitly stated in the job posting AND is below CAD $120,000
- US-based role that is explicitly NOT remote (e.g. "onsite only", "must work from our NYC office")
- Canadian role outside BC that is explicitly NOT remote (e.g. in-office only in Toronto, Ottawa, Montreal, Calgary)

KEEP — do NOT exclude these:
- Remote anywhere in Canada
- Remote US (open to or not excluding Canadian applicants)
- In-office or hybrid in Vancouver metro: Vancouver, Burnaby, Port Moody, Surrey, Richmond,
  North Vancouver, West Vancouver, Coquitlam, New Westminster, Langley, Maple Ridge
- Any role where location or remote policy is unclear — score it and flag in red_flags instead
- Government roles with equivalent titles (Director of Digital Services, Service Design Lead, etc.)

DO NOT exclude based on:
- Missing salary info (very common — just score it)
- Unclear or ambiguous location (score it, flag uncertainty in red_flags)
- Any doubt or ambiguity — when in doubt, score it and let Sofia decide

APPLY MODE RULES:
- Score 8-10 → action: "manual" (Sofia applies herself)
- Score 5-7 → action: "auto" (flag for auto-apply)
- Score 1-4 → action: "low" (visible in Notion but deprioritized)
- Score 0 → action: "exclude" (do not push to Notion)
"""

# ─────────────────────────────────────────────
# CLIENTS
# ─────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
notion = Client(auth=os.environ["NOTION_TOKEN"])
DB_ID = os.environ["NOTION_DATABASE_ID"]
SERP_API_KEY = os.environ.get("SERP_API", "")

# ─────────────────────────────────────────────
# LOCATION FILTER — North America allowlist
# Applied BEFORE Claude to save API calls
# ─────────────────────────────────────────────

NA_SIGNALS = [
    "canada", "canadian", "british columbia", "bc", "vancouver", "burnaby",
    "victoria", "surrey", "richmond", "coquitlam", "north vancouver",
    "west vancouver", "port moody", "new westminster", "langley", "maple ridge",
    "ontario", "toronto", "alberta", "calgary", "quebec", "montreal",
    "united states", "usa", "us", "new york", "san francisco", "seattle",
    "california", "texas", "remote", "north america", "anywhere",
    "not specified", "not disclosed",
]

NON_NA_SIGNALS = [
    "portugal", "porto", "lisbon", "uk", "united kingdom", "london",
    "germany", "berlin", "munich", "france", "paris", "spain", "madrid",
    "barcelona", "amsterdam", "netherlands", "sweden", "stockholm",
    "denmark", "copenhagen", "norway", "oslo", "finland", "helsinki",
    "ireland", "dublin", "australia", "sydney", "melbourne", "new zealand",
    "india", "bangalore", "mumbai", "singapore", "hong kong", "japan",
    "tokyo", "china", "beijing", "shanghai", "brazil", "mexico",
    "latin america", "apac", "emea", "europe", "asia",
]

US_WORK_AUTH_SIGNALS = [
    "must have a legal right to work in the united states",
    "legal right to work in the us",
    "authorized to work in the united states",
    "authorized to work in the us",
    "must be authorized to work in the us",
    "work authorization in the united states",
    "sponsorship will not be provided",
    "we do not sponsor",
    "unable to sponsor",
    "cannot sponsor",
    "no visa sponsorship",
    "visa sponsorship is not available",
    "sponsorship not available",
    "must be a us citizen",
    "us citizenship required",
    "eligible to work in the us without sponsorship",
    "without sponsorship",
]
US_CITIES = [
    "new york", "san francisco", "los angeles", "chicago", "boston",
    "austin", "seattle", "denver", "atlanta", "miami", "dallas",
    "portland", "philadelphia", "washington dc", "washington, dc",
    "minneapolis", "phoenix", "san diego", "nashville", "raleigh",
    "princeton", "new jersey", "nj, usa", "ny, usa", "ca, usa",
]
INOFFICE_SIGNALS = ["hybrid", "onsite", "on-site", "in office", "in-office", "days per week on-site", "days on-site", "days/week on-site"]

def is_workable_location(location: str) -> bool:
    """
    Stage 1: Reject non-North-America.
    Stage 2: Reject US in-office/hybrid (Sofia cannot commute from Vancouver).
    Keep: remote anywhere, BC office/hybrid, Canada, unknown.
    """
    if not location or location.strip() == "":
        return True
    loc = location.lower()
    # Stage 1: hard reject non-NA
    if any(signal in loc for signal in NON_NA_SIGNALS):
        return False
    # Stage 2: reject US city + hybrid/onsite without remote mention
    if "remote" not in loc:
        if any(city in loc for city in US_CITIES) and any(sig in loc for sig in INOFFICE_SIGNALS):
            return False
    return True

def is_workable_description(description: str, location: str) -> bool:
    """
    Catches two patterns buried in job description text:
    1. Hybrid/in-office + US city combination
    2. Explicit US work authorization requirements
    """
    if not description:
        return True
    desc = description.lower()
    loc = location.lower() if location else ""

    # Check 1: explicit US work authorization requirement — always reject
    if any(sig in desc for sig in US_WORK_AUTH_SIGNALS):
        return False

    # If location confirmed BC or Canada — skip hybrid check
    bc_signals = ["vancouver", "burnaby", "british columbia", " bc,", "victoria", "canada"]
    if any(sig in loc for sig in bc_signals):
        return True

    # If location explicitly remote — skip hybrid check
    if "remote" in loc:
        return True

    # Check 2: hybrid + US city in description
    has_inoffice = any(sig in desc for sig in INOFFICE_SIGNALS)
    has_us_city = any(city in desc for city in US_CITIES)
    if has_inoffice and has_us_city:
        if "fully remote" in desc or "100% remote" in desc or "remote-first" in desc:
            return True
        return False

    return True

# ─────────────────────────────────────────────
# DEDUPLICATION
# FIX: Updated for newer notion-client API
# ─────────────────────────────────────────────

def get_existing_urls():
    """Fetch all URLs already in Notion to avoid duplicates."""
    existing = set()
    try:
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {"database_id": DB_ID, "page_size": 100}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            for page in response["results"]:
                url_prop = page["properties"].get("URL", {})
                url = url_prop.get("url")
                if url:
                    existing.add(url)
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
    except Exception as e:
        print(f"  ⚠️  Warning: deduplication failed: {e}")
        print(f"  ⚠️  Continuing without deduplication — may see duplicates")
    return existing

# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────

FAKE_SCORE = {
    "score": 7,
    "action": "auto",
    "industry": "TEST MODE — Healthtech",
    "match_reason": "TEST MODE — not a real score",
    "why_you_fit": "TEST MODE — not a real assessment",
    "red_flags": "None",
    "job_description_summary": "TEST MODE — fake job description summary.",
    "company_summary": "TEST MODE — fake company summary.",
    "us_open_to_canadians": None,
}

def score_job(title, description, company, salary="Not specified", location="Not specified"):
    if TEST_MODE:
        print(f"  🧪 TEST MODE — skipping Claude, returning fake score")
        return FAKE_SCORE

    prompt = f"""You are a job relevance scorer. Score this job for the candidate below.
Return ONLY a valid JSON object — no preamble, no markdown fences, no extra text.

{SOFIA_PROFILE}

{SCORING_CRITERIA}

JOB TO SCORE:
Title: {title}
Company: {company}
Location: {location}
Salary: {salary}
Description: {description[:1500]}

Return exactly this JSON shape:
{{
  "score": <0-10 integer>,
  "action": "<exclude|low|auto|manual>",
  "industry": "<actual industry name, be specific>",
  "match_reason": "<one sentence why this matches or doesn't>",
  "why_you_fit": "<one sentence connecting Sofia's specific background to this role>",
  "red_flags": "<one sentence on any concerns, or 'None' if clean>",
  "job_description_summary": "<2 sentences: what product Sofia would be building>",
  "company_summary": "<2 sentences: what the company product does>",
  "us_open_to_canadians": <true|false|null>
}}"""

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        print(f"  ❌ Scoring error for {title}: {e}")
        return None

# ─────────────────────────────────────────────
# NOTION PUSH
# ─────────────────────────────────────────────

def push_to_notion(title, company, url, location, salary, date_posted, source, scored, linkedin_url=""):
    try:
        properties = {
            "Name": {"title": [{"text": {"content": title[:200]}}]},
            "Company": {"rich_text": [{"text": {"content": company[:200]}}]},
            "Company Description": {"rich_text": [{"text": {"content": scored.get("company_summary", "")[:500]}}]},
            "Job Description": {"rich_text": [{"text": {"content": scored.get("job_description_summary", "")[:500]}}]},
            "Industry": {"rich_text": [{"text": {"content": scored.get("industry", "")[:200]}}]},
            "Score": {"number": scored.get("score", 0)},
            "Apply Mode": {"select": {"name": scored.get("action", "low").title()}},
            "Match Reason": {"rich_text": [{"text": {"content": scored.get("match_reason", "")[:500]}}]},
            "Why You Fit": {"rich_text": [{"text": {"content": scored.get("why_you_fit", "")[:500]}}]},
            "Red Flags": {"rich_text": [{"text": {"content": scored.get("red_flags", "None")[:500]}}]},
            "Location": {"rich_text": [{"text": {"content": location[:200]}}]},
            "Salary": {"rich_text": [{"text": {"content": salary[:200]}}]},
            "Date Posted": {"rich_text": [{"text": {"content": date_posted[:100]}}]},
            "Source": {"rich_text": [{"text": {"content": source[:200]}}]},
            "URL": {"url": url},
            "Status": {"select": {"name": "New"}},
        }
        if linkedin_url:
            properties["LinkedIn URL"] = {"url": linkedin_url}
        notion.pages.create(parent={"database_id": DB_ID}, properties=properties)
        linkedin_note = " 🔗 LinkedIn" if linkedin_url else ""
        print(f"  ✅ Pushed: {title} at {company} (score: {scored.get('score')}){linkedin_note}")
    except Exception as e:
        print(f"  ❌ Notion push error for {title}: {e}")

def _make_linkedin_search_url(title: str, company: str) -> str:
    """
    Construct a LinkedIn job search URL for any listing.
    Opens LinkedIn pre-filtered to this role at this company —
    enough to see network connections even without a direct posting link.
    """
    query = urllib.parse.quote(f"{title} {company}")
    return f"https://www.linkedin.com/jobs/search/?keywords={query}"

# ─────────────────────────────────────────────
# SERP API HELPERS
# ─────────────────────────────────────────────

SERP_PM_QUERY = (
    '"senior product manager" OR "lead product manager" OR '
    '"group product manager" OR "director of product" OR '
    '"head of product" OR "VP of product" OR '
    '"senior product owner" OR "lead product owner"'
)

SERP_GOV_QUERY = (
    '"senior product manager" OR "product owner" OR '
    '"director digital services" OR "service design lead" OR '
    '"IT project manager"'
)

FAKE_SERP_LISTINGS = [
    {
        "title": "Senior Product Manager — Digital Health (TEST)",
        "company": "Test Company Canada",
        "url": "https://example.com/job/test-1",
        "location": "Vancouver, BC",
        "salary": "CAD $140,000",
        "date_posted": datetime.now().strftime("%Y-%m-%d"),
        "source": "SerpAPI / Indeed Canada (TEST)",
        "description": "Fake listing in TEST_MODE. No real API call was made.",
    },
    {
        "title": "Lead Product Manager — AI Platform (TEST)",
        "company": "Test Healthtech Inc",
        "url": "https://example.com/job/test-2",
        "location": "Remote Canada",
        "salary": "Not specified",
        "date_posted": datetime.now().strftime("%Y-%m-%d"),
        "source": "SerpAPI / Indeed Canada (TEST)",
        "description": "Another fake listing. Flip TEST_MODE = False for real results.",
    },
]

def _serp_fetch(params, source_label):
    """Shared SerpAPI fetch helper with logging."""
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=20)
        print(f"  {source_label} status: {response.status_code}", end="")
        if response.status_code != 200:
            print(f" — error: {response.text[:100]}")
            return []
        data = response.json()
        jobs = data.get("jobs_results", [])
        print(f" — {len(jobs)} results")
        return jobs
    except Exception as e:
        print(f"\n  ❌ {source_label} error: {e}")
        return []

def _best_apply_url(job: dict) -> str:
    """
    Extract the best direct apply URL from a SerpAPI Google Jobs result.
    Priority:
      Tier 1: LinkedIn — for network/referral visibility
      Tier 2: Company ATS (Lever/Greenhouse/Ashby/Workday) — direct, trackable
      Tier 3: Indeed, Glassdoor, ZipRecruiter
      Tier 4: Any other link
      Fallback: share_link (Google search URL — last resort only)
    """
    apply_options = job.get("apply_options", [])
    if not apply_options:
        return job.get("share_link", "")

    tiers = [
        # Tier 1: LinkedIn — see network connections and get referrals
        ["linkedin.com"],
        # Tier 2: Direct company ATS — trackable, dedup-friendly
        ["lever.co", "greenhouse.io", "ashbyhq.com", "workday.com",
         "smartrecruiters.com", "jobvite.com", "icims.com", "taleo.net"],
        # Tier 3: Major job boards
        ["indeed.com", "glassdoor.com", "ziprecruiter.com"],
        # Tier 4: anything else available
        [],
    ]
    for tier in tiers:
        for option in apply_options:
            link = option.get("link", "")
            if not tier:  # tier 4 — take first available
                return link
            if any(domain in link for domain in tier):
                return link

    return job.get("share_link", "")

def _linkedin_url(job: dict) -> str:
    """Extract LinkedIn URL from apply_options if available, else construct a search URL."""
    for option in job.get("apply_options", []):
        if "linkedin.com" in option.get("link", ""):
            return option["link"]
    # Fall back to constructed search URL
    title = job.get("title", "")
    company = job.get("company_name", "")
    if title and company:
        return _make_linkedin_search_url(title, company)
    return ""

def _serp_job_to_listing(job, company_override=None, location_override=None, source="SerpAPI"):
    detected = job.get("detected_extensions", {})
    url = _best_apply_url(job)
    linkedin = _linkedin_url(job)
    return {
        "title": job.get("title", ""),
        "company": company_override or job.get("company_name", "Unknown"),
        "url": url,
        "linkedin_url": linkedin,
        "location": location_override or job.get("location", "Not specified"),
        "salary": detected.get("salary", "Not specified"),
        "date_posted": detected.get("posted_at", "Unknown"),
        "source": source,
        "description": job.get("description", "")[:1500],
    }

# ─────────────────────────────────────────────
# SOURCE 1: SERPAPI — INDEED CANADA
# ─────────────────────────────────────────────

def fetch_serpapi_indeed():
    print("\n📡 Fetching Indeed Canada via SerpAPI...")
    if TEST_MODE:
        print(f"  🧪 TEST MODE — returning {len(FAKE_SERP_LISTINGS)} fake listings")
        return FAKE_SERP_LISTINGS
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": SERP_PM_QUERY,
        "location": "Canada",
        "gl": "ca",
        "hl": "en",
        "chips": "date_posted:month",  # ← first run: month covers 10+ days. Change to "date_posted:today" after first run
        "api_key": SERP_API_KEY,
    }, "SerpAPI Indeed Canada")
    listings = [_serp_job_to_listing(j, source="SerpAPI / Indeed Canada") for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 2: SERPAPI — BC PUBLIC SERVICE
# ─────────────────────────────────────────────

def fetch_serpapi_bc_gov():
    print("\n📡 Fetching BC Public Service via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": f"{SERP_GOV_QUERY} site:bcpublicservice.ca",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI BC Gov")
    listings = [_serp_job_to_listing(j, company_override="BC Public Service",
                location_override="British Columbia", source="SerpAPI / BC Public Service")
                for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 3: SERPAPI — GOVERNMENT OF CANADA
# ─────────────────────────────────────────────

def fetch_serpapi_gc_jobs():
    print("\n📡 Fetching Government of Canada via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": f"{SERP_GOV_QUERY} site:jobs-emplois.gc.ca",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI GC Jobs")
    listings = [_serp_job_to_listing(j, company_override="Government of Canada",
                location_override="Canada (remote eligible)", source="SerpAPI / GC Jobs")
                for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 4: SERPAPI — BC HEALTH AUTHORITIES
# ─────────────────────────────────────────────

def fetch_serpapi_health_authorities():
    print("\n📡 Fetching BC Health Authorities via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": (
            '("product manager" OR "product owner" OR "director digital") '
            '("Fraser Health" OR "Vancouver Coastal Health" OR "Providence Health" '
            'OR "BC Cancer" OR "PHSA" OR "Island Health" OR "Interior Health")'
        ),
        "location": "British Columbia, Canada",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI Health Authorities")
    listings = [_serp_job_to_listing(j, source="SerpAPI / BC Health Authorities") for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 5: SERPAPI — BC MUNICIPAL + POLICE
# ─────────────────────────────────────────────

def fetch_serpapi_municipal():
    print("\n📡 Fetching BC Municipal + Police via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": (
            '("product manager" OR "product owner" OR "director digital") '
            '("City of Vancouver" OR "Metro Vancouver" OR "Vancouver Police" '
            'OR "City of Burnaby" OR "City of Surrey" OR "TransLink")'
        ),
        "location": "British Columbia, Canada",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI Municipal")
    listings = [_serp_job_to_listing(j, source="SerpAPI / BC Municipal") for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 6: REMOTEOK
# ─────────────────────────────────────────────

def fetch_remoteok():
    print("\n📡 Fetching RemoteOK...")
    listings = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        response = requests.get(
            "https://remoteok.com/api",
            headers=headers, timeout=15
        )
        print(f"  RemoteOK status: {response.status_code}", end="")
        if response.status_code != 200:
            print()
            return []
        jobs = response.json()
        # Filter PM jobs
        pm_kw = ["product manager", "product owner", "head of product", "director of product"]
        senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head", "staff"]
        matched = 0
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = job.get("position", "").lower()
            tags = " ".join(job.get("tags", [])).lower()
            combined = title + " " + tags
            if not any(kw in combined for kw in pm_kw):
                continue
            if not any(kw in title for kw in senior_kw):
                continue
            matched += 1
            title = job.get("position", "")
            company = job.get("company", "Unknown")
            listings.append({
                "title": title,
                "company": company,
                "url": job.get("url", f"https://remoteok.com/l/{job.get('id','')}"),
                "linkedin_url": _make_linkedin_search_url(title, company),
                "location": "Remote",
                "salary": job.get("salary", "Not specified") or "Not specified",
                "date_posted": job.get("date", "Unknown"),
                "source": "RemoteOK",
                "description": job.get("description", "")[:1500],
            })
        print(f" — {len(jobs)} total, {matched} matched")
    except Exception as e:
        print(f"\n  ❌ RemoteOK error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 7: LEVER API
# FIX: Removed 404 slugs, corrected remaining ones
# ─────────────────────────────────────────────

LEVER_COMPANIES = [
    ("pointclickcare", "PointClickCare"),
    ("smiledigitalhealth", "Smile Digital Health"),
    ("includedhealth", "Included Health"),
    ("swordhealth", "Sword Health"),
    # Spring Health removed — fully moved to Greenhouse (springhealth66)
]

def fetch_lever_companies():
    print("\n📡 Fetching Lever company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_kw = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    for slug, company_name in LEVER_COMPANIES:
        try:
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            jobs = response.json()
            matched = 0
            for job in jobs:
                title = job.get("text", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                categories = job.get("categories", {})
                location = categories.get("location", "Not specified")
                matched += 1
                title = job.get("text", "")
                listings.append({
                    "title": title,
                    "company": company_name,
                    "url": job.get("hostedUrl", ""),
                    "linkedin_url": _make_linkedin_search_url(title, company_name),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": datetime.fromtimestamp(
                        job.get("createdAt", 0) / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d") if job.get("createdAt") else "Unknown",
                    "source": f"Lever / {company_name}",
                    "description": job.get("descriptionPlain", "")[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} error: {e}")

    print(f"  Total from Lever: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 8: GREENHOUSE API
# FIX: Correct slugs verified, both API endpoints supported
# ─────────────────────────────────────────────

# Uses boards-api.greenhouse.io
GREENHOUSE_COMPANIES = [
    ("alayacare", "AlayaCare"),
    ("springhealth66", "Spring Health"),
    ("hs", "Headspace"),
    ("woebot", "Woebot Health"),
    ("noomgrowth", "Noom"),
    ("dialoguehealthtechnologiesinc", "Dialogue"),
    ("prenuvo", "Prenuvo"),
]

# Uses job-boards.greenhouse.io (different endpoint)
GREENHOUSE_ALT_COMPANIES = [
    ("calm", "Calm"),
]

def fetch_greenhouse_companies():
    print("\n📡 Fetching Greenhouse company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_kw = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    # Standard endpoint
    for slug, company_name in GREENHOUSE_COMPANIES:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                location = job.get("location", {}).get("name", "Not specified")
                matched += 1
                job_title = job.get("title", "")
                listings.append({
                    "title": job_title,
                    "company": company_name,
                    "url": job.get("absolute_url", ""),
                    "linkedin_url": _make_linkedin_search_url(job_title, company_name),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("updated_at", "Unknown")[:10],
                    "source": f"Greenhouse / {company_name}",
                    "description": job.get("content", "")[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} error: {e}")

    # Alternative endpoint (job-boards.greenhouse.io)
    for slug, company_name in GREENHOUSE_ALT_COMPANIES:
        try:
            url = f"https://job-boards.greenhouse.io/api/v1/boards/{slug}/jobs?content=true"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name} (alt): HTTP {response.status_code}", end="")
            if response.status_code != 200:
                url2 = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
                response = requests.get(url2, headers=headers, timeout=20)
                print(f" → fallback HTTP {response.status_code}", end="")
                if response.status_code != 200:
                    print()
                    continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                location = job.get("location", {}).get("name", "Not specified")
                matched += 1
                job_title = job.get("title", "")
                listings.append({
                    "title": job_title,
                    "company": company_name,
                    "url": job.get("absolute_url", ""),
                    "linkedin_url": _make_linkedin_search_url(job_title, company_name),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("updated_at", "Unknown")[:10],
                    "source": f"Greenhouse / {company_name}",
                    "description": job.get("content", "")[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} error: {e}")

    print(f"  Total from Greenhouse: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 9: ASHBY API
# NEW: Hims & Hers moved from Lever to Ashby
# ─────────────────────────────────────────────

ASHBY_COMPANIES = [
    ("hims-and-hers", "Hims & Hers"),
]

def fetch_ashby_companies():
    print("\n📡 Fetching Ashby company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_kw = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    for slug, company_name in ASHBY_COMPANIES:
        try:
            # Ashby public API endpoint
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                location = job.get("location", "Not specified")
                if isinstance(location, dict):
                    location = location.get("name", "Not specified")
                matched += 1
                job_title = job.get("title", "")
                listings.append({
                    "title": job_title,
                    "company": company_name,
                    "url": job.get("jobUrl", ""),
                    "linkedin_url": _make_linkedin_search_url(job_title, company_name),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("publishedDate", "Unknown")[:10] if job.get("publishedDate") else "Unknown",
                    "source": f"Ashby / {company_name}",
                    "description": job.get("descriptionPlain", job.get("description", ""))[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} Ashby error: {e}")

    print(f"  Total from Ashby: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 10: BC TECH ASSOCIATION
# ─────────────────────────────────────────────

def fetch_bc_tech():
    print("\n📡 Fetching BC Tech job board...")
    listings = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        feed = feedparser.parse(
            "https://www.bctechnology.com/rss/jobs.cfm",
            request_headers=headers
        )
        print(f"  BC Tech RSS entries: {len(feed.entries)}", end="")
        matched = 0
        for entry in feed.entries[:30]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "product", "director", "vp", "lead", "digital", "design"
            ]):
                matched += 1
                company = entry.get("author", "Unknown")
                listings.append({
                    "title": title,
                    "company": company,
                    "url": entry.get("link", ""),
                    "linkedin_url": _make_linkedin_search_url(title, company),
                    "location": "British Columbia",
                    "salary": "Not specified",
                    "date_posted": entry.get("published", "Unknown"),
                    "source": "BC Tech Association",
                    "description": entry.get("summary", "")[:1500],
                })
        print(f" — {matched} matched")
    except Exception as e:
        print(f"\n  ❌ BC Tech error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 11: WELLFOUND
# ─────────────────────────────────────────────

def fetch_wellfound():
    print("\n📡 Fetching Wellfound...")
    listings = []
    try:
        feed = feedparser.parse(
            "https://wellfound.com/role/l/product-manager/canada-startups.rss"
        )
        print(f"  Wellfound RSS entries: {len(feed.entries)}", end="")
        matched = 0
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "senior", "lead", "director", "vp", "group", "principal", "head"
            ]):
                matched += 1
                company = entry.get("author", "Unknown")
                listings.append({
                    "title": title,
                    "company": company,
                    "url": entry.get("link", ""),
                    "linkedin_url": _make_linkedin_search_url(title, company),
                    "location": "Canada",
                    "salary": "Not specified",
                    "date_posted": entry.get("published", "Unknown"),
                    "source": "Wellfound",
                    "description": entry.get("summary", "")[:1500],
                })
        print(f" — {matched} matched")
    except Exception as e:
        print(f"\n  ❌ Wellfound error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# MAIN AGENT LOOP
# ─────────────────────────────────────────────

def run_agent():
    mode_label = "🧪 TEST MODE" if TEST_MODE else "🚀 PRODUCTION"
    print(f"\n{mode_label} — Job Search Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')} PT")
    print("=" * 60)

    print("\n🔍 Checking existing Notion entries for deduplication...")
    existing_urls = get_existing_urls()
    print(f"  Found {len(existing_urls)} existing entries in Notion")

    all_listings = []
    all_listings += fetch_serpapi_indeed()
    all_listings += fetch_serpapi_bc_gov()
    all_listings += fetch_serpapi_gc_jobs()
    all_listings += fetch_serpapi_health_authorities()
    all_listings += fetch_serpapi_municipal()
    all_listings += fetch_remoteok()
    all_listings += fetch_lever_companies()
    all_listings += fetch_greenhouse_companies()
    all_listings += fetch_ashby_companies()
    all_listings += fetch_bc_tech()
    all_listings += fetch_wellfound()

    print(f"\n📊 Total raw listings fetched: {len(all_listings)}")

    # Step 1: Location + description filter
    na_filtered = []
    location_rejected = 0
    for listing in all_listings:
        loc = listing.get("location", "")
        desc = listing.get("description", "")
        if not is_workable_location(loc):
            print(f"  🌍 Rejected (location): {listing['title']} @ {listing['company']} — {loc}")
            location_rejected += 1
        elif not is_workable_description(desc, loc):
            print(f"  🏢 Rejected (hybrid in description): {listing['title']} @ {listing['company']} — {loc}")
            location_rejected += 1
        else:
            na_filtered.append(listing)
    print(f"📊 After location filter: {len(na_filtered)} kept, {location_rejected} rejected")

    # Step 2: Deduplicate
    seen_urls = set()
    unique_listings = []
    for listing in na_filtered:
        url = listing.get("url", "")
        if url and url not in existing_urls and url not in seen_urls:
            seen_urls.add(url)
            unique_listings.append(listing)
    dupes = len(na_filtered) - len(unique_listings)
    print(f"📊 After dedup: {len(unique_listings)} new unique listings to score ({dupes} duplicates skipped)")

    # Step 3: Score and push
    pushed = 0
    excluded = 0
    errors = 0

    for i, listing in enumerate(unique_listings):
        print(f"\n[{i+1}/{len(unique_listings)}] {listing['title']} @ {listing['company']} ({listing['location']})")
        scored = score_job(
            title=listing["title"],
            description=listing["description"],
            company=listing["company"],
            salary=listing["salary"],
            location=listing["location"],
        )
        if not scored:
            errors += 1
            continue
        if scored.get("action") == "exclude":
            print(f"  ⛔ Excluded (score {scored.get('score')}): {scored.get('match_reason')}")
            excluded += 1
            continue
        push_to_notion(
            title=listing["title"],
            company=listing["company"],
            url=listing["url"],
            location=listing["location"],
            salary=listing["salary"],
            date_posted=listing["date_posted"],
            source=listing["source"],
            scored=scored,
            linkedin_url=listing.get("linkedin_url", ""),
        )
        pushed += 1

    print("\n" + "=" * 60)
    print(f"✅ Done!")
    print(f"   Sources: {len(all_listings)} fetched | {location_rejected} location-rejected | {dupes} dupes skipped")
    print(f"   Scoring: {len(unique_listings)} scored | {pushed} pushed | {excluded} excluded | {errors} errors")
    if not TEST_MODE:
        print(f"💰 Estimated API cost: ~${(pushed + excluded) * 0.001:.3f}")
    else:
        print(f"💰 TEST MODE — $0 spent on Claude or SerpAPI")

if __name__ == "__main__":
    run_agent()
# Changes: TEST_MODE off, cron 2pm PT, fromage=10 for first run

# ─────────────────────────────────────────────
# TEST MODE
# True  = no SerpAPI calls, no Claude calls, Notion push still runs
# False = full production run
# ─────────────────────────────────────────────

TEST_MODE = False  # ← set to True to test without spending API credits

# ─────────────────────────────────────────────
# PROFILE & SCORING CRITERIA
# ─────────────────────────────────────────────

SOFIA_PROFILE = """
Name: Sofia Blaunshteyn
Current location: Vancouver, BC, Canada

Background:
- 15+ years in tech: started as Android/Software Engineer (Samsung, Start.IO), transitioned to PM
- Lead PM at Match Group (GenAI features, 0→1 LLM agent with cross-session memory)
- Group PM at BC Government (led 18 engineers, rebuilt failing digital forms platform, $2.5M savings)
- Senior PM at Hello Heart (0→1 ML-powered mobile health feature, 1.5x engagement boost)
- PM Team Lead at Gett (ML marketplace algorithms, last-mile delivery, +70% daily deliveries)
- MBA, Tel Aviv University; BSc Computer Science

Core strengths: 0→1 product builds, AI/ML/GenAI product ownership, platform strategy,
stakeholder management, cross-functional leadership, data-driven decisions, consumer + B2B

Target roles: Senior PM, Lead PM, Group PM, Director of Product, VP of Product
Minimum salary: CAD $120,000 (no ceiling)
Location: Remote Canada, remote US (if open to Canadian applicants), or hybrid/in-office in BC

Industry preferences (for scoring):
- TOP TIER: Healthtech, wellness, longevity, senior care
- SECOND TIER: Any consumer-facing product
- MEDIUM: Govtech, civic tech
- NEUTRAL: B2B SaaS, enterprise (score on other criteria)

Government role equivalents to treat as Senior PM:
Director of Digital Services, Senior Business Analyst (technology),
IT Project Manager (digital/product focus), Service Design Lead,
Director of Product and Technology
"""

SCORING_CRITERIA = """
Score this job listing for Sofia on a scale of 1-10 using these weighted criteria:

1. SCOPE & OWNERSHIP (highest weight, 35%):
   - Owns 0→1 or major product area (not incremental features) → high score
   - Direct impact on company-level metrics (growth, revenue, adoption) → high score
   - Opportunity to define strategy, not just execute roadmap → high score
   - Space to shape new product paradigms, not maintain legacy → high score

2. INDUSTRY FIT (25%):
   - Healthtech, wellness, longevity, senior care → top score
   - Consumer-facing product → second tier score
   - Govtech/civic tech → medium score
   - B2B SaaS/enterprise → neutral, score on other criteria

3. AI/ML/GENAI EXPOSURE (20%):
   - Meaningful work with GenAI, agents, personalization → high score
   - General AI/ML product work → medium score
   - No AI component → lower score

4. USER IMPACT & PROBLEM DEPTH (20%):
   - Solves real meaningful user problems (health, behavior change, social impact) → high score
   - Ability to influence end-to-end user journey → high score

HARD EXCLUSIONS — return score 0 and action "exclude" ONLY if one of these is clearly and explicitly true:
- Explicitly requires relocation (not just "office available")
- Explicitly states US citizenship required, US work permit required, US work visa required, or security clearance required
- Title is clearly below Senior PM level (e.g. "Product Manager", "Associate PM", "Junior PM") — NOT government equivalents
- Salary is explicitly stated in the job posting AND is below CAD $120,000
- US-based role that is explicitly NOT remote (e.g. "onsite only", "must work from our NYC office")
- Canadian role outside BC that is explicitly NOT remote (e.g. in-office only in Toronto, Ottawa, Montreal, Calgary)

KEEP — do NOT exclude these:
- Remote anywhere in Canada
- Remote US (open to or not excluding Canadian applicants)
- In-office or hybrid in Vancouver metro: Vancouver, Burnaby, Port Moody, Surrey, Richmond,
  North Vancouver, West Vancouver, Coquitlam, New Westminster, Langley, Maple Ridge
- Any role where location or remote policy is unclear — score it and flag in red_flags instead
- Government roles with equivalent titles (Director of Digital Services, Service Design Lead, etc.)

DO NOT exclude based on:
- Missing salary info (very common — just score it)
- Unclear or ambiguous location (score it, flag uncertainty in red_flags)
- Any doubt or ambiguity — when in doubt, score it and let Sofia decide

APPLY MODE RULES:
- Score 8-10 → action: "manual" (Sofia applies herself)
- Score 5-7 → action: "auto" (flag for auto-apply)
- Score 1-4 → action: "low" (visible in Notion but deprioritized)
- Score 0 → action: "exclude" (do not push to Notion)
"""

# ─────────────────────────────────────────────
# CLIENTS
# ─────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
notion = Client(auth=os.environ["NOTION_TOKEN"])
DB_ID = os.environ["NOTION_DATABASE_ID"]
SERP_API_KEY = os.environ.get("SERP_API", "")

# ─────────────────────────────────────────────
# LOCATION FILTER — North America allowlist
# Applied BEFORE Claude to save API calls
# ─────────────────────────────────────────────

NA_SIGNALS = [
    "canada", "canadian", "british columbia", "bc", "vancouver", "burnaby",
    "victoria", "surrey", "richmond", "coquitlam", "north vancouver",
    "west vancouver", "port moody", "new westminster", "langley", "maple ridge",
    "ontario", "toronto", "alberta", "calgary", "quebec", "montreal",
    "united states", "usa", "us", "new york", "san francisco", "seattle",
    "california", "texas", "remote", "north america", "anywhere",
    "not specified", "not disclosed",
]

NON_NA_SIGNALS = [
    "portugal", "porto", "lisbon", "uk", "united kingdom", "london",
    "germany", "berlin", "munich", "france", "paris", "spain", "madrid",
    "barcelona", "amsterdam", "netherlands", "sweden", "stockholm",
    "denmark", "copenhagen", "norway", "oslo", "finland", "helsinki",
    "ireland", "dublin", "australia", "sydney", "melbourne", "new zealand",
    "india", "bangalore", "mumbai", "singapore", "hong kong", "japan",
    "tokyo", "china", "beijing", "shanghai", "brazil", "mexico",
    "latin america", "apac", "emea", "europe", "asia",
]

US_WORK_AUTH_SIGNALS = [
    "must have a legal right to work in the united states",
    "legal right to work in the us",
    "authorized to work in the united states",
    "authorized to work in the us",
    "must be authorized to work in the us",
    "work authorization in the united states",
    "sponsorship will not be provided",
    "we do not sponsor",
    "unable to sponsor",
    "cannot sponsor",
    "no visa sponsorship",
    "visa sponsorship is not available",
    "sponsorship not available",
    "must be a us citizen",
    "us citizenship required",
    "eligible to work in the us without sponsorship",
    "without sponsorship",
]
US_CITIES = [
    "new york", "san francisco", "los angeles", "chicago", "boston",
    "austin", "seattle", "denver", "atlanta", "miami", "dallas",
    "portland", "philadelphia", "washington dc", "washington, dc",
    "minneapolis", "phoenix", "san diego", "nashville", "raleigh",
    "princeton", "new jersey", "nj, usa", "ny, usa", "ca, usa",
]
INOFFICE_SIGNALS = ["hybrid", "onsite", "on-site", "in office", "in-office", "days per week on-site", "days on-site", "days/week on-site"]

def is_workable_location(location: str) -> bool:
    """
    Stage 1: Reject non-North-America.
    Stage 2: Reject US in-office/hybrid (Sofia cannot commute from Vancouver).
    Keep: remote anywhere, BC office/hybrid, Canada, unknown.
    """
    if not location or location.strip() == "":
        return True
    loc = location.lower()
    # Stage 1: hard reject non-NA
    if any(signal in loc for signal in NON_NA_SIGNALS):
        return False
    # Stage 2: reject US city + hybrid/onsite without remote mention
    if "remote" not in loc:
        if any(city in loc for city in US_CITIES) and any(sig in loc for sig in INOFFICE_SIGNALS):
            return False
    return True

def is_workable_description(description: str, location: str) -> bool:
    """
    Catches two patterns buried in job description text:
    1. Hybrid/in-office + US city combination
    2. Explicit US work authorization requirements
    """
    if not description:
        return True
    desc = description.lower()
    loc = location.lower() if location else ""

    # Check 1: explicit US work authorization requirement — always reject
    if any(sig in desc for sig in US_WORK_AUTH_SIGNALS):
        return False

    # If location confirmed BC or Canada — skip hybrid check
    bc_signals = ["vancouver", "burnaby", "british columbia", " bc,", "victoria", "canada"]
    if any(sig in loc for sig in bc_signals):
        return True

    # If location explicitly remote — skip hybrid check
    if "remote" in loc:
        return True

    # Check 2: hybrid + US city in description
    has_inoffice = any(sig in desc for sig in INOFFICE_SIGNALS)
    has_us_city = any(city in desc for city in US_CITIES)
    if has_inoffice and has_us_city:
        if "fully remote" in desc or "100% remote" in desc or "remote-first" in desc:
            return True
        return False

    return True

# ─────────────────────────────────────────────
# DEDUPLICATION
# FIX: Updated for newer notion-client API
# ─────────────────────────────────────────────

def get_existing_urls():
    """Fetch all URLs already in Notion to avoid duplicates."""
    existing = set()
    try:
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {"database_id": DB_ID, "page_size": 100}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            for page in response["results"]:
                url_prop = page["properties"].get("URL", {})
                url = url_prop.get("url")
                if url:
                    existing.add(url)
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
    except Exception as e:
        print(f"  ⚠️  Warning: deduplication failed: {e}")
        print(f"  ⚠️  Continuing without deduplication — may see duplicates")
    return existing

# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────

FAKE_SCORE = {
    "score": 7,
    "action": "auto",
    "industry": "TEST MODE — Healthtech",
    "match_reason": "TEST MODE — not a real score",
    "why_you_fit": "TEST MODE — not a real assessment",
    "red_flags": "None",
    "job_description_summary": "TEST MODE — fake job description summary.",
    "company_summary": "TEST MODE — fake company summary.",
    "us_open_to_canadians": None,
}

def score_job(title, description, company, salary="Not specified", location="Not specified"):
    if TEST_MODE:
        print(f"  🧪 TEST MODE — skipping Claude, returning fake score")
        return FAKE_SCORE

    prompt = f"""You are a job relevance scorer. Score this job for the candidate below.
Return ONLY a valid JSON object — no preamble, no markdown fences, no extra text.

{SOFIA_PROFILE}

{SCORING_CRITERIA}

JOB TO SCORE:
Title: {title}
Company: {company}
Location: {location}
Salary: {salary}
Description: {description[:1500]}

Return exactly this JSON shape:
{{
  "score": <0-10 integer>,
  "action": "<exclude|low|auto|manual>",
  "industry": "<actual industry name, be specific>",
  "match_reason": "<one sentence why this matches or doesn't>",
  "why_you_fit": "<one sentence connecting Sofia's specific background to this role>",
  "red_flags": "<one sentence on any concerns, or 'None' if clean>",
  "job_description_summary": "<2 sentences: what product Sofia would be building>",
  "company_summary": "<2 sentences: what the company product does>",
  "us_open_to_canadians": <true|false|null>
}}"""

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        print(f"  ❌ Scoring error for {title}: {e}")
        return None

# ─────────────────────────────────────────────
# NOTION PUSH
# ─────────────────────────────────────────────

def push_to_notion(title, company, url, location, salary, date_posted, source, scored):
    try:
        notion.pages.create(
            parent={"database_id": DB_ID},
            properties={
                "Name": {"title": [{"text": {"content": title[:200]}}]},
                "Company": {"rich_text": [{"text": {"content": company[:200]}}]},
                "Company Description": {"rich_text": [{"text": {"content": scored.get("company_summary", "")[:500]}}]},
                "Job Description": {"rich_text": [{"text": {"content": scored.get("job_description_summary", "")[:500]}}]},
                "Industry": {"rich_text": [{"text": {"content": scored.get("industry", "")[:200]}}]},
                "Score": {"number": scored.get("score", 0)},
                "Apply Mode": {"select": {"name": scored.get("action", "low").title()}},
                "Match Reason": {"rich_text": [{"text": {"content": scored.get("match_reason", "")[:500]}}]},
                "Why You Fit": {"rich_text": [{"text": {"content": scored.get("why_you_fit", "")[:500]}}]},
                "Red Flags": {"rich_text": [{"text": {"content": scored.get("red_flags", "None")[:500]}}]},
                "Location": {"rich_text": [{"text": {"content": location[:200]}}]},
                "Salary": {"rich_text": [{"text": {"content": salary[:200]}}]},
                "Date Posted": {"rich_text": [{"text": {"content": date_posted[:100]}}]},
                "Source": {"rich_text": [{"text": {"content": source[:200]}}]},
                "URL": {"url": url},
                "Status": {"select": {"name": "New"}},
            }
        )
        print(f"  ✅ Pushed: {title} at {company} (score: {scored.get('score')})")
    except Exception as e:
        print(f"  ❌ Notion push error for {title}: {e}")

# ─────────────────────────────────────────────
# SERP API HELPERS
# ─────────────────────────────────────────────

SERP_PM_QUERY = (
    '"senior product manager" OR "lead product manager" OR '
    '"group product manager" OR "director of product" OR '
    '"head of product" OR "VP of product" OR '
    '"senior product owner" OR "lead product owner"'
)

SERP_GOV_QUERY = (
    '"senior product manager" OR "product owner" OR '
    '"director digital services" OR "service design lead" OR '
    '"IT project manager"'
)

FAKE_SERP_LISTINGS = [
    {
        "title": "Senior Product Manager — Digital Health (TEST)",
        "company": "Test Company Canada",
        "url": "https://example.com/job/test-1",
        "location": "Vancouver, BC",
        "salary": "CAD $140,000",
        "date_posted": datetime.now().strftime("%Y-%m-%d"),
        "source": "SerpAPI / Indeed Canada (TEST)",
        "description": "Fake listing in TEST_MODE. No real API call was made.",
    },
    {
        "title": "Lead Product Manager — AI Platform (TEST)",
        "company": "Test Healthtech Inc",
        "url": "https://example.com/job/test-2",
        "location": "Remote Canada",
        "salary": "Not specified",
        "date_posted": datetime.now().strftime("%Y-%m-%d"),
        "source": "SerpAPI / Indeed Canada (TEST)",
        "description": "Another fake listing. Flip TEST_MODE = False for real results.",
    },
]

def _serp_fetch(params, source_label):
    """Shared SerpAPI fetch helper with logging."""
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=20)
        print(f"  {source_label} status: {response.status_code}", end="")
        if response.status_code != 200:
            print(f" — error: {response.text[:100]}")
            return []
        data = response.json()
        jobs = data.get("jobs_results", [])
        print(f" — {len(jobs)} results")
        return jobs
    except Exception as e:
        print(f"\n  ❌ {source_label} error: {e}")
        return []

def _best_apply_url(job: dict) -> str:
    """
    Extract the best direct apply URL from a SerpAPI Google Jobs result.
    Priority: company ATS (Lever/Greenhouse/Ashby) > Indeed > LinkedIn > any > share_link.
    The share_link is a Google search URL, not a direct job link — never use it as primary.
    """
    apply_options = job.get("apply_options", [])
    if not apply_options:
        return job.get("share_link", "")

    # Priority tiers — prefer direct company ATS links
    tiers = [
        # Tier 1: company ATS — direct, trackable, dedup-friendly
        ["lever.co", "greenhouse.io", "ashbyhq.com", "workday.com",
         "smartrecruiters.com", "jobvite.com", "icims.com", "taleo.net"],
        # Tier 2: major job boards
        ["indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com"],
        # Tier 3: anything else
        [],
    ]
    for tier in tiers:
        for option in apply_options:
            link = option.get("link", "")
            if not tier:  # tier 3 — take first available
                return link
            if any(domain in link for domain in tier):
                return link

    return job.get("share_link", "")

def _serp_job_to_listing(job, company_override=None, location_override=None, source="SerpAPI"):
    detected = job.get("detected_extensions", {})
    url = _best_apply_url(job)
    return {
        "title": job.get("title", ""),
        "company": company_override or job.get("company_name", "Unknown"),
        "url": url,
        "location": location_override or job.get("location", "Not specified"),
        "salary": detected.get("salary", "Not specified"),
        "date_posted": detected.get("posted_at", "Unknown"),
        "source": source,
        "description": job.get("description", "")[:1500],
    }

# ─────────────────────────────────────────────
# SOURCE 1: SERPAPI — INDEED CANADA
# ─────────────────────────────────────────────

def fetch_serpapi_indeed():
    print("\n📡 Fetching Indeed Canada via SerpAPI...")
    if TEST_MODE:
        print(f"  🧪 TEST MODE — returning {len(FAKE_SERP_LISTINGS)} fake listings")
        return FAKE_SERP_LISTINGS
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": SERP_PM_QUERY,
        "location": "Canada",
        "gl": "ca",
        "hl": "en",
        "chips": "date_posted:month",  # ← first run: month covers 10+ days. Change to "date_posted:today" after first run
        "api_key": SERP_API_KEY,
    }, "SerpAPI Indeed Canada")
    listings = [_serp_job_to_listing(j, source="SerpAPI / Indeed Canada") for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 2: SERPAPI — BC PUBLIC SERVICE
# ─────────────────────────────────────────────

def fetch_serpapi_bc_gov():
    print("\n📡 Fetching BC Public Service via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": f"{SERP_GOV_QUERY} site:bcpublicservice.ca",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI BC Gov")
    listings = [_serp_job_to_listing(j, company_override="BC Public Service",
                location_override="British Columbia", source="SerpAPI / BC Public Service")
                for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 3: SERPAPI — GOVERNMENT OF CANADA
# ─────────────────────────────────────────────

def fetch_serpapi_gc_jobs():
    print("\n📡 Fetching Government of Canada via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": f"{SERP_GOV_QUERY} site:jobs-emplois.gc.ca",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI GC Jobs")
    listings = [_serp_job_to_listing(j, company_override="Government of Canada",
                location_override="Canada (remote eligible)", source="SerpAPI / GC Jobs")
                for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 4: SERPAPI — BC HEALTH AUTHORITIES
# ─────────────────────────────────────────────

def fetch_serpapi_health_authorities():
    print("\n📡 Fetching BC Health Authorities via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": (
            '("product manager" OR "product owner" OR "director digital") '
            '("Fraser Health" OR "Vancouver Coastal Health" OR "Providence Health" '
            'OR "BC Cancer" OR "PHSA" OR "Island Health" OR "Interior Health")'
        ),
        "location": "British Columbia, Canada",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI Health Authorities")
    listings = [_serp_job_to_listing(j, source="SerpAPI / BC Health Authorities") for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 5: SERPAPI — BC MUNICIPAL + POLICE
# ─────────────────────────────────────────────

def fetch_serpapi_municipal():
    print("\n📡 Fetching BC Municipal + Police via SerpAPI...")
    if TEST_MODE:
        print("  🧪 TEST MODE — skipping")
        return []
    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []
    jobs = _serp_fetch({
        "engine": "google_jobs",
        "q": (
            '("product manager" OR "product owner" OR "director digital") '
            '("City of Vancouver" OR "Metro Vancouver" OR "Vancouver Police" '
            'OR "City of Burnaby" OR "City of Surrey" OR "TransLink")'
        ),
        "location": "British Columbia, Canada",
        "gl": "ca", "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }, "SerpAPI Municipal")
    listings = [_serp_job_to_listing(j, source="SerpAPI / BC Municipal") for j in jobs]
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 6: REMOTEOK
# ─────────────────────────────────────────────

def fetch_remoteok():
    print("\n📡 Fetching RemoteOK...")
    listings = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        response = requests.get(
            "https://remoteok.com/api",
            headers=headers, timeout=15
        )
        print(f"  RemoteOK status: {response.status_code}", end="")
        if response.status_code != 200:
            print()
            return []
        jobs = response.json()
        # Filter PM jobs
        pm_kw = ["product manager", "product owner", "head of product", "director of product"]
        senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head", "staff"]
        matched = 0
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = job.get("position", "").lower()
            tags = " ".join(job.get("tags", [])).lower()
            combined = title + " " + tags
            if not any(kw in combined for kw in pm_kw):
                continue
            if not any(kw in title for kw in senior_kw):
                continue
            matched += 1
            listings.append({
                "title": job.get("position", ""),
                "company": job.get("company", "Unknown"),
                "url": job.get("url", f"https://remoteok.com/l/{job.get('id','')}"),
                "location": "Remote",
                "salary": job.get("salary", "Not specified") or "Not specified",
                "date_posted": job.get("date", "Unknown"),
                "source": "RemoteOK",
                "description": job.get("description", "")[:1500],
            })
        print(f" — {len(jobs)} total, {matched} matched")
    except Exception as e:
        print(f"\n  ❌ RemoteOK error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 7: LEVER API
# FIX: Removed 404 slugs, corrected remaining ones
# ─────────────────────────────────────────────

LEVER_COMPANIES = [
    ("pointclickcare", "PointClickCare"),
    ("smiledigitalhealth", "Smile Digital Health"),
    ("includedhealth", "Included Health"),
    ("swordhealth", "Sword Health"),
    # Spring Health removed — fully moved to Greenhouse (springhealth66)
]

def fetch_lever_companies():
    print("\n📡 Fetching Lever company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_kw = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    for slug, company_name in LEVER_COMPANIES:
        try:
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            jobs = response.json()
            matched = 0
            for job in jobs:
                title = job.get("text", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                categories = job.get("categories", {})
                location = categories.get("location", "Not specified")
                matched += 1
                listings.append({
                    "title": job.get("text", ""),
                    "company": company_name,
                    "url": job.get("hostedUrl", ""),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": datetime.fromtimestamp(
                        job.get("createdAt", 0) / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d") if job.get("createdAt") else "Unknown",
                    "source": f"Lever / {company_name}",
                    "description": job.get("descriptionPlain", "")[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} error: {e}")

    print(f"  Total from Lever: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 8: GREENHOUSE API
# FIX: Correct slugs verified, both API endpoints supported
# ─────────────────────────────────────────────

# Uses boards-api.greenhouse.io
GREENHOUSE_COMPANIES = [
    ("alayacare", "AlayaCare"),
    ("springhealth66", "Spring Health"),
    ("hs", "Headspace"),
    ("woebot", "Woebot Health"),
    ("noomgrowth", "Noom"),
    ("dialoguehealthtechnologiesinc", "Dialogue"),
    ("prenuvo", "Prenuvo"),
]

# Uses job-boards.greenhouse.io (different endpoint)
GREENHOUSE_ALT_COMPANIES = [
    ("calm", "Calm"),
]

def fetch_greenhouse_companies():
    print("\n📡 Fetching Greenhouse company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_kw = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    # Standard endpoint
    for slug, company_name in GREENHOUSE_COMPANIES:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                location = job.get("location", {}).get("name", "Not specified")
                matched += 1
                listings.append({
                    "title": job.get("title", ""),
                    "company": company_name,
                    "url": job.get("absolute_url", ""),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("updated_at", "Unknown")[:10],
                    "source": f"Greenhouse / {company_name}",
                    "description": job.get("content", "")[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} error: {e}")

    # Alternative endpoint (job-boards.greenhouse.io)
    for slug, company_name in GREENHOUSE_ALT_COMPANIES:
        try:
            url = f"https://job-boards.greenhouse.io/api/v1/boards/{slug}/jobs?content=true"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name} (alt): HTTP {response.status_code}", end="")
            if response.status_code != 200:
                # Try standard endpoint as fallback
                url2 = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
                response = requests.get(url2, headers=headers, timeout=20)
                print(f" → fallback HTTP {response.status_code}", end="")
                if response.status_code != 200:
                    print()
                    continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                location = job.get("location", {}).get("name", "Not specified")
                matched += 1
                listings.append({
                    "title": job.get("title", ""),
                    "company": company_name,
                    "url": job.get("absolute_url", ""),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("updated_at", "Unknown")[:10],
                    "source": f"Greenhouse / {company_name}",
                    "description": job.get("content", "")[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} error: {e}")

    print(f"  Total from Greenhouse: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 9: ASHBY API
# NEW: Hims & Hers moved from Lever to Ashby
# ─────────────────────────────────────────────

ASHBY_COMPANIES = [
    ("hims-and-hers", "Hims & Hers"),
]

def fetch_ashby_companies():
    print("\n📡 Fetching Ashby company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_kw = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_kw = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    for slug, company_name in ASHBY_COMPANIES:
        try:
            # Ashby public API endpoint
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            response = requests.get(url, headers=headers, timeout=20)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_kw):
                    continue
                if not any(kw in title for kw in senior_kw):
                    continue
                location = job.get("location", "Not specified")
                if isinstance(location, dict):
                    location = location.get("name", "Not specified")
                matched += 1
                listings.append({
                    "title": job.get("title", ""),
                    "company": company_name,
                    "url": job.get("jobUrl", ""),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("publishedDate", "Unknown")[:10] if job.get("publishedDate") else "Unknown",
                    "source": f"Ashby / {company_name}",
                    "description": job.get("descriptionPlain", job.get("description", ""))[:1500],
                })
            print(f" → {len(jobs)} total, {matched} matched")
        except Exception as e:
            print(f"\n  ❌ {company_name} Ashby error: {e}")

    print(f"  Total from Ashby: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 10: BC TECH ASSOCIATION
# ─────────────────────────────────────────────

def fetch_bc_tech():
    print("\n📡 Fetching BC Tech job board...")
    listings = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        feed = feedparser.parse(
            "https://www.bctechnology.com/rss/jobs.cfm",
            request_headers=headers
        )
        print(f"  BC Tech RSS entries: {len(feed.entries)}", end="")
        matched = 0
        for entry in feed.entries[:30]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "product", "director", "vp", "lead", "digital", "design"
            ]):
                matched += 1
                listings.append({
                    "title": title,
                    "company": entry.get("author", "Unknown"),
                    "url": entry.get("link", ""),
                    "location": "British Columbia",
                    "salary": "Not specified",
                    "date_posted": entry.get("published", "Unknown"),
                    "source": "BC Tech Association",
                    "description": entry.get("summary", "")[:1500],
                })
        print(f" — {matched} matched")
    except Exception as e:
        print(f"\n  ❌ BC Tech error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 11: WELLFOUND
# ─────────────────────────────────────────────

def fetch_wellfound():
    print("\n📡 Fetching Wellfound...")
    listings = []
    try:
        feed = feedparser.parse(
            "https://wellfound.com/role/l/product-manager/canada-startups.rss"
        )
        print(f"  Wellfound RSS entries: {len(feed.entries)}", end="")
        matched = 0
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "senior", "lead", "director", "vp", "group", "principal", "head"
            ]):
                matched += 1
                listings.append({
                    "title": title,
                    "company": entry.get("author", "Unknown"),
                    "url": entry.get("link", ""),
                    "location": "Canada",
                    "salary": "Not specified",
                    "date_posted": entry.get("published", "Unknown"),
                    "source": "Wellfound",
                    "description": entry.get("summary", "")[:1500],
                })
        print(f" — {matched} matched")
    except Exception as e:
        print(f"\n  ❌ Wellfound error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# MAIN AGENT LOOP
# ─────────────────────────────────────────────

def run_agent():
    mode_label = "🧪 TEST MODE" if TEST_MODE else "🚀 PRODUCTION"
    print(f"\n{mode_label} — Job Search Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')} PT")
    print("=" * 60)

    print("\n🔍 Checking existing Notion entries for deduplication...")
    existing_urls = get_existing_urls()
    print(f"  Found {len(existing_urls)} existing entries in Notion")

    all_listings = []
    all_listings += fetch_serpapi_indeed()
    all_listings += fetch_serpapi_bc_gov()
    all_listings += fetch_serpapi_gc_jobs()
    all_listings += fetch_serpapi_health_authorities()
    all_listings += fetch_serpapi_municipal()
    all_listings += fetch_remoteok()
    all_listings += fetch_lever_companies()
    all_listings += fetch_greenhouse_companies()
    all_listings += fetch_ashby_companies()
    all_listings += fetch_bc_tech()
    all_listings += fetch_wellfound()

    print(f"\n📊 Total raw listings fetched: {len(all_listings)}")

    # Step 1: Location + description filter
    na_filtered = []
    location_rejected = 0
    for listing in all_listings:
        loc = listing.get("location", "")
        desc = listing.get("description", "")
        if not is_workable_location(loc):
            print(f"  🌍 Rejected (location): {listing['title']} @ {listing['company']} — {loc}")
            location_rejected += 1
        elif not is_workable_description(desc, loc):
            print(f"  🏢 Rejected (hybrid in description): {listing['title']} @ {listing['company']} — {loc}")
            location_rejected += 1
        else:
            na_filtered.append(listing)
    print(f"📊 After location filter: {len(na_filtered)} kept, {location_rejected} rejected")

    # Step 2: Deduplicate
    seen_urls = set()
    unique_listings = []
    for listing in na_filtered:
        url = listing.get("url", "")
        if url and url not in existing_urls and url not in seen_urls:
            seen_urls.add(url)
            unique_listings.append(listing)
    dupes = len(na_filtered) - len(unique_listings)
    print(f"📊 After dedup: {len(unique_listings)} new unique listings to score ({dupes} duplicates skipped)")

    # Step 3: Score and push
    pushed = 0
    excluded = 0
    errors = 0

    for i, listing in enumerate(unique_listings):
        print(f"\n[{i+1}/{len(unique_listings)}] {listing['title']} @ {listing['company']} ({listing['location']})")
        scored = score_job(
            title=listing["title"],
            description=listing["description"],
            company=listing["company"],
            salary=listing["salary"],
            location=listing["location"],
        )
        if not scored:
            errors += 1
            continue
        if scored.get("action") == "exclude":
            print(f"  ⛔ Excluded (score {scored.get('score')}): {scored.get('match_reason')}")
            excluded += 1
            continue
        push_to_notion(
            title=listing["title"],
            company=listing["company"],
            url=listing["url"],
            location=listing["location"],
            salary=listing["salary"],
            date_posted=listing["date_posted"],
            source=listing["source"],
            scored=scored,
        )
        pushed += 1

    print("\n" + "=" * 60)
    print(f"✅ Done!")
    print(f"   Sources: {len(all_listings)} fetched | {location_rejected} location-rejected | {dupes} dupes skipped")
    print(f"   Scoring: {len(unique_listings)} scored | {pushed} pushed | {excluded} excluded | {errors} errors")
    if not TEST_MODE:
        print(f"💰 Estimated API cost: ~${(pushed + excluded) * 0.001:.3f}")
    else:
        print(f"💰 TEST MODE — $0 spent on Claude or SerpAPI")

if __name__ == "__main__":
    run_agent()
