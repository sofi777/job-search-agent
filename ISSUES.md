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

## Known gaps

- [ ] **Location matching is exact-substring only.** `src/components/base.py`'s
  `location_match()` (used by `src/filters.py`'s hard eligibility/remote-country gate and
  by the ATS component) checks whether an allowed country name is a literal substring of
  the listing's location text - "US" or "USA" won't match an allowed "United States", and
  vice versa. A real listing could get wrongly filtered out (or through) on an
  abbreviation/alias mismatch. Fine for now (most sources return full country/city names);
  would need real geocoding or an alias table to be reliable.
- [ ] **Commute radius is a city-text match, not a real distance.** `src/filters.py`'s hard
  gate now drops onsite listings whose location text doesn't contain the user's home city
  (from `profile.home_address`), but `commute_miles` itself is still never read anywhere -
  it's "same city or not", not "within N miles". Needs real geocoding (address -> lat/lng
  + distance) to honor the actual radius the user set.
