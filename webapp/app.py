from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from src import ai, scanner, store

app = Flask(__name__)
app.secret_key = "dev-only-secret-key"  # POC only; use a real secret + env var before any real deploy

ONBOARDING_STEPS = ["resume", "roles", "location", "preferences", "salary"]


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    return {"user": store.DEMO_USER}


def fmt_last_scan():
    if store.last_scan is None:
        return "Never"
    return store.last_scan.strftime("%b %d, %I:%M %p")


# ---- auth -------------------------------------------------------------

@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    if not store.profile["onboarding_complete"]:
        return redirect(url_for("onboarding", step="resume"))
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session["logged_in"] = True
        session["user"] = store.DEMO_USER["name"]
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---- onboarding wizard --------------------------------------------------

@app.route("/onboarding/restart", methods=["POST"])
@login_required
def onboarding_restart():
    store.reset_profile()
    return redirect(url_for("onboarding", step="resume"))


@app.route("/onboarding/<step>", methods=["GET", "POST"])
@login_required
def onboarding(step):
    if step not in ONBOARDING_STEPS:
        return redirect(url_for("onboarding", step="resume"))

    profile = store.profile
    error = None

    if request.method == "POST":
        if step == "resume":
            file = request.files.get("resume")
            if file and file.filename:
                filename = secure_filename(file.filename)
                file.save(store.UPLOADS_DIR / filename)
                profile["resume_filename"] = filename
                profile["roles"] = ai.suggest_roles(profile["resume_filename"])
                profile["home_address"] = ai.suggest_home_address(profile["resume_filename"])
            elif not profile["resume_filename"]:
                error = "Please upload your resume to continue."
            if error is None:
                store.save_profile()
                return redirect(url_for("onboarding", step="roles"))

        if step == "roles":
            action = request.form.get("action")
            if action == "add":
                new_role = request.form.get("new_role", "").strip()
                if new_role and new_role not in profile["roles"]:
                    profile["roles"].append(new_role)
            elif action == "remove":
                profile["roles"] = [r for r in profile["roles"] if r != request.form.get("role")]
            store.save_profile()
            if action == "continue":
                return redirect(url_for("onboarding", step="location"))
            return redirect(url_for("onboarding", step="roles"))

        if step == "location":
            action = request.form.get("action")
            if action == "add_country":
                v = store.normalize_country(request.form.get("country", ""))
                if v and v not in profile["eligible_countries"]:
                    profile["eligible_countries"].append(v)
            elif action == "remove_country":
                profile["eligible_countries"] = [c for c in profile["eligible_countries"] if c != request.form.get("country")]
            elif action == "add_remote_country":
                v = store.normalize_country(request.form.get("country", ""))
                if v and v not in profile["remote_countries"]:
                    profile["remote_countries"].append(v)
            elif action == "remove_remote_country":
                profile["remote_countries"] = [c for c in profile["remote_countries"] if c != request.form.get("country")]
            elif action == "continue":
                profile["home_address"] = request.form.get("home_address", profile["home_address"])
                profile["commute_miles"] = int(request.form.get("commute_miles", profile["commute_miles"]))
                profile["remote_ok"] = bool(request.form.get("remote_ok"))
            store.save_profile()
            if action == "continue":
                return redirect(url_for("onboarding", step="preferences"))
            return redirect(url_for("onboarding", step="location"))

        if step == "preferences":
            action = request.form.get("action")
            if action == "toggle_industry":
                name = request.form.get("industry")
                if name in profile["industries"]:
                    profile["industries"].remove(name)
                else:
                    profile["industries"].append(name)
            elif action == "continue":
                profile["industries_text"] = request.form.get("industries_text", "")
            store.save_profile()
            if action == "continue":
                return redirect(url_for("onboarding", step="salary"))
            return redirect(url_for("onboarding", step="preferences"))

        if step == "salary":
            profile["min_salary"] = int(request.form.get("min_salary") or 0)
            profile["currency"] = request.form.get("currency", profile["currency"])
            profile["onboarding_complete"] = True
            store.save_profile()
            scanner.run_scan()
            return redirect(url_for("dashboard"))

    step_index = ONBOARDING_STEPS.index(step)
    return render_template(
        f"onboarding_{step}.html",
        profile=profile,
        step_index=step_index,
        step_count=len(ONBOARDING_STEPS),
        industry_options=store.INDUSTRY_OPTIONS,
        currency_options=store.CURRENCY_OPTIONS,
        countries=store.COUNTRIES,
        error=error,
    )


