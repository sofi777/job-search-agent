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
- update `requirements.txt` if needed
- update `CLAUDE.md` on structure changes
- update the `README.md` whenever a new feature is added
- Do not do browser testing (like playwright) unless i explicitly tell you to
- Ask me questions before implementing if you are not sure, have missing information
- Commit after each logical change once it's tested — one coherent change per commit, working state only.

## Stack
- Flask, SQLite (built-in `sqlite3`), Jinja2 templates

## Code Structure
- **`app.py`** - Flask App entry point
- **`templates/`** - front end
- **`data/`** - all app data
- **`data/app.db`** - SQLite database, user profile + jobs + per-job progress
- **`src/`** - backend logic
- **`scripts/`** - quick scripting
- **`tests/`** - unit tests

## Final
- end with the header Summary and a 1-3 concise bullet points of what has been done under it. Each bullet point needs a bold prefix with a colon
- end with **"All Done"**

