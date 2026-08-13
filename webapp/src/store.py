"""In-memory view over the SQLite-backed state for the single demo user.

`profile`, `priority_weights`, `last_scan` and `jobs` are kept as plain
Python objects for cheap reads (routes/templates access them directly, same
as before). Every mutation goes through a save_*()/update_job_progress()
call here, which is the only place outside src/db.py that talks to
persistence. Routes never touch SQL directly.
"""
import json
from datetime import datetime
from pathlib import Path

from . import db

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
UPLOADS_DIR = DATA_DIR / "uploads"

INDUSTRY_OPTIONS = ["Climate tech", "Healthcare", "Fintech", "Developer tools", "Consumer", "No preference"]
CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "CAD"]

PROFILE_FIELDS = [
    "resume_filename", "roles", "home_address", "commute_miles", "remote_ok",
    "remote_countries", "eligible_countries", "industries", "industries_text",
    "min_salary", "currency", "onboarding_complete",
]

COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Argentina", "Armenia",
    "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados",
    "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina",
    "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia",
    "Cameroon", "Canada", "Chad", "Chile", "China", "Colombia", "Costa Rica", "Croatia",
    "Cuba", "Cyprus", "Czech Republic", "Denmark", "Djibouti", "Dominican Republic",
    "Ecuador", "Egypt", "El Salvador", "Estonia", "Eswatini", "Ethiopia", "Fiji", "Finland",
    "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Guatemala",
    "Guinea", "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia",
    "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan", "Kazakhstan",
    "Kenya", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia",
    "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar", "Malawi", "Malaysia",
    "Maldives", "Mali", "Malta", "Mauritania", "Mauritius", "Mexico", "Moldova", "Monaco",
    "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea",
    "North Macedonia", "Norway", "Oman", "Pakistan", "Panama", "Papua New Guinea",
    "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia",
    "Rwanda", "Saudi Arabia", "Senegal", "Serbia", "Singapore", "Slovakia", "Slovenia",
    "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan",
    "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand",
    "Togo", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Uganda", "Ukraine",
    "United Arab Emirates", "United Kingdom", "United States", "Uruguay", "Uzbekistan",
    "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
]
_COUNTRIES_BY_LOWER = {c.lower(): c for c in COUNTRIES}


def normalize_country(name):
    """Return the canonical country name for a case-insensitive match, or None."""
    return _COUNTRIES_BY_LOWER.get(name.strip().lower())


user_id = None
profile = {}
priority_weights = {}
last_scan = None
jobs = []
DEMO_USER = {}


def _initials(name):
    parts = name.split()
    return "".join(p[0] for p in parts[:2]).upper()


def _load_user():
    global user_id, profile, priority_weights, last_scan, DEMO_USER
    row = db.ensure_demo_user()
    user_id = row["id"]
    profile = {k: row[k] for k in PROFILE_FIELDS}
    priority_weights = row["priority_weights"]
    last_scan = datetime.fromisoformat(row["last_scan"]) if row["last_scan"] else None
    DEMO_USER = {"name": row["name"], "email": row["email"], "initials": _initials(row["name"])}


def save_profile():
    db.update_user(user_id, **{k: profile[k] for k in PROFILE_FIELDS})


def save_priority_weights():
    db.update_user(user_id, priority_weights=priority_weights)


def save_last_scan():
    db.update_user(user_id, last_scan=last_scan.isoformat())


def reset_profile():
    global profile
    db.reset_user(user_id)
    row = db.ensure_demo_user()
    profile = {k: row[k] for k in PROFILE_FIELDS}


def reload_jobs():
    """Re-read data/jobs.json (the catalog) and merge in this user's DB progress.

    Lets the sample catalog be hand-edited on disk and picked up without
    restarting the Flask process. Call via the "Reload sample data" action.
    Status/comments/generated docs live in SQLite and are untouched by this.
    """
    global jobs
    with open(JOBS_FILE) as f:
        catalog = json.load(f)

    db.ensure_progress_rows(user_id, [j["id"] for j in catalog])
    progress = db.fetch_progress(user_id)

    merged = []
    for job in catalog:
        p = progress.get(job["id"], {})
        merged.append({
            **job,
            "status": p.get("status", "new"),
            "comments": p.get("comments", ""),
            "cover_letter": p.get("cover_letter"),
            "tailored_resume": p.get("tailored_resume"),
        })
    jobs = merged
    return jobs


def get_job(job_id):
    return next((j for j in jobs if j["id"] == job_id), None)


def update_job_progress(job_id, **fields):
    db.update_progress(user_id, job_id, **fields)
    job = get_job(job_id)
    if job is not None:
        job.update(fields)


UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_load_user()
reload_jobs()
