"""Chat orchestrator for the floating, cross-page assistant - one continuous global thread
(see db.py's assistant_messages docstring), not scoped to a job or a "pane". Routes each
free-text turn to the right action (plain chat, a live workflow, a tailoring draft/revision,
a job-status update, adding a job by URL, or a preference-learning check) via
agents.route_assistant_turn, and always persists to that thread. Calls agents (LLM), ai (job
extraction), learning (preference-learning runs), store (DB), and tailoring (the shared
per-job turn logic) - owns no persistence itself - same shape as scanner.py/learning.py.
"""
import re

from . import agents, ai, learning, store, tailoring, workflows

_RANK_WORDS = {"top": 1, "first": 1, "best": 1, "highest": 1, "second": 2, "third": 3}
_RANK_NUMBER_RE = re.compile(r"#\s*(\d+)")

_TAILORING_TABS = {"cover_letter", "resume", "qa"}

# The fixed (non-workflow) actions the assistant can take, beyond "chat"/"unclear" - combined
# with the live workflows (see _live_workflows) into one catalogue that's both fed to
# agents.route_assistant_turn's prompt and shown back to the user when routing lands on
# "unclear" (see _clarify_action_reply), so the two never drift apart.
FIXED_ACTIONS = [
    {"id": "cover_letter", "name": "Draft or revise a cover letter",
     "description": "for a job you name, or the one just discussed."},
    {"id": "resume", "name": "Draft or revise a resume",
     "description": "for a job you name, or the one just discussed."},
    {"id": "qa", "name": "Draft or revise application Q&A answers",
     "description": "for a job you name, or the one just discussed."},
    {"id": "show_preferred", "name": "Show the preferred cover letter",
     "description": "the one already marked \"ready to send\" for a job."},
    {"id": "job_status", "name": "Mark a job's status",
     "description": "applied, rejected, irrelevant, or viewed - name the job and the status."},
    {"id": "add_job_url", "name": "Add a job posting",
     "description": "paste its URL and I'll extract and add it to your dashboard."},
    {"id": "preference_learning", "name": "Check what's been learned from your feedback",
     "description": "for one job, or across everything - updates your saved writing preferences."},
]


def resolve_job(job_query, jobs):
    """Deterministic match of a free-text job reference (route_assistant_turn's job_query)
    against store.jobs - no LLM, so resolution is inspectable and fails clearly instead of
    the model silently guessing. Handles rank phrases ("top job", "highest match", "#2") via
    jobs sorted by match score, else case-insensitive substring match on "{title} {company}".

    Returns (job, None) on a single clean match, (None, candidates) if 2+ jobs match
    ambiguously, (None, []) if job_query doesn't match anything.
    """
    query = (job_query or "").strip().lower()
    if not query:
        return None, []

    rank = None
    number_match = _RANK_NUMBER_RE.search(query)
    if number_match:
        rank = int(number_match.group(1))
    else:
        for word, r in _RANK_WORDS.items():
            if word in query:
                rank = r
                break
    if rank:
        ranked = sorted(jobs, key=lambda j: j.get("match") if j.get("match") is not None else -1, reverse=True)
        return (ranked[rank - 1], None) if 1 <= rank <= len(ranked) else (None, [])

    matches = [j for j in jobs if query in f"{j['title']} {j['company']}".lower()]
    if len(matches) == 1:
        return matches[0], None
    return (None, matches) if matches else (None, [])


def get_or_create_cover_letter_session(job_id, model):
    """Reuse this job's existing cover_letter thread if one exists, retargeting it to the
    widget's current model in place - store.set_session_model, not store.switch_session_model
    (that helper is fork-aware, built for the Tailor page's multi-model compare panes, and
    would resurface/create a *different* session; here we want the same session, model
    swapped, so switching models in the widget never forks/resets the conversation). Creates
    a new session with that model if none exists yet.

    If this job has a cover letter marked "ready to send" (see store.get_preferred_cover_letter),
    that session always wins over the plain "first pane" default - so once a letter is marked
    preferred, chat-driven feedback ("make it shorter") keeps revising that exact letter
    instead of a different, unmarked pane.
    """
    preferred = store.get_preferred_cover_letter(job_id)
    if preferred:
        session_id = preferred["session_id"]
    else:
        sessions = store.get_chat_sessions(job_id, "cover_letter")
        if not sessions:
            return store.get_chat_session(store.create_chat_session(job_id, "cover_letter", model))
        session_id = sessions[0]["id"]
    store.set_session_model(session_id, model)
    return store.get_chat_session(session_id)


