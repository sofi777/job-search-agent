import anthropic
import json
import requests
import feedparser
from datetime import datetime, timezone
from notion_client import Client
import os
# VERSION 2 — all fixes applied 2026-04-09

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
Location: Remote (Canada or US if open to Canadian applicants), or hybrid/in-office in BC

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
        print(f"Warning: could not fetch existing URLs: {e}")
    return existing

# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────

def score_job(title, description, company, salary="Not specified", location="Not specified"):
    """Score a job listing using Claude Haiku."""
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
        # Strip markdown fences if Claude adds them despite instructions
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        print(f"  Scoring error for {title}: {e}")
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
# SOURCE 1: INDEED CANADA RSS
# ─────────────────────────────────────────────

def fetch_indeed():
    print("\n📡 Fetching Indeed Canada...")
    listings = []
    headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}

    # (query, location) pairs — all Canada-specific
    # Includes government and crown corp searches
    searches = [
        # Senior PM roles — BC and remote Canada
        ("Senior+Product+Manager", "Vancouver+BC"),
        ("Senior+Product+Manager", "British+Columbia"),
        ("Senior+Product+Manager", "Remote+Canada"),
        ("Lead+Product+Manager", "Vancouver+BC"),
        ("Lead+Product+Manager", "Remote+Canada"),
        ("Group+Product+Manager", "Remote+Canada"),
        ("Director+of+Product", "Vancouver+BC"),
        ("Director+of+Product", "Remote+Canada"),
        ("VP+of+Product", "Remote+Canada"),
        ("Head+of+Product", "Vancouver+BC"),
        ("Head+of+Product", "Remote+Canada"),
        # Government — BC
        ("Product+Manager", "BC+Public+Service"),
        ("Director+Digital+Services", "British+Columbia"),
        ("Service+Design+Lead", "British+Columbia"),
        ("IT+Project+Manager+digital", "British+Columbia"),
        # Crown corps — BC
        ("Product+Manager", "BC+Hydro"),
        ("Product+Manager", "TransLink+Vancouver"),
        ("Product+Manager", "ICBC+British+Columbia"),
        ("Product+Manager", "BC+Lottery+Corporation"),
        # Federal government — remote eligible
        ("Product+Manager+digital", "Government+of+Canada"),
        ("Director+Digital", "Government+of+Canada"),
        ("Senior+Analyst+digital+product", "Government+of+Canada"),
    ]

    seen = set()
    for query, location in searches:
        # fromage=7 for first run (last 7 days)
        # Change to fromage=1 for daily runs after first run
        url = f"https://ca.indeed.com/rss?q={query}&l={location}&sort=date&fromage=7"
        try:
            feed = feedparser.parse(url, request_headers=headers)
            for entry in feed.entries[:10]:
                entry_url = entry.get("link", "")
                if not entry_url or entry_url in seen:
                    continue
                seen.add(entry_url)
                listings.append({
                    "title": entry.get("title", ""),
                    "company": entry.get("author", "Unknown"),
                    "url": entry_url,
                    "location": location.replace("+", " "),
                    "salary": "Not specified",
                    "date_posted": entry.get("published", "Unknown"),
                    "source": "Indeed Canada",
                    "description": entry.get("summary", "")[:1500],
                })
        except Exception as e:
            print(f"  Indeed error ({query}/{location}): {e}")

    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 2: REMOTEOK RSS
# ─────────────────────────────────────────────

