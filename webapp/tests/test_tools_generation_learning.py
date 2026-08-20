"""New /components cards wiring existing functionality: tailored generation (job picker ->
/jobs/<id>/tailor) and preference learning (read-only view -> /preferences to edit). Neither
adds new generation/learning logic - see src/agents.run_tailor_turn / revise_preferences.
"""
import unittest

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import store, db


class TailoredGenerationToolTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_components_page_links_both_new_tools(self):
        html = self.client.get("/components").get_data(as_text=True)
        self.assertIn("/tools/tailored_generation", html)
        self.assertIn("/tools/preference_learning", html)

    def test_tailored_generation_lists_jobs_and_links_to_tailor_page(self):
        self.assertTrue(store.jobs, "sample jobs should be seeded for this test to be meaningful")
        job = store.jobs[0]
        html = self.client.get("/tools/tailored_generation").get_data(as_text=True)
        self.assertIn(job["title"], html)
        self.assertIn(f'value="{job["id"]}"', html)  # <option> for this job
        self.assertIn("/jobs/' + id + '/tailor", html)  # onclick builds the real tailor route

    def test_tailored_generation_empty_state(self):
        original = list(store.jobs)
        store.jobs.clear()
        try:
            html = self.client.get("/tools/tailored_generation").get_data(as_text=True)
            self.assertIn("No jobs on your dashboard yet", html)
        finally:
            store.jobs.extend(original)


class PreferenceLearningToolTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def tearDown(self):
        with db.db_transaction() as conn:
            conn.execute("UPDATE preferences SET text = '', previous_text = '', updated_at = NULL")

    def test_shows_learned_preference_text(self):
        store.save_preference("cover_letter", "Prefers a warm, direct opening line.")
        html = self.client.get("/tools/preference_learning").get_data(as_text=True)
        self.assertIn("Prefers a warm, direct opening line.", html)
        self.assertIn("/preferences", html)

    def test_empty_category_shows_placeholder_not_blank(self):
        html = self.client.get("/tools/preference_learning").get_data(as_text=True)
        self.assertIn("(none yet)", html)


if __name__ == "__main__":
    unittest.main()