# ---- dashboard ----------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    if not store.profile["onboarding_complete"]:
        return redirect(url_for("onboarding", step="resume"))

    sort_key = request.args.get("sort", "posted")
    sort_dir = request.args.get("dir", "desc")
    status_filter = request.args.get("status", "all")
    query = request.args.get("q", "").strip().lower()

    jobs = list(store.jobs)
    if status_filter != "all":
        jobs = [j for j in jobs if j["status"] == status_filter]
    if query:
        jobs = [j for j in jobs if query in j["company"].lower() or query in j["title"].lower()]

    reverse = sort_dir == "desc"
    if sort_key == "posted":
        jobs.sort(key=lambda j: j["posted"], reverse=reverse)
    elif sort_key == "match":
        jobs.sort(key=lambda j: j.get("match", 0), reverse=reverse)
    elif sort_key in ("company", "title", "status"):
        jobs.sort(key=lambda j: j[sort_key], reverse=reverse)

    new_count = sum(1 for j in store.jobs if j["status"] == "new")

    return render_template(
        "dashboard.html",
        jobs=jobs,
        job_count=len(store.jobs),
        new_count=new_count,
        last_scan=fmt_last_scan(),
        sort_key=sort_key,
        sort_dir=sort_dir,
        status_filter=status_filter,
        query=request.args.get("q", ""),
    )


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    scanner.run_scan()
    return redirect(url_for("dashboard"))


@app.route("/data/reload", methods=["POST"])
@login_required
def reload_data():
    store.reload_jobs()
    scanner.run_scan()
    return redirect(url_for("dashboard"))


# ---- priority ranking -----------------------------------------------------

@app.route("/priority", methods=["GET", "POST"])
@login_required
def priority():
    if request.method == "POST":
        for key in store.priority_weights:
            store.priority_weights[key] = int(request.form.get(key, store.priority_weights[key]))
        store.save_priority_weights()
        scanner.run_scan()
        return redirect(url_for("dashboard"))
    return render_template("priority.html", weights=store.priority_weights)


# ---- job detail + generated docs ------------------------------------------

@app.route("/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    job = store.get_job(job_id)
    if job is None:
        return redirect(url_for("dashboard"))
    if job["status"] == "new":
        store.update_job_progress(job_id, status="viewed")
    return render_template("job_detail.html", job=job)


@app.route("/jobs/<int:job_id>/comment", methods=["POST"])
@login_required
def job_comment(job_id):
    store.update_job_progress(job_id, comments=request.form.get("comments", ""))
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/jobs/<int:job_id>/apply", methods=["POST"])
@login_required
def job_apply(job_id):
    job = store.get_job(job_id)
    if job is not None:
        new_status = "viewed" if job["status"] == "applied" else "applied"
        store.update_job_progress(job_id, status=new_status)
    return redirect(url_for("job_detail", job_id=job_id))


JOB_STATUSES = ["new", "viewed", "applied"]


@app.route("/jobs/<int:job_id>/status", methods=["POST"])
@login_required
def job_status(job_id):
    new_status = request.form.get("status")
    if store.get_job(job_id) is not None and new_status in JOB_STATUSES:
        store.update_job_progress(job_id, status=new_status)
    return redirect(request.referrer or url_for("dashboard"))


DOC_GENERATORS = {
    "cover-letter": ("cover_letter", "Cover letter", ai.generate_cover_letter),
    "resume": ("tailored_resume", "Tailored resume", ai.generate_tailored_resume),
}


@app.route("/jobs/<int:job_id>/<doc_type>", methods=["GET", "POST"])
@login_required
def job_document(job_id, doc_type):
    if doc_type not in DOC_GENERATORS:
        return redirect(url_for("job_detail", job_id=job_id))
    field, label, generate = DOC_GENERATORS[doc_type]

    job = store.get_job(job_id)
    if job is None:
        return redirect(url_for("dashboard"))
    if job["status"] == "new":
        store.update_job_progress(job_id, status="viewed")

    if request.method == "POST":
        if request.form.get("action") == "regenerate":
            store.update_job_progress(job_id, **{field: generate(job, store.profile)})
        else:
            store.update_job_progress(job_id, **{field: request.form.get("text", "")})
            return redirect(url_for("job_detail", job_id=job_id))
    elif not job.get(field):
        store.update_job_progress(job_id, **{field: generate(job, store.profile)})

    return render_template("generated_doc.html", job=job, doc_type=doc_type, label=label, text=job[field])


if __name__ == "__main__":
    app.run(debug=True, port=8014)