def fetch_remoteok():
    print("\n📡 Fetching RemoteOK...")
    listings = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        response = requests.get("https://remoteok.com/remote-product-manager-jobs.json", headers=headers, timeout=10)
        jobs = response.json()
        for job in jobs[1:30]:  # skip first item (metadata)
            if not isinstance(job, dict):
                continue
            title = job.get("position", "")
            if not any(kw in title.lower() for kw in ["senior", "lead", "group", "director", "vp", "principal", "head"]):
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
        print(f"  RemoteOK error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 3: LEVER API (COMPANY CAREER PAGES)
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
    pm_keywords = ["product manager", "product lead", "head of product", "director of product", "vp of product", "vp product"]

    for slug, company_name in LEVER_COMPANIES:
        try:
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"  {company_name}: status {response.status_code}")
                continue
            jobs = response.json()
            for job in jobs:
                title = job.get("text", "").lower()
                if not any(kw in title for kw in pm_keywords):
                    continue
                if not any(kw in title for kw in senior_keywords):
                    continue
                categories = job.get("categories", {})
                location = categories.get("location", "Not specified")
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
        except Exception as e:
            print(f"  {company_name} Lever error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 6: GREENHOUSE API (COMPANY CAREER PAGES)
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
    pm_keywords = ["product manager", "product lead", "head of product", "director of product", "vp of product", "vp product"]

    for slug, company_name in GREENHOUSE_COMPANIES:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"  {company_name}: status {response.status_code}")
                continue
            data = response.json()
            jobs = data.get("jobs", [])
            for job in jobs:
                title = job.get("title", "").lower()
                if not any(kw in title for kw in pm_keywords):
                    continue
                if not any(kw in title for kw in senior_keywords):
                    continue
                location = job.get("location", {}).get("name", "Not specified")
                description = job.get("content", "")[:1500]
                listings.append({
                    "title": job.get("title", ""),
                    "company": company_name,
                    "url": job.get("absolute_url", ""),
                    "location": location,
                    "salary": "Not specified",
                    "date_posted": job.get("updated_at", "Unknown")[:10],
                    "source": f"Greenhouse / {company_name}",
                    "description": description,
                })
        except Exception as e:
            print(f"  {company_name} Greenhouse error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 7: BC TECH ASSOCIATION
# ─────────────────────────────────────────────

def fetch_bc_tech():
    print("\n📡 Fetching BC Tech job board...")
    listings = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        feed = feedparser.parse("https://www.bctechnology.com/rss/jobs.cfm")
        for entry in feed.entries[:30]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in ["product", "director", "vp", "lead"]):
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
        print(f"  BC Tech error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 8: WELLFOUND
# ─────────────────────────────────────────────

def fetch_wellfound():
    print("\n📡 Fetching Wellfound...")
    listings = []
    try:
        # Wellfound public RSS for PM roles in Canada
        feed = feedparser.parse("https://wellfound.com/role/l/product-manager/canada-startups.rss")
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in ["senior", "lead", "director", "vp", "group", "principal", "head"]):
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
        print(f"  Wellfound error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# MAIN AGENT LOOP
# ─────────────────────────────────────────────

def run_agent():
    print(f"\n🚀 Job Search Agent starting — {datetime.now().strftime('%Y-%m-%d %H:%M')} PT")
    print("=" * 60)

    # Get existing URLs to avoid duplicates
    print("\n🔍 Checking existing Notion entries for deduplication...")
    existing_urls = get_existing_urls()
    print(f"  Found {len(existing_urls)} existing entries")

    # Fetch from all sources
    all_listings = []
    all_listings += fetch_indeed()          # includes gov + crown corp queries
    all_listings += fetch_remoteok()
    all_listings += fetch_lever_companies()
    all_listings += fetch_greenhouse_companies()
    all_listings += fetch_bc_tech()
    all_listings += fetch_wellfound()

    print(f"\n📊 Total raw listings fetched: {len(all_listings)}")

    # Deduplicate by URL
    seen_urls = set()
    unique_listings = []
    for listing in all_listings:
        url = listing.get("url", "")
        if url and url not in existing_urls and url not in seen_urls:
            seen_urls.add(url)
            unique_listings.append(listing)

    print(f"📊 New unique listings to score: {len(unique_listings)}")

    # Score and push
    pushed = 0
    excluded = 0
    errors = 0

    for i, listing in enumerate(unique_listings):
        print(f"\n[{i+1}/{len(unique_listings)}] Scoring: {listing['title']} at {listing['company']}")
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
    print(f"✅ Done! Pushed: {pushed} | Excluded: {excluded} | Errors: {errors}")
    print(f"💰 Estimated API cost: ~${(pushed + excluded) * 0.001:.3f}")

if __name__ == "__main__":
    run_agent()
