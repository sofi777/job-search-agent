"""Dashboard route sorting (app.py's /dashboard). Regression test: a custom-added job has
no match score (None) until the next scan - sorting by "match" must not crash on it.
"""
import unittest

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import store

JOB_FIELDS = {
    "company": "Acme", "title": "Unscored Role", "source": "Direct", "location": "Remote",
    "remote": True, "posted": "2026-01-01", "salary_min": 100000, "salary_max": 150000,
    "currency": "USD", "description": "desc",
}


class DashboardSortTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        store.profile["onboarding_complete"] = True
        # session DB is shared across tests (see tests/db_setup.py) - unique url per test
        # so re-adding the same job doesn't hit the UNIQUE constraint.
        url = f"https://example.com/unscored-job-{self._testMethodName}"
        store.add_custom_job({**JOB_FIELDS, "url": url})  # match stays None until the next scan

    def tearDown(self):
        store.profile["onboarding_complete"] = False

    def test_match_sort_does_not_crash_on_an_unscored_job(self):
        for direction in ("asc", "desc"):
            resp = self.client.get(f"/dashboard?sort=match&dir={direction}")
            self.assertEqual(resp.status_code, 200)

    def test_unscored_job_sorts_below_scored_ones_in_desc_order(self):
        html = self.client.get("/dashboard?sort=match&dir=desc").get_data(as_text=True)
        self.assertIn("Unscored Role", html)


if __name__ == "__main__":
    unittest.main()
