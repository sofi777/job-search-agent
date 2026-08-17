# Open issues

Tracked here so they don't get lost. Check off and remove once fixed.

## Must fix before any public deploy

- [ ] **Fake login.** `login()` in `webapp/app.py` accepts any POST and logs the user
  in - no password check. `login.html` literally says "anything works (demo login)".
  Fine while the app only runs on localhost; not fine once it's reachable from the
  internet, since anyone who finds the URL gets full access, including the tailoring
  chat that spends real OpenRouter credits.
- [ ] **Hardcoded secret key.** `app.secret_key = "dev-only-secret-key"` in
  `webapp/app.py`, committed to a public repo. Flask uses this to sign session
  cookies, so anyone who reads the source can forge a valid "logged in" cookie
  without even hitting `/login`. Needs to move to an env var (`SECRET_KEY` in `.env`,
  random value, never committed).
