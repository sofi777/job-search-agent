import anthropic
import json
import requests
import feedparser
from datetime import datetime, timezone
from notion_client import Client
import os

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

HARD EXCLUSIONS — return score 0 and action "exclude" if any of these apply:
- Requires relocation
- Requires US citizenship, US work permit, US work visa, or security clearance
- Pure project management with no product ownership
- Role is below Senior PM level (PM, Associate PM, Junior PM)
- Salary explicitly stated and below CAD $120,000

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
    queries = [
        "Senior+Product+Manager",
        "Lead+Product+Manager",
        "Group+Product+Manager",
        "Director+of+Product",
        "VP+Product",
    ]
    locations = ["Vancouver+BC", "British+Columbia", "Remote+Canada"]
    for query in queries:
        for loc in locations:
            url = f"https://ca.indeed.com/rss?q={query}&l={loc}&sort=date"
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:10]:
                    listings.append({
                        "title": entry.get("title", ""),
                        "company": entry.get("source", {}).get("title", "Unknown"),
                        "url": entry.get("link", ""),
                        "location": loc.replace("+", " "),
                        "salary": "Not specified",
                        "date_posted": entry.get("published", "Unknown"),
                        "source": "Indeed Canada",
                        "description": entry.get("summary", "")[:1500],
                    })
            except Exception as e:
                print(f"  Indeed error ({query}/{loc}): {e}")
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
# SOURCE 3: BC PUBLIC SERVICE
# ─────────────────────────────────────────────

def fetch_bc_public_service():
    print("\n📡 Fetching BC Public Service...")
    listings = []
    try:
        url = "https://bcpublicservice.hua.hrsmart.com/hr/ats/JobSearch/search"
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        params = {"keywords": "product manager director digital"}
        response = requests.get(
            "https://www.bcpublicservice.ca/careers/search-current-opportunities/",
            headers=headers, timeout=15
        )
        # Parse basic job listings from BC Public Service RSS
        feed = feedparser.parse("https://bcpublicservice.hua.hrsmart.com/hr/ats/JobSearch/viewAll")
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            if any(kw in title.lower() for kw in [
                "product", "digital", "director", "service design",
                "business analyst", "it manager", "technology"
            ]):
                listings.append({
                    "title": title,
                    "company": "BC Public Service",
                    "url": entry.get("link", "https://www.bcpublicservice.ca/careers/"),
                    "location": "British Columbia (hybrid/in-office)",
                    "salary": "As per BC Government pay grid",
                    "date_posted": entry.get("published", "Unknown"),
                    "source": "BC Public Service",
                    "description": entry.get("summary", "")[:1500],
                })
    except Exception as e:
        print(f"  BC Public Service error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 4: GC JOBS (FEDERAL CANADA)
# ─────────────────────────────────────────────

def fetch_gc_jobs():
    print("\n📡 Fetching GC Jobs (Federal Canada)...")
    listings = []
    try:
        feeds = [
            "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1710?toggleLanguage=en&sender=displaySearchJob%40actionId%3D10&psrsMode=1&requestedPage=1710&searchJobType=0&searchCity=&searchOrganization=&searchKeyword=product+manager&keywordButton=Search&numOfResults=25",
        ]
        # GC Jobs RSS alternative
        url = "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1710?toggleLanguage=en&psrsMode=1&searchKeyword=product+manager&numOfResults=25"
        headers = {"User-Agent": "Mozilla/5.0 job-search-agent/1.0"}
        feed = feedparser.parse(
            "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1710?toggleLanguage=en&psrsMode=1&searchKeyword=digital+product&numOfResults=25&sender=displaySearchJob%40actionId%3D10&requestedPage=1710"
        )
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            listings.append({
                "title": title,
                "company": "Government of Canada",
                "url": entry.get("link", "https://jobs-emplois.gc.ca/"),
                "location": "Canada (remote eligible)",
                "salary": "As per GC pay grid",
                "date_posted": entry.get("published", "Unknown"),
                "source": "GC Jobs Federal",
                "description": entry.get("summary", "")[:1500],
            })
    except Exception as e:
        print(f"  GC Jobs error: {e}")
    print(f"  Found {len(listings)} listings")
    return listings

# ─────────────────────────────────────────────
# SOURCE 5: LEVER API (COMPANY CAREER PAGES)
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
    all_listings += fetch_indeed()
    all_listings += fetch_remoteok()
    all_listings += fetch_bc_public_service()
    all_listings += fetch_gc_jobs()
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
    print(f"💰 Estimated API cost: ~${pushed * 0.001:.3f}")

if __name__ == "__main__":
    run_agent()
