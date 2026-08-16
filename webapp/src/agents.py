"""AI logic: OpenRouter transport, prompt templates, and tailoring/preferences orchestration.

send_message/send_chat talk to OpenRouter directly. Swap DEFAULT_MODEL, or let
the user pick from MODEL_OPTIONS per message, to change models without
touching callers. run_tailor_turn and revise_preferences are pure functions
of their arguments (no DB access) - callers (app.py) fetch state via store
and persist the results.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# openai/gpt-oss-20b:free is a reasoning model that can burn its whole token budget
# "thinking" and return content: null on longer creative-writing-in-JSON tasks (seen
# live while building the tailoring chat - see run_tailor_turn). google/gemma's
# instruct models don't have that failure mode and are just as fast/free.
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MAX_RATE_LIMIT_WAIT_SECONDS = 30

# Shown in the model dropdown. Free options first, then a couple of paid ones.
# Browse the full catalog at https://openrouter.ai/models.
MODEL_OPTIONS = [
    "google/gemma-4-26b-a4b-it:free",
    "openai/gpt-oss-20b:free",
    "deepseek/deepseek-r1:free",
    "openai/gpt-4o-mini",
    "anthropic/claude-sonnet-5",
]


def _load_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()


def _format_error(status, body_text):
    try:
        error = json.loads(body_text)["error"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return f"OpenRouter request failed ({status}): {body_text}"
    detail = error.get("metadata", {}).get("raw") or ""
    return f"OpenRouter error ({status}): {error.get('message', 'request failed')}. {detail}".strip()


def _retry_after_seconds(body_text):
    try:
        return json.loads(body_text)["error"]["metadata"]["retry_after_seconds"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _post(messages, model, api_key):
    # max_tokens is generous on purpose: reasoning models spend tokens "thinking"
    # before ever emitting the reply, and a too-low cap truncates them mid-thought,
    # coming back with content: null (see send_chat's check below).
    body = json.dumps({"model": model, "messages": messages, "max_tokens": 4000}).encode()

    request = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


LAST_CALL_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "last_llm_call.json"


def _log_last_call(messages, model, response_data):
    """Overwrite data/last_llm_call.json with the full request + usage of the most recent call.

    Debug aid only (see scripts/show_last_call.py) - not read by the app itself.
    """
    try:
        LAST_CALL_LOG_PATH.write_text(json.dumps({
            "model": model,
            "messages": messages,
            "usage": response_data.get("usage"),
            "reply_content": response_data["choices"][0]["message"]["content"],
        }, indent=2))
    except OSError:
        pass  # debug aid only, never let logging break a real request


def send_chat(messages, model=DEFAULT_MODEL):
    """Send a full message list ([{role, content}, ...]) to OpenRouter, return the assistant's text reply.

    Retries once on a 429 (rate limit), waiting however long OpenRouter says
    to (capped at MAX_RATE_LIMIT_WAIT_SECONDS). Any other failure, or a 429
    that's still rate-limited after that wait, raises RuntimeError with a
    readable message instead of the raw error JSON.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to webapp/.env.")

    try:
        data = _post(messages, model, api_key)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        if e.code == 429:
            wait = min(_retry_after_seconds(body_text) or 5, MAX_RATE_LIMIT_WAIT_SECONDS)
            time.sleep(wait)
            try:
                data = _post(messages, model, api_key)
            except urllib.error.HTTPError as retry_e:
                raise RuntimeError(_format_error(retry_e.code, retry_e.read().decode())) from retry_e
        else:
            raise RuntimeError(_format_error(e.code, body_text)) from e

    _log_last_call(messages, model, data)

    choice = data["choices"][0]
    content = choice["message"]["content"]
    if content is None:
        # Seen with reasoning-heavy free models that exhaust their token budget
        # "thinking" before emitting a reply. Fail clearly instead of crashing
        # downstream on None; the caller can retry with a different model.
        finish_reason = choice.get("finish_reason", "unknown")
        raise RuntimeError(
            f"{model} returned no reply (finish_reason={finish_reason}). "
            "This can happen with reasoning models running out of budget before answering - "
            "try a different model from the dropdown."
        )
    return content


def send_message(message, model=DEFAULT_MODEL):
    """Send a single user message and return the reply. Thin wrapper over send_chat."""
    return send_chat([{"role": "user", "content": message}], model)


