"""Runs one turn of a per-job tailoring chat (cover_letter/resume/qa) - classify, retrieve,
generate, persist, and (if the feedback reveals one) learn a durable writing preference.

Shared by app.py's per-job Tailor pane route and src/assistant.py's chat-driven cover-letter
drafting - a single code path, so feedback given from either surface behaves identically
(same persistence, same learning-cycle trigger). Extracted from what was app.py's
_run_pane_turn - same logic, still Flask-free (no request/session use), just callable from
more than one place now.
"""
import time

from . import agents, store


def _qa_context_text(session_id):
    items = store.get_qa_list(session_id)
    if not items:
        return ""
    return "\n\n".join(f"Q: {i['question_text']}\nA: {i['content']}" for i in items)


def run_turn(chat_session, job, display_message, preferences):
    """Run one full turn (classify -> retrieve -> generate -> persist) for one session's own
    thread. Returns (assistant_message_id, error): assistant_message_id is the newly saved
    chat_messages row (None if the turn failed before one was created), error is None on
    success or a message string on failure, for the caller to show inline rather than
    crashing the whole request.
    """
    session_id, model, tab = chat_session["id"], chat_session["model"], chat_session["type"]
    try:
        history = agents.trim_history(store.get_chat(session_id))

        # Saved now, before any of the LLM calls below that could fail - so the user's message
        # is never lost from this session's thread if something downstream breaks. history was
        # already fetched above, so this doesn't duplicate into what gets sent to the model.
        user_message_id = store.add_chat_message(session_id, job["id"], tab, "user", display_message, model)

        current_text = _qa_context_text(session_id) if tab == "qa" else store.get_artifact_text(session_id)

        # One free-tier classification call covers both "does this need fresh retrieval" and
        # "could this reveal a durable preference" - see agents.classify_turn.
        classification = agents.classify_turn(tab, display_message, bool(current_text))
        check_preferences = bool(current_text) and classification["reveals_preference"]

        if classification["needs_retrieval"]:
            retrieval_query = agents.build_retrieval_query(job, display_message)
            retrieved_chunks = store.retrieve_context(job["id"], retrieval_query, top_k=3)
            retrieved_context = agents.format_retrieved_context(retrieved_chunks)
        else:
            retrieved_chunks = []
            retrieved_context = "(not re-searched this turn - existing content covers this; rely on what's already here.)"

        turn_started = time.monotonic()
        result, used_model, usage = agents.run_tailor_turn(
            tab, job, store.profile, preferences, history, current_text, display_message, model, retrieved_context
        )
        response_time_seconds = time.monotonic() - turn_started

        # The resulting document as of this turn - the actual cover letter/résumé/Q&A answer,
        # not the conversational reply. Computed before add_chat_message so it can be stored
        # alongside it (chat_messages.artifact_text) and reused for revise_preferences below.
        artifact_text = result.get("answer", "") if tab == "qa" else (result.get("artifact") or current_text)

        # Preferences stay global/shared across every pane on purpose - feedback given to one
        # model should improve every model's output, not just that pane's. Run before saving
        # the assistant reply so a learned preference can be surfaced inline in that same
        # message, visible to the user rather than a silent DB write.
        reply_text = result.get("reply", "")
        if check_preferences:
            try:
                revision = agents.revise_preferences(tab, display_message, artifact_text, preferences, model)
                if revision:
                    store.save_preference(revision["category"], revision["text"])
                    reply_text += f"\n\n_Learned ({revision['category']}): {revision['text']}_"
            except RuntimeError:
                # This is a secondary, best-effort step - the message/artifact above already
                # generated and saved successfully, so a broken model reply here shouldn't
                # discard that and report the whole turn as failed.
                pass

        assistant_message_id = store.add_chat_message(
            session_id, job["id"], tab, "assistant", reply_text, used_model,
            response_time_seconds=response_time_seconds,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            artifact_text=artifact_text,
        )
        store.save_citations(assistant_message_id, retrieved_chunks)

        if tab == "qa":
            qa_list = store.get_qa_list(session_id)
            if result.get("action") == "new_question" and result.get("question"):
                store.add_qa(session_id, job["id"], result["question"], result.get("answer", ""))
            elif qa_list:
                store.update_qa(qa_list[-1]["id"], result.get("answer", ""))
        elif result.get("artifact"):
            store.save_artifact(session_id, job["id"], tab, result["artifact"])
        # classify_turn (above) always ran for this message regardless of check_preferences,
        # so it's fully considered either way - mark it so a /tools/preference_learning bulk
        # run never re-spends a call on it. Only reached once the turn's succeeded end to end;
        # a message from a turn that raised before here is left unmarked, for the bulk run to
        # pick up later (see src/learning.py).
        store.mark_message_preference_checked(user_message_id)
        return assistant_message_id, None
    except RuntimeError as e:
        return None, str(e)