def _clarify_job_reply(candidates, prompt="Which job is this about?"):
    if candidates:
        options = "; ".join(f"{j['title']} at {j['company']}" for j in candidates[:5])
        return f"I found more than one match - which one did you mean? {options}"
    if not store.jobs:
        return "There aren't any jobs on your dashboard yet."
    return prompt


def _get_or_create_tailor_session(job_id, tab, model):
    """Session-getter for _handle_tailoring_turn - cover_letter defers to
    get_or_create_cover_letter_session (preferred-session aware); resume/qa have no such
    concept, so they just reuse the job's first session of that tab, model retargeted in
    place, or create one."""
    if tab == "cover_letter":
        return get_or_create_cover_letter_session(job_id, model)
    sessions = store.get_chat_sessions(job_id, tab)
    if not sessions:
        return store.get_chat_session(store.create_chat_session(job_id, tab, model))
    session_id = sessions[0]["id"]
    store.set_session_model(session_id, model)
    return store.get_chat_session(session_id)


def _handle_tailoring_turn(tab, message, job_query, active_job_id, model):
    """Draft/revise a cover letter/resume/Q&A answers for the job this turn is about, via
    the same tailoring.run_turn every per-job Tailor pane uses - so feedback given here
    produces identical persistence and preference-learning behavior. Mirrors the result into
    this thread (tool_name=tab, linked_chat_message_id pointing at the real chat_messages
    row) so the widget's rating buttons can hit the existing, unmodified POST
    /messages/<id>/rate route against the exact row shown on that job's own Tailor page.

    Returns the new assistant_messages row id.
    """
    job, candidates = resolve_job(job_query, store.jobs) if job_query else (None, [])
    if job is None and not job_query:
        job = store.get_job(active_job_id) if active_job_id else None
    if job is None:
        prompt = {
            "cover_letter": "Which job would you like a cover letter for?",
            "resume": "Which job would you like a resume for?",
            "qa": "Which job would you like Q&A answers for?",
        }[tab]
        return store.add_assistant_message("assistant", _clarify_job_reply(candidates, prompt), model=model)

    chat_session = _get_or_create_tailor_session(job["id"], tab, model)
    chat_message_id, error = tailoring.run_turn(chat_session, job, message, store.get_preferences())
    if error:
        return store.add_assistant_message("assistant", error, job_id=job["id"], model=model)

    chat_message = store.get_chat_message(chat_message_id)
    return store.add_assistant_message(
        "assistant", chat_message["content"], job_id=job["id"], tool_name=tab,
        model=chat_message["model"], artifact_text=chat_message["artifact_text"],
        linked_chat_message_id=chat_message_id,
        response_time_seconds=chat_message["response_time_seconds"],
        input_tokens=chat_message["input_tokens"], output_tokens=chat_message["output_tokens"],
    )


def _handle_show_preferred_turn(job_query, active_job_id, model):
    """"Show me the preferred letter" - a plain lookup, no LLM generation call. Mirrors the
    marked letter into this thread as an artifact_text bubble (same rendering the widget
    already does for a freshly drafted one), so the user can read it and then just keep
    talking - a following revision request routes back through _handle_tailoring_turn,
    which now always continues this exact session once one is marked preferred (see
    get_or_create_cover_letter_session), so that feedback runs the normal tailoring turn
    (classify -> retrieve -> generate -> persist -> learn) against the real letter.
    """
    job, candidates = resolve_job(job_query, store.jobs) if job_query else (None, [])
    if job is None and not job_query:
        job = store.get_job(active_job_id) if active_job_id else None
    if job is None:
        prompt = "Which job would you like the preferred cover letter for?"
        return store.add_assistant_message("assistant", _clarify_job_reply(candidates, prompt), model=model)

    preferred = store.get_preferred_cover_letter(job["id"])
    if not preferred:
        reply = f"No cover letter is marked ready to send yet for {job['title']} at {job['company']}."
        return store.add_assistant_message("assistant", reply, job_id=job["id"], model=model)

    reply = f"Here's the cover letter marked ready to send for {job['title']} at {job['company']}:"
    return store.add_assistant_message(
        "assistant", reply, job_id=job["id"], tool_name="show_preferred",
        model=preferred["model"], artifact_text=preferred["content"],
    )


