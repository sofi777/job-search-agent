# CLAUDE.md

## Style
- concise, no filler
- clean, readable
- minimal comments and docs

## Rules
- minimal code and libraries
- modular, reusable, no repetition
- no hardcoding, use variables
- use HTML templates with base
- no fallback errors, fail clearly
- no em-dashes at all costs
- whenever you add a feature, create a concise, fast unit-test that verifies that the feature is working correctly including corner cases (and run it)
- update `requirements.txt` if needed
- update `CLAUDE.md` when a rule/convention changes; update `structure.md` when the
  code structure changes or you learn something about it worth recording (keep it
  concise) - they cover different things, check both
- update the `README.md` whenever a new feature is added
- always load API keys from .env and never hardcode or print them
- Do not do browser testing (like playwright) unless i explicitly tell you to
- Ask me questions before implementing if you are not sure, have missing information
- if you see repeated code, consolidate
- put things in variables and dont' repeat names
- one llm-based module used for anything that needs llm (`webapp/src/agents.py`)
- src/prompts for all prompts and all are in text file
- every LLM call goes through that module's `send_chat` and gets logged to
  `data/usage.json`, one entry per call: timestamp, model, provider,
  prompt/completion/total tokens, estimated cost. Visible in-app at `/usage`.
  Don't log usage anywhere else - `send_chat` is the single choke point.
## before push to githib
- run all unit test, fix any issues found. dont until all tests passed
- Push via gh CLI 
- always thin of security loopholes
- see [`ISSUES.md`](ISSUES.md) for known gaps to fix before a public deploy - add to
  it instead of silently accepting a new one

## Stack
- Flask, sqlite3, Jinja2 templates

## Code Structure
See [`structure.md`](structure.md) for the full breakdown - keep it up to date

## Final
- end with the header Summary and a 1-3 concise bullet points of what has been done under it. Each bullet point needs a bold prefix with a colon
- end with **"All Done"**

