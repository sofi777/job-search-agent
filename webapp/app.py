import re
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename

from src import agents, ai, files, scanner, store

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

def _save_optional_profile_document(file, doc_type, filename=None):
    """Extract text from an optional onboarding upload and save it to the profile-wide knowledge
    base (reused across every job - see store.get_knowledge_base_text). Returns None if no file
    was chosen (nothing to do - these are optional), or a warning string if a file WAS chosen but
    couldn't be read - the caller shows this rather than silently dropping the upload, since a
    document that never made it into the knowledge base with no visible error is a real trap.
    Resets the stream position after reading so a later file.save() still works.
    """
    if not file or not file.filename:
        return None
    filename = filename or secure_filename(file.filename)
    try:
        content = files.extract_text(file)
        store.save_profile_document(doc_type, filename, content)
        return None
    except RuntimeError as e:
        return f"Couldn't read {filename}: {e}"
    finally:
        file.stream.seek(0)


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
    document_warnings = []

    if request.method == "POST":
        if step == "resume":
            file = request.files.get("resume")
            if file and file.filename:
                filename = secure_filename(file.filename)
                # extract before .save() consumes the stream
                warning = _save_optional_profile_document(file, "resume", filename)
                if warning:
                    document_warnings.append(warning)
                file.save(store.UPLOADS_DIR / filename)
                profile["resume_filename"] = filename
                profile["roles"] = ai.suggest_roles(profile["resume_filename"])
                profile["home_address"] = ai.suggest_home_address(profile["resume_filename"])
            elif not profile["resume_filename"]:
                error = "Please upload your resume to continue."

            # Optional extras, reused across every job's tailoring chat (see src/agents.py knowledge_base).
            for warning in (
                _save_optional_profile_document(request.files.get("cover_letter_sample"), "cover_letter_sample"),
                _save_optional_profile_document(request.files.get("story_bank"), "story_bank"),
            ):
                if warning:
                    document_warnings.append(warning)

            # A failed optional upload doesn't block onboarding, but it also shouldn't vanish
            # silently - stay on this page so the warning is actually seen, instead of redirecting
            # straight past it.
            if error is None and not document_warnings:
                store.save_profile()
                return redirect(url_for("onboarding", step="roles"))
            elif error is None:
                store.save_profile()

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
    profile_documents = {d["type"]: d["filename"] for d in store.get_profile_documents()} if step == "resume" else {}
    return render_template(
        f"onboarding_{step}.html",
        profile=profile,
        step_index=step_index,
        step_count=len(ONBOARDING_STEPS),
        industry_options=store.INDUSTRY_OPTIONS,
        currency_options=store.CURRENCY_OPTIONS,
        countries=store.COUNTRIES,
        profile_documents=profile_documents,
        error=error,
        document_warnings=document_warnings,
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
        add_job_error=session.pop("add_job_error", None),
        add_job_success=session.pop("add_job_success", None),
    )


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    scanner.run_scan()
    return redirect(url_for("dashboard"))


@app.route("/data/reload", methods=["POST"])
@login_required
def reload_data():
    store.reload_sample_jobs()
    scanner.run_scan()
    return redirect(url_for("dashboard"))


@app.route("/jobs/add", methods=["POST"])
@login_required
def job_add():
    url = request.form.get("url", "").strip()
    if url:
        if store.job_url_exists(url):
            session["add_job_error"] = f"That job is already on your list: {url}"
        else:
            try:
                fields = ai.extract_job_posting(url)
                store.add_custom_job(fields)
                session["add_job_success"] = f'Added "{fields["title"]}" at {fields["company"]}.'
            except Exception as e:
                session["add_job_error"] = str(e)
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


TAB_LABELS = {"cover_letter": "Cover Letter", "resume": "Resume", "qa": "Q&A"}
_CITATION_RE = re.compile(r"\[Source (\d+)\]")


def _qa_context_text(job_id):
    items = store.get_qa_list(job_id)
    if not items:
        return ""
    return "\n\n".join(f"Q: {i['question_text']}\nA: {i['content']}" for i in items)


def _render_chat_content(content, citations):
    """Convert "[Source N]" markers into clickable buttons carrying that citation's chunk
    text/score/filename as data attributes (see tailor.html showCitation()). Everything else
    is escaped normally. Returns a Markup-safe string.
    """
    by_number = {c["source_number"]: c for c in citations}
    pieces, last_end = [], 0
    for m in _CITATION_RE.finditer(content):
        pieces.append(escape(content[last_end:m.start()]))
        citation = by_number.get(int(m.group(1)))
        if citation:
            pieces.append(Markup(
                '<button type="button" class="citation" data-source="{}" data-filename="{}" '
                'data-score="{}" data-text="{}">{}</button>'
            ).format(m.group(1), citation["filename"], citation["score"], citation["text"], m.group(0)))
        else:
            pieces.append(escape(m.group(0)))
        last_end = m.end()
    pieces.append(escape(content[last_end:]))
    return Markup("").join(pieces)


