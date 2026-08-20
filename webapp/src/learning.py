"""Preference learning replay: run_learning() walks tailoring-chat feedback that hasn't
been checked yet (chat_messages.preference_checked_at IS NULL - see db.py migration) and
applies the same decision app.py's _run_pane_turn makes live, per turn: does this message
reveal a durable writing-style preference (agents.classify_turn), and if so, what should
change (agents.revise_preferences). Run on demand from /tools/preference_learning, across
every job or scoped to one, to catch up on feedback the live hook never saw (older chats,
or a turn where the live check itself failed).

mode="test" makes real LLM calls (not a fixture - it's meant to preview real classification/
revision behavior) but never persists: no preference saved, no message marked checked - so
it can be re-run freely and always previews the same unprocessed set. mode="live" applies
revisions and marks every message it looked at as checked (whether or not it changed
anything), so a repeat run only ever processes feedback that arrived since - same
incremental contract as the live per-turn hook already keeps.
"""
from . import agents, store


def run_learning(scope_job_id=None, mode="test", model=None):
    live = mode == "live"
    model = (model or agents.DEFAULT_MODEL) if live else agents.DEFAULT_MODEL
    run_id = store.start_preference_learning_run(scope_job_id, mode, model)

    messages = store.get_unchecked_feedback_messages(scope_job_id)
    preferences = store.get_preferences()
    processed, updated, error = 0, 0, None

    for message in messages:
        try:
            # Gate on the PRE-turn draft (was there already something to give feedback on) -
            # same check _run_pane_turn makes before ever calling classify_turn.
            had_existing_draft = bool(store.get_artifact_text_before(message["session_id"], message["id"]))
            if had_existing_draft:
                classification = agents.classify_turn(message["type"], message["content"], True)
                if classification["reveals_preference"]:
                    # revise_preferences judges against the POST-turn result, same as live -
                    # see store.get_artifact_text_after's docstring for why.
                    resulting_text = store.get_artifact_text_after(message["session_id"], message["id"])
                    if resulting_text:
                        revision = agents.revise_preferences(
                            message["type"], message["content"], resulting_text, preferences, model
                        )
                        if revision:
                            preferences[revision["category"]] = revision["text"]
                            store.save_preference_learning_result(
                                run_id, message["job_id"], revision["category"], message["content"][:140]
                            )
                            updated += 1
                            if live:
                                store.save_preference(revision["category"], revision["text"])
            processed += 1
            if live:
                store.mark_message_preference_checked(message["id"])
        except Exception as e:
            error = f"Stopped after {processed} of {len(messages)} messages: {e}"
            break

    store.finish_preference_learning_run(run_id, "error" if error else "ok", processed, updated, error)
    return run_id
