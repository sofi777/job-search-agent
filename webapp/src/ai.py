"""Placeholder AI functions.

None of these call a real model yet. Each returns a generic, deterministic
hint derived from simple string/number rules. Every function's signature
(what it takes, what it returns) is the contract a real LLM-backed
implementation should keep, so swapping the body later is a drop-in change
with no caller updates.
"""

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


def generate_cover_letter(job, profile):
    """Draft a cover letter for a job, in the user's voice.

    Placeholder: fills a fixed template with job/profile details.
    Real version: generate from the résumé + job description via an LLM.
    """
    name = profile.get("name", "the applicant")
    return (
        f"Hi there,\n\n"
        f"I've been following {job['company']}'s work closely, and the {job['title']} role is exactly "
        f"the kind of problem I want to spend my next few years on. My background lines up closely with "
        f"what this role needs, and I'd bring the same ownership and pace your team is clearly optimizing for.\n\n"
        f"I'd welcome the chance to talk through specifics whenever's useful.\n\n"
        f"{name}"
    )


def generate_tailored_resume(job, profile):
    """Draft a resume summary tailored to a specific job.

    Placeholder: fills a fixed template with job/profile details.
    Real version: rewrite the uploaded résumé, tailored via an LLM.
    """
    name = profile.get("name", "APPLICANT")
    roles = ", ".join(profile.get("roles", [])) or "relevant roles"
    return (
        f"{name.upper()}\n"
        f"{roles}\n\n"
        f"Tailored for: {job['title']} @ {job['company']}\n\n"
        f"SUMMARY\n"
        f"Experienced professional with a track record directly relevant to this role's core "
        f"responsibilities: {job['description'][:140]}...\n\n"
        f"RELEVANT EXPERIENCE\n"
        f"- [Placeholder] Achievement matching this role's key requirement\n"
        f"- [Placeholder] Achievement demonstrating relevant domain depth\n"
        f"- [Placeholder] Achievement showing scope/ownership fit for this level"
    )
