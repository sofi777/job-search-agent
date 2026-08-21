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
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
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
# A flaky connection (dropped wifi/VPN mid-handshake) surfaces as ssl.SSLError or
# urllib.error.URLError, not an HTTPError - OpenRouter never saw the request, so it's
# worth a couple of quick retries rather than failing the whole call outright.
TRANSIENT_ERROR_MAX_RETRIES = 2
TRANSIENT_ERROR_BACKOFF_SECONDS = 2
TRANSIENT_ERRORS = (ssl.SSLError, socket.error, urllib.error.URLError)
# Generous on purpose: reasoning models spend tokens "thinking" before ever emitting the
# reply, and the visible reply itself is JSON wrapping a few-hundred-word artifact - a too-low
# cap truncates mid-generation, coming back either as content: null or, worse, a real-looking
# but incomplete JSON string that fails to parse (see send_chat's finish_reason check).
MAX_TOKENS = 8000

# Shown in the model dropdown. Free options first, then paid ones cheapest to priciest.
# Browse the full catalog at https://openrouter.ai/models.
# deepseek/deepseek-r1:free was here until it was pulled from OpenRouter's catalog entirely
# (calling it now 404s: "unavailable for free") - replaced with nvidia/nemotron-3-super-120b-a12b:free,
# picked because it's large (120B), non-reasoning (no ":thinking" burn-the-budget risk, see
# gpt-oss-20b below), and flags structured_outputs support in OpenRouter's own model metadata.
# google/gemini-2.5-flash and qwen/qwen3.8-max were added after a real comparison against this
# app's actual task (strict-JSON creative writing) measured on real production data: gemini-2.5-flash
# had the best verified structured-output reliability of anything compared (99.8-99.9% schema-
# enforced JSON success) at ~$0.002/cover-letter-generation; qwen3.8-max was the one model ranking
# well on both instruction-following AND creative writing at once, priced close to Sonnet.
MODEL_OPTIONS = [
    "google/gemma-4-26b-a4b-it:free",
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "qwen/qwen3.8-max",
    "anthropic/claude-sonnet-5",
]

# Fallback order when a free model is rate-limited (see send_chat) - by suitability for this
# app's actual task (JSON creative writing: cover letters/resumes/Q&A), not MODEL_OPTIONS'
# display order. gpt-oss-20b is a reasoning model that can burn its whole token budget
# "thinking" and return content: null on exactly this kind of task (see DEFAULT_MODEL above),
# so it's demoted to last resort.
FREE_MODEL_PRIORITY = [
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-20b:free",
]

# Chat history sent to the model, capped to the last N messages (~3 exchanges). The current
# artifact/Q&A list already carries the full up-to-date state into every prompt, so older
# exchanges are mostly redundant for continuing edits - the model doesn't need to re-see turn 1
# by turn 6. Doesn't affect what's stored/displayed (see store.get_chat_for_display).
HISTORY_MAX_MESSAGES = 6

# Anthropic (via OpenRouter) discounts repeat input tokens ~90% within a short cache TTL when
# marked with cache_control. Only these models get the split-prompt/cache_control treatment in
# _build_system_content - other providers either ignore the field or don't support it the same
# way, so they get the plain single-string prompt as before.
ANTHROPIC_MODEL_PREFIX = "anthropic/"


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


class UnusableReply(RuntimeError):
    """The API call itself succeeded, but the reply can't be used as-is (unparseable JSON, cut
    off by the token cap, or empty). Distinct from a plain RuntimeError (HTTP/config/rate-limit
    failures) so callers can retry only this kind - retrying an HTTP error wouldn't help and
    would just burn more of a capped budget for nothing."""


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
    body = json.dumps({"model": model, "messages": messages, "max_tokens": MAX_TOKENS}).encode()

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
USAGE_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "usage.json"

# Rough public per-1M-token USD pricing (input, output) for cost estimates - OpenRouter
# doesn't return actual cost in the response. Free models are $0. Unlisted models fall back
# to DEFAULT_PRICING rather than guessing. Update alongside MODEL_OPTIONS.
MODEL_PRICING = {
    "google/gemma-4-26b-a4b-it:free": (0, 0),
    "openai/gpt-oss-20b:free": (0, 0),
    "nvidia/nemotron-3-super-120b-a12b:free": (0, 0),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "google/gemini-2.5-flash": (0.30, 2.50),
    "qwen/qwen3.8-max": (2.00, 6.00),
    "anthropic/claude-sonnet-5": (3.00, 15.00),
}
DEFAULT_PRICING = (0, 0)


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