def _handle_job_status_turn(job_query, status, active_job_id, model):
    """Mark a job's status via chat - the same store.update_job_progress write the
    dashboard's status control uses, not a separate path. `status` comes from
    route_assistant_turn's extraction; not trusted blindly since it's still a free-text
    reading of the message - validated against store.JOB_STATUSES before writing.
    """
    job, candidates = resolve_job(job_query, store.jobs) if job_query else (None, [])
    if job is None and not job_query:
        job = store.get_job(active_job_id) if active_job_id else None
    if job is None:
        prompt = "Which job's status would you like to update?"
        return store.add_assistant_message("assistant", _clarify_job_reply(candidates, prompt), model=model)

    valid = ", ".join(store.JOB_STATUSES)
    if not status:
        reply = f"What should I mark {job['title']} at {job['company']} as? ({valid})"
        return store.add_assistant_message("assistant", reply, job_id=job["id"], model=model)
    if status not in store.JOB_STATUSES:
        reply = f'"{status}" isn\'t a status I recognize - pick one of: {valid}.'
        return store.add_assistant_message("assistant", reply, job_id=job["id"], model=model)

    store.update_job_progress(job["id"], status=status)
    reply = f"Marked {job['title']} at {job['company']} as {status}."
    return store.add_assistant_message("assistant", reply, job_id=job["id"], model=model)


def _handle_add_job_url_turn(url, model):
    """Add a job posting by URL via chat - the same ai.extract_job_posting +
    store.add_custom_job path app.py's job_add route uses for the dashboard's "Add Job
    Posting" popup, not a separate one. Left unranked until the next rescore, same as any
    other add (see store.add_custom_job)."""
    if not url:
        return store.add_assistant_message("assistant", "Paste the job posting's URL and I'll add it.", model=model)
    if store.job_url_exists(url):
        reply = f"That job is already on your dashboard: {url}"
        return store.add_assistant_message("assistant", reply, model=model)

    try:
        fields = ai.extract_job_posting(url)
        job_id = store.add_custom_job(fields)
    except Exception as e:
        return store.add_assistant_message("assistant", str(e), model=model)

    reply = f'Added "{fields["title"]}" at {fields["company"]}. Not yet ranked - rerank your jobs to score it.'
    return store.add_assistant_message("assistant", reply, job_id=job_id, model=model)


def _handle_preference_learning_turn(job_query, model):
    """Run preference learning via chat - the same src.learning.run_learning the
    /tools/preference_learning page uses. Scoped to a named job if one's given, else every
    job's unchecked feedback - no active-job fallback here, unlike the tailoring actions:
    "what have you learned" defaults to "everything", not whatever job was last discussed.
    """
    scope_job_id = None
    if job_query:
        job, candidates = resolve_job(job_query, store.jobs)
        if job is None:
            prompt = "Which job's feedback would you like me to check?"
            return store.add_assistant_message("assistant", _clarify_job_reply(candidates, prompt), model=model)
        scope_job_id = job["id"]

    run_id = learning.run_learning(scope_job_id=scope_job_id, mode="live")
    run = store.get_preference_learning_run(run_id)
    scope_text = "that job's" if scope_job_id else "your"
    reply = f"Checked {run['processed_count']} {scope_text} feedback message(s), updated {run['updated_count']} preference(s)."
    if run["error_message"]:
        reply += f" (error: {run['error_message']})"
    return store.add_assistant_message("assistant", reply, job_id=scope_job_id, model=model)


def _live_workflows():
    return [
        {"id": wid, "name": w["name"], "description": w["description"]}
        for wid, w in workflows.WORKFLOWS.items() if w.get("status") == "live"
    ]


def _clarify_action_reply(available_actions):
    """Deterministic "here's what I can do" reply for action == "unclear" - not restated by
    the model, so it can't hallucinate a capability that isn't actually wired up."""
    listing = "\n".join(f"- {a['name']} ({a['description']})" for a in available_actions)
    return "Not sure I understood that - here's what I can help with:\n" + listing


