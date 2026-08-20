"""Chat orchestrator for the floating, cross-page assistant - one continuous global thread
(see db.py's assistant_messages docstring), not scoped to a job or a "pane". Routes each
free-text turn to the right action (plain chat, a live workflow, or drafting/revising a
cover letter) via agents.route_assistant_turn, and always persists to that thread. Calls
agents (LLM), store (DB), and tailoring (the shared per-job turn logic) - owns no
persistence itself - same shape as scanner.py/learning.py.
"""
import re

from . import agents, store, tailoring, workflows

_RANK_WORDS = {"top": 1, "first": 1, "best": 1, "highest": 1, "second": 2, "third": 3}
_RANK_NUMBER_RE = re.compile(r"#\s*(\d+)")


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
    """
    sessions = store.get_chat_sessions(job_id, "cover_letter")
    if sessions:
        session_id = sessions[0]["id"]
        store.set_session_model(session_id, model)
        return store.get_chat_session(session_id)
    return store.get_chat_session(store.create_chat_session(job_id, "cover_letter", model))


def _clarify_job_reply(candidates):
    if candidates:
        options = "; ".join(f"{j['title']} at {j['company']}" for j in candidates[:5])
        return f"I found more than one match - which one did you mean? {options}"
    if not store.jobs:
        return "There aren't any jobs on your dashboard yet to draft a cover letter for."
    return "Which job would you like a cover letter for?"


def _handle_cover_letter_turn(message, job_query, active_job_id, model):
    """Draft/revise a cover letter for the job this turn is about, via the same
    tailoring.run_turn every per-job Tailor pane uses - so feedback given here produces
    identical persistence and preference-learning behavior. Mirrors the result into this
    thread (tool_name="cover_letter", linked_chat_message_id pointing at the real
    chat_messages row) so the widget's rating buttons can hit the existing, unmodified
    POST /messages/<id>/rate route against the exact row shown on that job's own Tailor page.

    Returns the new assistant_messages row id.
    """
    job, candidates = resolve_job(job_query, store.jobs) if job_query else (None, [])
    if job is None and not job_query:
        job = store.get_job(active_job_id) if active_job_id else None
    if job is None:
        return store.add_assistant_message("assistant", _clarify_job_reply(candidates), model=model)

    chat_session = get_or_create_cover_letter_session(job["id"], model)
    chat_message_id, error = tailoring.run_turn(chat_session, job, message, store.get_preferences())
    if error:
        return store.add_assistant_message("assistant", error, job_id=job["id"], model=model)

    chat_message = store.get_chat_message(chat_message_id)
    return store.add_assistant_message(
        "assistant", chat_message["content"], job_id=job["id"], tool_name="cover_letter",
        model=chat_message["model"], artifact_text=chat_message["artifact_text"],
        linked_chat_message_id=chat_message_id,
        response_time_seconds=chat_message["response_time_seconds"],
        input_tokens=chat_message["input_tokens"], output_tokens=chat_message["output_tokens"],
    )


def _live_workflows():
    return [
        {"id": wid, "name": w["name"], "description": w["description"]}
        for wid, w in workflows.WORKFLOWS.items() if w.get("status") == "live"
    ]


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

    Routes to a live workflow (see workflows.WORKFLOWS), cover-letter drafting/revision, or a
    plain conversational reply - see agents.route_assistant_turn.

    Returns {"user_message": {...}, "assistant_message": {...}}.
    """
    user_message_id = store.add_assistant_message("user", message, model=model)
    history = [{"role": m["role"], "content": m["content"]} for m in store.get_assistant_messages()][:-1]

    try:
        live_workflows = _live_workflows()
        active_job_id = store.get_active_job_id()
        active_job = store.get_job(active_job_id) if active_job_id else None
        routing = agents.route_assistant_turn(message, history, active_job, live_workflows)
        action = routing["action"]

        if action in {w["id"] for w in live_workflows}:
            summary = workflows.WORKFLOWS[action]["run"](mode="live")
            reply = _describe_workflow_result(action, summary)
            assistant_message_id = store.add_assistant_message("assistant", reply, model=model)
        elif action == "cover_letter":
            assistant_message_id = _handle_cover_letter_turn(message, routing.get("job_query"), active_job_id, model)
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