@app.route("/jobs/<int:job_id>/tailor")
@login_required
def tailor(job_id):
    job = store.get_job(job_id)
    if job is None:
        return redirect(url_for("dashboard"))
    if job["status"] == "new":
        store.update_job_progress(job_id, status="viewed")

    tab = request.args.get("tab", "cover_letter")
    if tab not in store.TAILOR_TYPES:
        return redirect(url_for("tailor", job_id=job_id))

    chat = store.get_chat_for_display(job_id, tab)
    for m in chat:
        m["rendered"] = _render_chat_content(m["content"], m["citations"])

    return render_template(
        "tailor.html",
        job=job,
        tab=tab,
        tab_labels=TAB_LABELS,
        chat=chat,
        artifact_text=store.get_artifact_text(job_id, tab) if tab != "qa" else None,
        qa_list=store.get_qa_list(job_id) if tab == "qa" else None,
        models=agents.MODEL_OPTIONS,
        default_model=agents.DEFAULT_MODEL,
        error=session.pop("tailor_error", None),
    )


@app.route("/jobs/<int:job_id>/tailor/<tab>/message", methods=["POST"])
@login_required
def tailor_message(job_id, tab):
    if tab not in store.TAILOR_TYPES:
        return redirect(url_for("tailor", job_id=job_id))
    job = store.get_job(job_id)
    if job is None:
        return redirect(url_for("dashboard"))

    message = request.form.get("message", "").strip()
    model = request.form.get("model") or agents.DEFAULT_MODEL
    attachment = request.files.get("attachment")
    save_to_profile = bool(request.form.get("save_to_profile"))

    if not message and not (attachment and attachment.filename):
        return redirect(url_for("tailor", job_id=job_id, tab=tab))

    try:
        # display_message is what's shown in the chat and persisted to history: a short marker
        # for the attachment, not its full text. The full text is persisted+chunked+embedded
        # separately (store.save_chat_attachment) and reaches the model only via retrieval on
        # whichever future turns actually match it - not smuggled into chat history, which would
        # otherwise resend the whole document on every later message in this thread.
        display_message = message
        if attachment and attachment.filename:
            attachment_text = files.extract_text(attachment)
            store.save_chat_attachment(job_id, attachment.filename, attachment_text, save_to_profile)
            display_message = f"\U0001F4CE {attachment.filename}\n{message}".strip()

        history = store.get_chat(job_id, tab)
        preferences = store.get_preferences()

        retrieval_query = agents.build_retrieval_query(job, display_message)
        retrieved_chunks = store.retrieve_context(job_id, retrieval_query, top_k=3)
        retrieved_context = agents.format_retrieved_context(retrieved_chunks)

        if tab == "qa":
            current_text = _qa_context_text(job_id)
        else:
            current_text = store.get_artifact_text(job_id, tab)

        result = agents.run_tailor_turn(
            tab, job, store.profile, preferences, history, current_text, display_message, model, retrieved_context
        )

        store.add_chat_message(job_id, tab, "user", display_message, model)
        assistant_message_id = store.add_chat_message(job_id, tab, "assistant", result.get("reply", ""), model)
        store.save_citations(assistant_message_id, retrieved_chunks)

        if tab == "qa":
            qa_list = store.get_qa_list(job_id)
            if result.get("action") == "new_question" and result.get("question"):
                store.add_qa(job_id, result["question"], result.get("answer", ""))
            elif qa_list:
                store.update_qa(qa_list[-1]["id"], result.get("answer", ""))
            feedback_content = result.get("answer", "")
        else:
            new_artifact = result.get("artifact")
            if new_artifact:
                store.save_artifact(job_id, tab, new_artifact)
            feedback_content = new_artifact or current_text

        revision = agents.revise_preferences(tab, display_message, feedback_content, preferences, model)
        if revision:
            store.save_preference(revision["category"], revision["text"])
    except RuntimeError as e:
        session["tailor_error"] = str(e)

    return redirect(url_for("tailor", job_id=job_id, tab=tab))


# ---- preferences ----------------------------------------------------------

@app.route("/preferences", methods=["GET", "POST"])
@login_required
def preferences():
    if request.method == "POST":
        for category in store.PREFERENCE_CATEGORIES:
            store.save_preference(category, request.form.get(category, ""))
        return redirect(url_for("preferences"))
    return render_template("preferences.html", prefs=store.get_preferences_full())


# ---- knowledge base chunks --------------------------------------------------

@app.route("/chunks", methods=["GET", "POST"])
@login_required
def chunks():
    if request.method == "POST":
        try:
            size = int(request.form.get("chunk_size", ""))
            if size < 16:
                raise ValueError("Chunk size must be at least 16 tokens.")
            store.rechunk_knowledge_base(size)
        except (ValueError, RuntimeError) as e:
            session["chunks_error"] = str(e)
        return redirect(url_for("chunks"))

    rows = store.get_all_chunks()
    for row in rows:
        job = store.get_job(row["job_id"]) if row["job_id"] is not None else None
        row["scope"] = job["title"] + " (" + job["company"] + ")" if job else "All jobs"

    return render_template(
        "chunks.html",
        chunks=rows,
        chunk_size=store.get_chunk_size(),
        error=session.pop("chunks_error", None),
    )


if __name__ == "__main__":
    app.run(debug=True, port=8014)