def _describe_workflow_result(action, summary):
    """Deterministic confirmation text for a completed workflow run - the numbers are exact,
    so this composes the reply in Python rather than risking an LLM restating (and possibly
    hallucinating) a count."""
    if action == "job_search_rerank":
        parts = [f"Added {summary['added']} new job(s) to the dashboard"]
        for component_id, result in summary["per_component"].items():
            if result["error"]:
                parts.append(f"({component_id} failed: {result['error']})")
        parts.append(f"and rescored {summary['rescored']} job(s)")
        if summary["scan_error"]:
            parts.append(f"(scoring error: {summary['scan_error']})")
        return ". ".join(parts) + "."
    if action == "rescore_jobs":
        parts = [f"Reranked {summary['rescored']} job(s) already on the dashboard"]
        if summary["scan_error"]:
            parts.append(f"(scoring error: {summary['scan_error']})")
        return ". ".join(parts) + "."
    return "Done."


def _serialize(message):
    return {
        "id": message["id"],
        "role": message["role"],
        "content": message["content"],
        "job_id": message["job_id"],
        "model": message["model"],
        "artifact_text": message["artifact_text"],
        "linked_chat_message_id": message["linked_chat_message_id"],
        "created_at": message["created_at"],
    }


def handle_turn(message, model):
    """Run one turn of the global assistant thread. Never raises - LLM failures become a
    visible assistant reply, not a 500 (fail clearly to the user, not to the caller). Saves
    the user message before any LLM call, so it's never lost if something downstream breaks -
    same "never lose it" precedent as tailoring.run_turn.

    Routes to a live workflow (see workflows.WORKFLOWS), a tailoring draft/revision, a
    job-status update, adding a job by URL, a preference-learning check, "unclear" (a
    deterministic list of what's supported, see _clarify_action_reply), or a plain
    conversational reply - see agents.route_assistant_turn.

    Returns {"user_message": {...}, "assistant_message": {...}}.
    """
    user_message_id = store.add_assistant_message("user", message, model=model)
    history = [{"role": m["role"], "content": m["content"]} for m in store.get_assistant_messages()][:-1]

    try:
        live_workflows = _live_workflows()
        available_actions = live_workflows + FIXED_ACTIONS
        active_job_id = store.get_active_job_id()
        active_job = store.get_job(active_job_id) if active_job_id else None
        routing = agents.route_assistant_turn(message, history, active_job, available_actions)
        action = routing["action"]

        if action in {w["id"] for w in live_workflows}:
            summary = workflows.WORKFLOWS[action]["run"](mode="live")
            reply = _describe_workflow_result(action, summary)
            assistant_message_id = store.add_assistant_message("assistant", reply, model=model)
        elif action in _TAILORING_TABS:
            assistant_message_id = _handle_tailoring_turn(action, message, routing.get("job_query"), active_job_id, model)
        elif action == "show_preferred":
            assistant_message_id = _handle_show_preferred_turn(routing.get("job_query"), active_job_id, model)
        elif action == "job_status":
            assistant_message_id = _handle_job_status_turn(
                routing.get("job_query"), routing.get("status"), active_job_id, model
            )
        elif action == "add_job_url":
            assistant_message_id = _handle_add_job_url_turn(routing.get("url"), model)
        elif action == "preference_learning":
            assistant_message_id = _handle_preference_learning_turn(routing.get("job_query"), model)
        elif action == "unclear":
            assistant_message_id = store.add_assistant_message(
                "assistant", _clarify_action_reply(available_actions), model=model
            )
        else:
            reply, used_model, usage = agents.answer_assistant_message(
                message, history, store.profile, store.get_preferences(), store.jobs, model
            )
            assistant_message_id = store.add_assistant_message(
                "assistant", reply, model=used_model,
                input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
            )
    except RuntimeError as e:
        assistant_message_id = store.add_assistant_message("assistant", str(e), model=model)

    return {
        "user_message": _serialize(store.get_assistant_message(user_message_id)),
        "assistant_message": _serialize(store.get_assistant_message(assistant_message_id)),
    }


def get_history_payload():
    """{"messages": [...], "model": current assistant model} - for GET /assistant/history."""
    return {
        "messages": [_serialize(m) for m in store.get_assistant_messages()],
        "model": store.get_assistant_model(agents.DEFAULT_MODEL),
    }