def strip_json_fence(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    return re.sub(r"```$", "", text).strip()


def _load_prompt(name):
    return (PROMPTS_DIR / name).read_text()


def _parse_json_reply(reply):
    try:
        return json.loads(strip_json_fence(reply))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse the model's reply as JSON: {reply[:200]}") from e


# ---- tailoring chat -------------------------------------------------------

TAILOR_PROMPT_FILES = {
    "cover_letter": "cover_letter.txt",
    "resume": "resume.txt",
    "qa": "qa.txt",
}


def build_retrieval_query(job, user_message):
    """The text embedded to search the knowledge base for one turn.

    Not just the raw user message: a short instruction like "generate the cover
    letter" carries almost no semantic signal on its own, so chunks retrieved
    from it alone tend to be near-random. Folding in the job title + description
    means retrieval is grounded in what THIS job actually needs, even when the
    message itself is generic.
    """
    return f"{job['title']}\n{job.get('description', '')}\n{user_message}".strip()


def format_retrieved_context(chunks):
    """Format retrieve()'s output ([{chunk_id, text, filename, score}, ...]) as the numbered,
    scored source list the prompt and the [Source N] citation convention both refer to."""
    if not chunks:
        return "(nothing relevant found - the knowledge base may be empty, or nothing on file matches this request)"
    return "\n\n".join(
        f"[Source {i}] (similarity: {c['score']}, from {c['filename']})\n{c['text']}"
        for i, c in enumerate(chunks, start=1)
    )


def build_tailor_system_prompt(artifact_type, job, profile, preferences, current_artifact_text, retrieved_context=""):
    """Assemble the system prompt for one tailoring turn from the shared context + type-specific template.

    `preferences` is {category: text} for general/cover_letter/resume/qa.
    `retrieved_context` is format_retrieved_context()'s output - the top-k chunks
    for this turn's query, already numbered/scored (see store.retrieve_context()).
    """
    context = _load_prompt("_context.txt").format(
        job_title=job["title"],
        job_company=job["company"],
        job_location=job.get("location", ""),
        job_description=job.get("description", ""),
        profile_name=profile.get("name", "the candidate"),
        profile_roles=", ".join(profile.get("roles", [])) or "not specified",
        profile_home_address=profile.get("home_address", "not specified"),
        retrieved_context=retrieved_context or "(nothing retrieved for this turn)",
        pref_general=preferences.get("general") or "(none yet)",
    )
    template = _load_prompt(TAILOR_PROMPT_FILES[artifact_type])
    return template.format(
        context=context,
        pref_category=preferences.get(artifact_type) or "(none yet)",
        current_artifact=current_artifact_text or "(none yet)",
    )


def run_tailor_turn(
    artifact_type, job, profile, preferences, history, current_artifact_text, user_message, model, retrieved_context=""
):
    """Run one turn of a tailoring chat. Pure function - no DB access.

    `history` is the prior [{role, content}, ...] for this job+tab (assistant
    entries are the raw JSON replies aren't re-sent; only reply text is, see
    caller). `retrieved_context` is format_retrieved_context()'s output for this
    turn's query (see build_retrieval_query). Returns the parsed JSON dict from
    the model - shape depends on artifact_type (cover_letter/resume: reply+
    artifact; qa: reply+action+question+answer).
    """
    system_prompt = build_tailor_system_prompt(
        artifact_type, job, profile, preferences, current_artifact_text, retrieved_context
    )
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_message}]
    reply = send_chat(messages, model)
    return _parse_json_reply(reply)


# ---- cross-job preference learning ----------------------------------------

def revise_preferences(artifact_type, feedback, current_content, preferences, model):
    """Decide whether a feedback message reveals a durable preference, and if so, return its revision.

    Pure function - no DB access. `preferences` is {category: text}. Returns
    None if nothing should change, otherwise {"category": ..., "text": ...}.
    """
    prompt = _load_prompt("preferences_update.txt").format(
        artifact_type=artifact_type,
        feedback=feedback,
        current_content=(current_content or "")[:2000],
        pref_general=preferences.get("general") or "(none yet)",
        pref_category=preferences.get(artifact_type) or "(none yet)",
    )
    reply = send_chat([{"role": "user", "content": prompt}], model)
    data = _parse_json_reply(reply)
    if not data.get("changed"):
        return None
    return {"category": data.get("category") or "general", "text": data.get("text") or ""}
