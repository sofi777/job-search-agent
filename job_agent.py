import anthropic
import json
import requests
import feedparser
from datetime import datetime, timezone
from notion_client import Client
import os
# VERSION 3 — 2026-04-10
# Changes: TEST_MODE, SerpAPI Indeed + gov sources, NA location filter, verbose logging

# ─────────────────────────────────────────────
# TEST MODE
# True  = no SerpAPI calls, no Claude calls, Notion push still runs
# False = full production run
# ─────────────────────────────────────────────

TEST_MODE = True  # ← flip to False when ready for real run

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
# Applied in Python BEFORE calling Claude
# Saves API calls on clearly non-NA jobs
# ─────────────────────────────────────────────

# Signals that confirm North America
NA_SIGNALS = [
    "canada", "canadian", "british columbia", "bc", "vancouver", "burnaby",
    "victoria", "surrey", "richmond", "coquitlam", "north vancouver",
    "west vancouver", "port moody", "new westminster", "langley", "maple ridge",
    "ontario", "toronto", "alberta", "calgary", "quebec", "montreal",
    "united states", "usa", "us", "new york", "san francisco", "seattle",
    "california", "texas", "remote", "north america", "anywhere",
    "not specified", "not disclosed",
]

# Signals that confirm non-North America — reject immediately
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

def is_north_america(location: str) -> bool:
    """
    Returns True if location is confirmed NA or unknown (pass to Claude).
    Returns False only if location explicitly signals non-NA.
    """
    if not location or location.strip() == "":
        return True  # unknown — pass to Claude to flag
    loc = location.lower()
    # Reject if any non-NA signal found
    if any(signal in loc for signal in NON_NA_SIGNALS):
        return False
    # Accept if any NA signal found
    if any(signal in loc for signal in NA_SIGNALS):
        return True
    # Unknown location — pass to Claude
    return True

# ─────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────

def get_existing_urls():
    """Fetch all URLs already in the Notion database to avoid duplicates."""
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
        print(f"  ⚠️  Warning: could not fetch existing URLs: {e}")
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
    "job_description_summary": "TEST MODE — this is a fake job description summary.",
    "company_summary": "TEST MODE — this is a fake company summary.",
    "us_open_to_canadians": None,
}

def score_job(title, description, company, salary="Not specified", location="Not specified"):
    """Score a job listing using Claude Haiku. Returns fake score in TEST_MODE."""
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
    """Push a scored job to the Notion database."""
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
# SOURCE 1: SERPAPI — INDEED CANADA
# Replaces direct Indeed RSS (blocked on GitHub IPs)
# ─────────────────────────────────────────────

SERP_JOB_QUERY = (
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
        "description": "This is a fake job listing generated in TEST_MODE. No real API call was made.",
    },
    {
        "title": "Lead Product Manager — AI Platform (TEST)",
        "company": "Test Healthtech Inc",
        "url": "https://example.com/job/test-2",
        "location": "Remote Canada",
        "salary": "Not specified",
        "date_posted": datetime.now().strftime("%Y-%m-%d"),
        "source": "SerpAPI / Indeed Canada (TEST)",
        "description": "Another fake listing in TEST_MODE. Flip TEST_MODE = False for real results.",
    },
]

def fetch_serpapi_indeed():
    """Fetch Indeed Canada jobs via SerpAPI. Returns fake data in TEST_MODE."""
    print("\n📡 Fetching Indeed Canada via SerpAPI...")

    if TEST_MODE:
        print(f"  🧪 TEST MODE — returning {len(FAKE_SERP_LISTINGS)} fake listings")
        return FAKE_SERP_LISTINGS

    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []

    listings = []
    params_list = [
        {
            "engine": "google_jobs",
            "q": SERP_JOB_QUERY,
            "location": "Canada",
            "gl": "ca",
            "hl": "en",
            "chips": "date_posted:week",
            "api_key": SERP_API_KEY,
        },
    ]

    for params in params_list:
        try:
            response = requests.get(
                "https://serpapi.com/search",
                params=params,
                timeout=15
            )
            print(f"  SerpAPI Indeed status: {response.status_code}")
            if response.status_code != 200:
                print(f"  ⚠️  SerpAPI error: {response.text[:200]}")
                continue
            data = response.json()
            jobs = data.get("jobs_results", [])
            print(f"  Raw results returned: {len(jobs)}")
            for job in jobs:
                location = job.get("location", "Not specified")
                detected_via = job.get("detected_extensions", {})
                salary = detected_via.get("salary", "Not specified")
                date_posted = detected_via.get("posted_at", "Unknown")
                listings.append({
                    "title": job.get("title", ""),
                    "company": job.get("company_name", "Unknown"),
                    "url": job.get("share_link", job.get("related_links", [{}])[0].get("link", "")),
                    "location": location,
                    "salary": salary,
                    "date_posted": date_posted,
                    "source": "SerpAPI / Indeed Canada",
                    "description": job.get("description", "")[:1500],
                })
        except Exception as e:
            print(f"  ❌ SerpAPI Indeed error: {e}")

    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 2: SERPAPI — BC PUBLIC SERVICE