def _estimate_cost(model, usage):
    input_price, output_price = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return round(
        usage.get("prompt_tokens", 0) / 1_000_000 * input_price
        + usage.get("completion_tokens", 0) / 1_000_000 * output_price,
        6,
    )


def _log_usage(model, usage):
    """Append one entry to data/usage.json for every real LLM call - see CLAUDE.md's LLM
    usage tracking rule. send_chat is the single choke point every caller in this app goes
    through, so this is the only place usage ever gets logged."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "provider": model.split("/", 1)[0] if "/" in model else "unknown",
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "estimated_cost_usd": _estimate_cost(model, usage),
    }
    try:
        log = json.loads(USAGE_LOG_PATH.read_text()) if USAGE_LOG_PATH.exists() else []
    except json.JSONDecodeError:
        log = []
    log.append(entry)
    try:
        USAGE_LOG_PATH.write_text(json.dumps(log, indent=2))
    except OSError:
        pass  # never let usage tracking break a real request


def _post_with_backoff(messages, model, api_key):
    """POST, retrying on two different kinds of failure:
    - a transient network/SSL error (TRANSIENT_ERRORS) - the request never reached
      OpenRouter, so it's retried up to TRANSIENT_ERROR_MAX_RETRIES times with a short
      fixed backoff.
    - a 429, retried once after OpenRouter's suggested wait (capped at
      MAX_RATE_LIMIT_WAIT_SECONDS).
    Raises the last error - HTTPError or a transient one - if still failing, unread, so
    the caller can inspect/fall back on it."""
    for attempt in range(TRANSIENT_ERROR_MAX_RETRIES + 1):
        try:
            return _post(messages, model, api_key)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            wait = min(_retry_after_seconds(e.read().decode()) or 5, MAX_RATE_LIMIT_WAIT_SECONDS)
            time.sleep(wait)
            return _post(messages, model, api_key)
        except TRANSIENT_ERRORS:
            if attempt == TRANSIENT_ERROR_MAX_RETRIES:
                raise
            time.sleep(TRANSIENT_ERROR_BACKOFF_SECONDS)


def send_chat(messages, model=DEFAULT_MODEL):
    """Send a full message list ([{role, content}, ...]) to OpenRouter. Returns (content,
    used_model, usage) - used_model equals `model` unless a fallback kicked in (see below), so
    callers that care can tell the user their reply came from a different model. usage is
    OpenRouter's {"prompt_tokens", "completion_tokens", "total_tokens", ...} dict, or {} if the
    response didn't include one.

    Retries transient network/SSL errors (see TRANSIENT_ERROR_MAX_RETRIES) and, once on a
    429 (rate limit) per model, waiting however long OpenRouter says to (see
    _post_with_backoff). If `model` is one of FREE_MODELS and still rate-limited after
    that, or its reply is unusable (empty / cut off by the MAX_TOKENS cap - see UnusableReply
    below), falls back through FREE_MODEL_PRIORITY in order (skipping `model` itself) -
    OpenRouter's free tier gets rate-limited per-model, not per-account, and free models vary
    in how often they blow the token budget on a given prompt, so a sibling free model is often
    fine even when the requested one isn't. A non-free `model` gets no fallback: any failure
    raises immediately. Exhausting every free fallback raises the last error seen (RuntimeError
    for HTTP failures, UnusableReply for empty/cut-off replies).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to webapp/.env.")

    if model in FREE_MODEL_PRIORITY:
        candidates = [model] + [m for m in FREE_MODEL_PRIORITY if m != model]
    else:
        candidates = [model]

    last_error = None
    for candidate in candidates:
        try:
            data = _post_with_backoff(messages, candidate, api_key)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise RuntimeError(_format_error(e.code, e.read().decode())) from e
            last_error = RuntimeError(_format_error(e.code, e.read().decode()))
            continue

        _log_last_call(messages, candidate, data)
        _log_usage(candidate, data.get("usage") or {})

        if not data.get("choices"):
            # A 200 response missing "choices" - seen on OpenRouter free-tier hiccups where
            # the body is an error payload instead of a completion. Same fallback path as an
            # unusable reply rather than a raw KeyError, so one bad response doesn't crash a
            # whole calling loop (e.g. scanner.run_scan scoring several jobs in a row).
            detail = (data.get("error") or {}).get("message") or "no choices in response"
            last_error = UnusableReply(f"{candidate} returned an unusable reply ({detail}).")
            continue

        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason", "unknown")
        if content is None:
            # Seen with reasoning-heavy free models that exhaust their token budget
            # "thinking" before emitting a reply. Try the next free model rather than
            # failing outright - see the fallback note above.
            last_error = UnusableReply(
                f"{candidate} returned no reply (finish_reason={finish_reason}). "
                "This can happen with reasoning models running out of budget before answering - "
                "try a different model from the dropdown."
            )
            continue
        if finish_reason == "length":
            # content is non-None but was cut off mid-generation by the MAX_TOKENS cap - e.g. a
            # reasoning model spent most of its budget "thinking" and ran out partway through the
            # visible JSON reply. Downstream json.loads() would fail on this too, but with a
            # confusing "malformed JSON" message that doesn't explain why; catching it here first
            # gives a clear, specific error and, for a free model, a shot at a sibling that
            # doesn't burn its budget on this same prompt.
            last_error = UnusableReply(
                f"{candidate}'s reply was cut off before finishing (hit the {MAX_TOKENS}-token "
                "limit). Try a shorter message, or a different model from the dropdown."
            )
            continue
        return content, candidate, data.get("usage") or {}

    raise last_error