# ─────────────────────────────────────────────

def fetch_serpapi_bc_gov():
    """Fetch BC Public Service jobs via SerpAPI."""
    print("\n📡 Fetching BC Public Service via SerpAPI...")

    if TEST_MODE:
        print(f"  🧪 TEST MODE — skipping SerpAPI call")
        return []

    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []

    listings = []
    params = {
        "engine": "google_jobs",
        "q": f"{SERP_GOV_QUERY} site:bcpublicservice.ca",
        "gl": "ca",
        "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        print(f"  SerpAPI BC Gov status: {response.status_code}")
        if response.status_code != 200:
            print(f"  ⚠️  SerpAPI error: {response.text[:200]}")
            return []
        data = response.json()
        jobs = data.get("jobs_results", [])
        print(f"  Raw results returned: {len(jobs)}")
        for job in jobs:
            detected_via = job.get("detected_extensions", {})
            listings.append({
                "title": job.get("title", ""),
                "company": "BC Public Service",
                "url": job.get("share_link", "https://www.bcpublicservice.ca/careers/"),
                "location": job.get("location", "British Columbia"),
                "salary": detected_via.get("salary", "As per BC Government pay grid"),
                "date_posted": detected_via.get("posted_at", "Unknown"),
                "source": "SerpAPI / BC Public Service",
                "description": job.get("description", "")[:1500],
            })
    except Exception as e:
        print(f"  ❌ SerpAPI BC Gov error: {e}")

    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 3: SERPAPI — GOVERNMENT OF CANADA
# ─────────────────────────────────────────────

def fetch_serpapi_gc_jobs():
    """Fetch Government of Canada jobs via SerpAPI."""
    print("\n📡 Fetching Government of Canada via SerpAPI...")

    if TEST_MODE:
        print(f"  🧪 TEST MODE — skipping SerpAPI call")
        return []

    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []

    listings = []
    params = {
        "engine": "google_jobs",
        "q": f"{SERP_GOV_QUERY} site:jobs-emplois.gc.ca",
        "gl": "ca",
        "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        print(f"  SerpAPI GC Jobs status: {response.status_code}")
        if response.status_code != 200:
            print(f"  ⚠️  SerpAPI error: {response.text[:200]}")
            return []
        data = response.json()
        jobs = data.get("jobs_results", [])
        print(f"  Raw results returned: {len(jobs)}")
        for job in jobs:
            detected_via = job.get("detected_extensions", {})
            listings.append({
                "title": job.get("title", ""),
                "company": "Government of Canada",
                "url": job.get("share_link", "https://jobs-emplois.gc.ca/"),
                "location": job.get("location", "Canada (remote eligible)"),
                "salary": detected_via.get("salary", "As per GC pay grid"),
                "date_posted": detected_via.get("posted_at", "Unknown"),
                "source": "SerpAPI / GC Jobs",
                "description": job.get("description", "")[:1500],
            })
    except Exception as e:
        print(f"  ❌ SerpAPI GC Jobs error: {e}")

    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 4: SERPAPI — BC HEALTH AUTHORITIES
# ─────────────────────────────────────────────

def fetch_serpapi_health_authorities():
    """Fetch BC Health Authority jobs via SerpAPI."""
    print("\n📡 Fetching BC Health Authorities via SerpAPI...")

    if TEST_MODE:
        print(f"  🧪 TEST MODE — skipping SerpAPI call")
        return []

    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []

    listings = []
    params = {
        "engine": "google_jobs",
        "q": (
            '("product manager" OR "product owner" OR "director digital") '
            '("Fraser Health" OR "Vancouver Coastal Health" OR "Providence Health" '
            'OR "BC Cancer" OR "PHSA" OR "Island Health" OR "Interior Health")'
        ),
        "location": "British Columbia, Canada",
        "gl": "ca",
        "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        print(f"  SerpAPI Health Authorities status: {response.status_code}")
        if response.status_code != 200:
            print(f"  ⚠️  SerpAPI error: {response.text[:200]}")
            return []
        data = response.json()
        jobs = data.get("jobs_results", [])
        print(f"  Raw results returned: {len(jobs)}")
        for job in jobs:
            detected_via = job.get("detected_extensions", {})
            listings.append({
                "title": job.get("title", ""),
                "company": job.get("company_name", "BC Health Authority"),
                "url": job.get("share_link", ""),
                "location": job.get("location", "British Columbia"),
                "salary": detected_via.get("salary", "Not specified"),
                "date_posted": detected_via.get("posted_at", "Unknown"),
                "source": "SerpAPI / BC Health Authorities",
                "description": job.get("description", "")[:1500],
            })
    except Exception as e:
        print(f"  ❌ SerpAPI Health Authorities error: {e}")

    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 5: SERPAPI — BC MUNICIPAL + POLICE
# ─────────────────────────────────────────────

def fetch_serpapi_municipal():
    """Fetch BC Municipal and Police jobs via SerpAPI."""
    print("\n📡 Fetching BC Municipal + Police via SerpAPI...")

    if TEST_MODE:
        print(f"  🧪 TEST MODE — skipping SerpAPI call")
        return []

    if not SERP_API_KEY:
        print("  ⚠️  SERP_API secret not set — skipping")
        return []

    listings = []
    params = {
        "engine": "google_jobs",
        "q": (
            '("product manager" OR "product owner" OR "director digital") '
            '("City of Vancouver" OR "Metro Vancouver" OR "Vancouver Police" '
            'OR "City of Burnaby" OR "City of Surrey" OR "TransLink")'
        ),
        "location": "British Columbia, Canada",
        "gl": "ca",
        "hl": "en",
        "chips": "date_posted:month",
        "api_key": SERP_API_KEY,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        print(f"  SerpAPI Municipal status: {response.status_code}")
        if response.status_code != 200:
            print(f"  ⚠️  SerpAPI error: {response.text[:200]}")
            return []
        data = response.json()
        jobs = data.get("jobs_results", [])
        print(f"  Raw results returned: {len(jobs)}")
        for job in jobs:
            detected_via = job.get("detected_extensions", {})
            listings.append({
                "title": job.get("title", ""),
                "company": job.get("company_name", "BC Municipality"),
                "url": job.get("share_link", ""),
                "location": job.get("location", "British Columbia"),
                "salary": detected_via.get("salary", "Not specified"),
                "date_posted": detected_via.get("posted_at", "Unknown"),
                "source": "SerpAPI / BC Municipal",
                "description": job.get("description", "")[:1500],
            })
    except Exception as e:
        print(f"  ❌ SerpAPI Municipal error: {e}")

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
            "https://remoteok.com/remote-product-manager-jobs.json",
            headers=headers,
            timeout=10
        )
        print(f"  RemoteOK status: {response.status_code}")
        jobs = response.json()
        print(f"  Raw results returned: {len(jobs) - 1}")
        for job in jobs[1:30]:
            if not isinstance(job, dict):
                continue
            title = job.get("position", "")
            if not any(kw in title.lower() for kw in [
                "senior", "lead", "group", "director", "vp", "principal", "head"
            ]):
                continue
            listings.append({
                "title": title,
                "company": job.get("company", "Unknown"),
                "url": job.get("url", ""),
                "location": "Remote",
                "salary": job.get("salary", "Not specified") or "Not specified",
                "date_posted": job.get("date", "Unknown"),
                "source": "RemoteOK",
                "description": job.get("description", "")[:1500],
            })
    except Exception as e:
        print(f"  ❌ RemoteOK error: {e}")
    print(f"  Found {len(listings)} listings after title filter")
    return listings

# ─────────────────────────────────────────────
# SOURCE 7: LEVER API (COMPANY CAREER PAGES)
# ─────────────────────────────────────────────

LEVER_COMPANIES = [
    ("pointclickcare", "PointClickCare"),
    ("smiledigitalhealth", "Smile Digital Health"),
    ("alayacare", "AlayaCare"),
    ("dialogue", "Dialogue"),
    ("includedhealth", "Included Health"),
    ("springhealth", "Spring Health"),
    ("headspace", "Headspace"),
    ("calm", "Calm"),
    ("swordhealth", "Sword Health"),
    ("noom", "Noom"),
    ("woebothealth", "Woebot Health"),
    ("forhims", "Hims & Hers"),
    ("brightsidehealth", "Brightside Health"),
]

def fetch_lever_companies():
    print("\n📡 Fetching Lever company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_keywords = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_keywords = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    for slug, company_name in LEVER_COMPANIES:
        try:
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            response = requests.get(url, headers=headers, timeout=10)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            jobs = response.json()
            matched = 0
            for job in jobs:
                title = job.get("text", "").lower()
                if not any(kw in title for kw in pm_keywords):
                    continue
                if not any(kw in title for kw in senior_keywords):
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
            print(f"\n  ❌ {company_name} Lever error: {e}")

    print(f"  Total from Lever: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 8: GREENHOUSE API (COMPANY CAREER PAGES)
# ─────────────────────────────────────────────

GREENHOUSE_COMPANIES = [
    ("prenuvo", "Prenuvo"),
    ("wellhealth", "WELL Health"),
    ("carebook", "Carebook"),
    ("metaoptima", "MetaOptima"),
    ("brightsidehealth", "Brightside Health"),
    ("kiihealth", "Kii Health"),
    ("thrive-health", "Thrive Health"),
    ("mapleca", "Maple"),
]

def fetch_greenhouse_companies():
    print("\n📡 Fetching Greenhouse company career pages...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
    senior_keywords = ["senior", "lead", "group", "director", "vp", "principal", "head of", "staff"]
    pm_keywords = [
        "product manager", "product lead", "head of product",
        "director of product", "vp of product", "vp product", "product owner"
    ]

    for slug, company_name in GREENHOUSE_COMPANIES:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            response = requests.get(url, headers=headers, timeout=10)
            print(f"  {company_name}: HTTP {response.status_code}", end="")
            if response.status_code != 200:
                print()
                continue
            data = response.json()
            jobs = data.get("jobs", [])
            matched = 0
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_keywords):
                    continue
                if not any(kw in title for kw in senior_keywords):
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
            print(f"\n  ❌ {company_name} Greenhouse error: {e}")

    print(f"  Total from Greenhouse: {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 9: BC TECH ASSOCIATION
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
        print(f"  BC Tech RSS entries: {len(feed.entries)}")
        for entry in feed.entries[:30]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "product", "director", "vp", "lead", "digital", "design"
            ]):
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
    except Exception as e:
        print(f"  ❌ BC Tech error: {e}")
    print(f"  Found {len(listings)} listings after title filter")
    return listings

# ─────────────────────────────────────────────
# SOURCE 10: WELLFOUND
# ─────────────────────────────────────────────

def fetch_wellfound():
    print("\n📡 Fetching Wellfound...")
    listings = []
    try:
        feed = feedparser.parse(
            "https://wellfound.com/role/l/product-manager/canada-startups.rss"
        )
        print(f"  Wellfound RSS entries: {len(feed.entries)}")
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "senior", "lead", "director", "vp", "group", "principal", "head"
            ]):
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
    except Exception as e:
        print(f"  ❌ Wellfound error: {e}")
    print(f"  Found {len(listings)} listings after title filter")
    return listings

# ─────────────────────────────────────────────
# MAIN AGENT LOOP
# ─────────────────────────────────────────────

def run_agent():
    mode_label = "🧪 TEST MODE" if TEST_MODE else "🚀 PRODUCTION"
    print(f"\n{mode_label} — Job Search Agent starting — {datetime.now().strftime('%Y-%m-%d %H:%M')} PT")
    print("=" * 60)

    # Get existing URLs to avoid duplicates
    print("\n🔍 Checking existing Notion entries for deduplication...")
    existing_urls = get_existing_urls()
    print(f"  Found {len(existing_urls)} existing entries in Notion")

    # Fetch from all sources
    all_listings = []
    all_listings += fetch_serpapi_indeed()
    all_listings += fetch_serpapi_bc_gov()
    all_listings += fetch_serpapi_gc_jobs()
    all_listings += fetch_serpapi_health_authorities()
    all_listings += fetch_serpapi_municipal()
    all_listings += fetch_remoteok()
    all_listings += fetch_lever_companies()
    all_listings += fetch_greenhouse_companies()
    all_listings += fetch_bc_tech()
    all_listings += fetch_wellfound()

    print(f"\n📊 Total raw listings fetched: {len(all_listings)}")

    # Step 1: Location filter — North America only
    na_filtered = []
    location_rejected = 0
    for listing in all_listings:
        if is_north_america(listing.get("location", "")):
            na_filtered.append(listing)
        else:
            print(f"  🌍 Location rejected: {listing['title']} @ {listing['company']} — {listing['location']}")
            location_rejected += 1
    print(f"📊 After NA location filter: {len(na_filtered)} kept, {location_rejected} rejected")

    # Step 2: Deduplicate by URL
    seen_urls = set()
    unique_listings = []
    for listing in na_filtered:
        url = listing.get("url", "")
        if url and url not in existing_urls and url not in seen_urls:
            seen_urls.add(url)
            unique_listings.append(listing)
    print(f"📊 After deduplication: {len(unique_listings)} new unique listings to score")

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
            print(f"  ⛔ Excluded (score: {scored.get('score')}): {scored.get('match_reason')}")
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
    print(f"   Fetched: {len(all_listings)} | NA filtered out: {location_rejected} | Duplicates skipped: {len(na_filtered) - len(unique_listings)}")
    print(f"   Scored: {len(unique_listings)} | Pushed: {pushed} | Excluded: {excluded} | Errors: {errors}")
    if not TEST_MODE:
        print(f"💰 Estimated API cost: ~${(pushed + excluded) * 0.001:.3f}")
    else:
        print(f"💰 TEST MODE — $0 spent on Claude or SerpAPI")

if __name__ == "__main__":
    run_agent()