def send_message(message, model=DEFAULT_MODEL):
    """Send a single user message, return (content, used_model, usage). Thin wrapper over send_chat."""
    return send_chat([{"role": "user", "content": message}], model)


def strip_json_fence(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    return re.sub(r"```$", "", text).strip()


def _load_prompt(name):
    return (PROMPTS_DIR / name).read_text()


def _parse_json_reply(reply):
    text = strip_json_fence(reply)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Some models add commentary before/after the JSON object despite being told not to -
    # try the outermost {...} span before giving up, rather than failing on stray prose alone.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise UnusableReply(f"Could not parse the model's reply as JSON: {reply[:200]}")


JSON_RETRY_NUDGE = (
    "Your last reply wasn't usable - it was either not valid JSON, left the artifact/answer "
    "field empty, or ran out of space before finishing. Reply again with ONLY the JSON object "
    "described earlier, no other text before or after it, no extended reasoning first, and "
    "keep the reply field brief so there's room to fit the full artifact/answer content."
)


def _tailor_reply_incomplete(artifact_type, data, current_artifact_text):
    """True if the model returned parseable JSON but skipped the actual content - e.g. left
    artifact/answer empty on what should have been a first draft. Seen intermittently across
    models (not just one), so this is checked generically rather than special-cased per model.
    """
    if artifact_type == "qa":
        return data.get("action") == "new_question" and not data.get("answer")
    return not current_artifact_text and not data.get("artifact")


_CITATION_MARKER = re.compile(r"\s*\[Source\s+\d+\]")


def strip_citations(text):
    """Remove any '[Source N]' markers from artifact/answer text. The prompt already tells
    the model citations belong only in the conversational reply, never here (see
    src/prompts/*.txt) - free/small models don't always follow that reliably, so this is a
    safety net, not the primary enforcement."""
    if not text:
        return text
    return re.sub(r"[ \t]{2,}", " ", _CITATION_MARKER.sub("", text)).strip()


# ---- tailoring chat -------------------------------------------------------

TAILOR_PROMPT_FILES = {
    "cover_letter": "cover_letter.txt",
    "resume": "resume.txt",
    "qa": "qa.txt",
}


def trim_history(history):
    """Cap chat history to the last HISTORY_MAX_MESSAGES before sending to the model."""
    return history[-HISTORY_MAX_MESSAGES:]


def classify_turn(artifact_type, message, has_existing_content):
    """Ask DEFAULT_MODEL (free) two things about this message in a single call: does
    it need fresh RAG retrieval, and does it look like it might reveal a durable
    style preference worth checking with revise_preferences? Combined into one
    call, rather than a separate retrieval call plus a word-count check, to
    minimize API calls per turn.

    Neither question is answerable from word count alone: a brand-new qa question
    ("Why us?") and pure feedback ("make it shorter") are both commonly short, but
    only one needs new facts pulled in or could reveal nothing preference-worthy.
    A real semantic read gets both right. Always makes this call, even on an
    obvious first message, for consistency - it's free-tier, so the only real
    cost is latency, not money.

    Returns {"needs_retrieval": bool, "reveals_preference": bool}, defaulting both
    to True if the call fails or the reply is unparseable - the safe direction for
    both: doing the real thing when unsure never hurts correctness, it just costs
    a bit more.

    Skips the call entirely when has_existing_content is False: a from-scratch generation
    request always needs retrieval (there's nothing yet to treat as "just a stylistic edit"),
    and reveals_preference is moot in that case anyway (app.py only checks it when there's
    existing content to compare against). Not just an optimization - DEFAULT_MODEL was seen
    answering needs_retrieval=False on this exact case (~1 in 4 tries live), which silently
    skips retrieval on the highest-stakes turn to get it wrong on: the model then has no
    facts to draw from or cite, and either fabricates or refuses.
    """
    if not has_existing_content:
        return {"needs_retrieval": True, "reveals_preference": False}

    existing_state = (
        "Some questions have already been answered for this job."
        if artifact_type == "qa"
        else "A draft already exists." if has_existing_content else "Nothing has been written yet."
    )
    prompt = _load_prompt("classify_turn.txt").format(
        artifact_type=artifact_type, existing_state=existing_state, message=message
    )
    try:
        reply, _used_model, _usage = send_chat([{"role": "user", "content": prompt}], DEFAULT_MODEL)
        data = _parse_json_reply(reply)
    except RuntimeError:
        return {"needs_retrieval": True, "reveals_preference": True}
    return {
        "needs_retrieval": bool(data.get("needs_retrieval", True)),
        "reveals_preference": bool(data.get("reveals_preference", True)),
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


def build_tailor_system_prompt_parts(artifact_type, job, profile, preferences, current_artifact_text, retrieved_context=""):
    """Build the tailoring system prompt as (cacheable, dynamic) parts instead of one string.

    `cacheable` (job/profile/instructions/JSON-key spec) is identical across every turn of a
    job+tab thread - see _build_system_content, which marks it as an Anthropic prompt-caching
    breakpoint. `dynamic` (the current draft + this turn's retrieved context) changes every
    turn and would never benefit from caching anyway, so it's kept separate and last.

    `preferences` is {category: text} for general/cover_letter/resume/qa. `retrieved_context`
    is format_retrieved_context()'s output - the top-k chunks for this turn's query, already
    numbered/scored (see store.retrieve_context()).
    """
    context = _load_prompt("_context.txt").format(
        job_title=job["title"],
        job_company=job["company"],
        job_location=job.get("location", ""),
        job_description=job.get("description", ""),
        profile_name=profile.get("name", "the candidate"),
        profile_roles=", ".join(profile.get("roles", [])) or "not specified",
        profile_home_address=profile.get("home_address", "not specified"),
        pref_general=preferences.get("general") or "(none yet)",
    )
    template = _load_prompt(TAILOR_PROMPT_FILES[artifact_type])
    cacheable = template.format(context=context, pref_category=preferences.get(artifact_type) or "(none yet)")

    artifact_label = "Questions already answered for this job" if artifact_type == "qa" else "Current draft (empty if none written yet)"
    dynamic = (
        f"{artifact_label}:\n---\n{current_artifact_text or '(none yet)'}\n---\n\n"
        f"Retrieved context for this turn:\n{retrieved_context or '(nothing retrieved for this turn)'}"
    )
    return cacheable, dynamic


def _build_system_content(cacheable, dynamic, model):
    """The system message's `content`: a plain string for most models, or Anthropic's
    content-block format with a cache_control breakpoint after `cacheable` for anthropic/*
    models (see ANTHROPIC_MODEL_PREFIX) - OpenRouter passes this through to Anthropic's
    prompt caching, discounting `cacheable` ~90% on repeat calls within its cache TTL. That
    matters here because `cacheable` is identical across every turn of a regenerate loop on
    the same job, while `dynamic` never repeats and wouldn't be cacheable anyway.
    """
    if not model.startswith(ANTHROPIC_MODEL_PREFIX):
        return f"{cacheable}\n\n{dynamic}"
    return [
        {"type": "text", "text": cacheable, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic},
    ]


def run_tailor_turn(
    artifact_type, job, profile, preferences, history, current_artifact_text, user_message, model, retrieved_context=""
):
    """Run one turn of a tailoring chat. Pure function - no DB access.

    `history` is the prior [{role, content}, ...] for this job+tab (assistant
    entries are the raw JSON replies aren't re-sent; only reply text is, see
    caller). `retrieved_context` is format_retrieved_context()'s output for this
    turn's query (see build_retrieval_query). Returns (data, used_model, usage):
    data is the parsed JSON dict from the model - shape depends on artifact_type
    (cover_letter/resume: reply+artifact; qa: reply+action+question+answer);
    used_model/usage are send_chat's, passed through for callers that log/rate
    the response (see app.py tailor_message).
    """
    cacheable, dynamic = build_tailor_system_prompt_parts(
        artifact_type, job, profile, preferences, current_artifact_text, retrieved_context
    )
    system_content = _build_system_content(cacheable, dynamic, model)
    messages = [{"role": "system", "content": system_content}, *history, {"role": "user", "content": user_message}]
    try:
        reply, used_model, usage = send_chat(messages, model)
        data = _parse_json_reply(reply)
        if _tailor_reply_incomplete(artifact_type, data, current_artifact_text):
            raise UnusableReply(f"{model} left the artifact/answer field empty on what should have been a first draft.")
    except UnusableReply:
        # One retry with an explicit correction, same model - no prompt wording is 100%
        # reliable across every model (seen intermittently as prose instead of JSON, JSON with
        # an empty artifact, and replies cut off by the token cap before finishing), so this
        # self-corrects generically instead of hardcoding a workaround for one provider. Plain
        # RuntimeErrors (HTTP/config/rate-limit failures) are not caught here - retrying those
        # wouldn't help and would just spend more of a capped budget for nothing.
        retry_messages = messages + [{"role": "user", "content": JSON_RETRY_NUDGE}]
        # send_chat logs usage for both calls independently (see _log_usage) - usage here just
        # needs to reflect the reply actually used, so the retry's usage replaces, not merges.
        reply, used_model, usage = send_chat(retry_messages, model)
        data = _parse_json_reply(reply)  # let this raise clearly if it's still broken
    if data.get("artifact"):
        data["artifact"] = strip_citations(data["artifact"])
    if data.get("answer"):
        data["answer"] = strip_citations(data["answer"])
    if used_model != model and data.get("reply"):
        data["reply"] = f"_(Note: {model} was rate-limited, so I used {used_model} for this reply.)_\n\n{data['reply']}"
    return data, used_model, usage


# ---- cross-job preference learning ----------------------------------------

def revise_preferences(artifact_type, feedback, current_content, preferences, model):
    """Decide whether a feedback message reveals a durable preference, and if so, return its revision.

    Pure function - no DB access. `preferences` is {category: text}. Returns
    None if nothing should change, otherwise {"category": ..., "text": ...}.
    Runs on `model` - whatever the user picked for the main generation - rather
    than a fixed model, so preference-learning never introduces a paid call the
    user didn't choose: if they're running fully on free models, this stays
    free too, keeping cost consistent with what they picked.
    """
    prompt = _load_prompt("preferences_update.txt").format(
        artifact_type=artifact_type,
        feedback=feedback,
        current_content=(current_content or "")[:2000],
        pref_general=preferences.get("general") or "(none yet)",
        pref_category=preferences.get(artifact_type) or "(none yet)",
    )
    reply, _used_model, _usage = send_chat([{"role": "user", "content": prompt}], model)
    data = _parse_json_reply(reply)
    if not data.get("changed"):
        return None
    return {"category": data.get("category") or "general", "text": data.get("text") or ""}


# ---- global assistant chat -------------------------------------------------

ASSISTANT_JOBS_SUMMARY_LIMIT = 30


def _format_jobs_summary(jobs):
    if not jobs:
        return "(none yet)"
    # Highest match first - the jobs most likely to come up in conversation, since the prompt
    # is capped rather than listing every job on a large dashboard.
    ranked = sorted(jobs, key=lambda j: j.get("match") if j.get("match") is not None else -1, reverse=True)
    lines = []
    for j in ranked[:ASSISTANT_JOBS_SUMMARY_LIMIT]:
        match = f"match {j['match']}%" if j.get("match") is not None else "not yet scored"
        lines.append(f"#{j['id']}: {j['title']} at {j['company']} - {j['status']}, {match}")
    if len(jobs) > ASSISTANT_JOBS_SUMMARY_LIMIT:
        lines.append(f"(+{len(jobs) - ASSISTANT_JOBS_SUMMARY_LIMIT} more, not shown)")
    return "\n".join(lines)


def answer_assistant_message(message, history, profile, preferences, jobs, model):
    """One turn of the general-purpose floating chat - not tied to a job. Grounded in
    cross-session memory: profile, learned writing preferences, and a compact jobs-on-
    dashboard summary (id/title/company/status/match - not full descriptions, to keep the
    prompt small and avoid leaking unrelated job text into an off-topic turn).

    Pure function, no DB access - same contract as run_tailor_turn. Plain conversational
    reply, not JSON (there's no artifact/structured decision here). Returns
    (reply_text, used_model, usage).
    """
    system_content = _load_prompt("assistant_chat.txt").format(
        profile_name=profile.get("name", "the candidate"),
        profile_roles=", ".join(profile.get("roles", [])) or "not specified",
        profile_home_address=profile.get("home_address", "not specified"),
        pref_general=preferences.get("general") or "(none yet)",
        jobs_summary=_format_jobs_summary(jobs),
    )
    messages = [{"role": "system", "content": system_content}, *trim_history(history), {"role": "user", "content": message}]
    reply, used_model, usage = send_chat(messages, model)
    return reply.strip(), used_model, usage


def _format_history_text(history):
    if not history:
        return "(nothing yet)"
    return "\n".join(f"{m['role']}: {m['content']}" for m in trim_history(history))


def route_assistant_turn(message, history, active_job, actions):
    """Classify one assistant-widget turn - same "prompt -> parsed JSON -> Python
    dispatches" shape as classify_turn. `active_job` is {"title", "company"} or None.
    `actions` is [{"id", "name", "description"}, ...] - every action the model may pick,
    built by src.assistant from live workflows.WORKFLOWS entries plus its own fixed-action
    catalogue; wiring up a new workflow's runner later makes it routable automatically, no
    change needed here.

    Returns {"action": one of `actions`' ids, "chat", or "unclear",
             "job_query": short phrase naming the job this turn is about, or None,
             "url": a job posting URL (only meaningful for "add_job_url"), or None,
             "status": a job status (only meaningful for "job_status"), or None}.

    Defaults to {"action": "chat", ...all else None} on any failure (RuntimeError, an
    unparseable reply, or an action id outside the allowed set) - unlike classify_turn's
    "default to the safe-to-over-trigger option" bias, wrongly firing a paid workflow or an
    unwanted document draft is the unsafe direction here, so the inert action is the only
    safe default. "unclear" is a deliberate model choice (message clearly wants an action but
    doesn't cleanly match one), not a failure fallback - see route_assistant_turn.txt.
    """
    allowed_actions = {a["id"] for a in actions} | {"chat", "unclear"}
    fallback = {"action": "chat", "job_query": None, "url": None, "status": None}

    action_list = "\n".join(f'- "{a["id"]}": {a["description"]}' for a in actions)
    active_job_line = f"{active_job['title']} at {active_job['company']}" if active_job else "(none)"
    prompt = _load_prompt("route_assistant_turn.txt").format(
        history_text=_format_history_text(history),
        active_job_line=active_job_line,
        message=message,
        action_list=action_list or "(none currently available)",
    )
    try:
        reply, _used_model, _usage = send_chat([{"role": "user", "content": prompt}], DEFAULT_MODEL)
        data = _parse_json_reply(reply)
    except RuntimeError:
        return fallback

    action = data.get("action")
    if action not in allowed_actions:
        return fallback
    return {
        "action": action,
        "job_query": data.get("job_query") or None,
        "url": data.get("url") or None,
        "status": data.get("status") or None,
    }
