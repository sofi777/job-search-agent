"""Component settings/run wiring (app.py's /components/<id> routes + store.get_component_config):
unsaved settings track the live profile instead of freezing on first view, Run uses whatever's
currently in the form without persisting it, and Save settings is the only thing that writes.
"""
import unittest
from unittest.mock import patch

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import store, db
from src import components as comp

SERPAPI_QUERY_FORM = {
    "query_label": "Primary", "query_terms": "Made Up Title", "query_match": "OR",
    "query_location": "Canada", "query_date_posted": "week", "query_employment_types": "",
    "query_remote_only": "",
    "use_followed_companies": "", "followed_location": "", "followed_date_posted": "month",
    "followed_employment_types": "", "followed_remote_only": "",
}


class ComponentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self._original_roles = list(store.profile["roles"])
        store.profile["onboarding_complete"] = True
        store.save_profile()

    def tearDown(self):
        store.profile["roles"] = self._original_roles
        store.save_profile()
        with db.db_transaction() as conn:
            conn.execute("DELETE FROM component_settings WHERE component_id = 'serpapi'")

    def test_unsaved_settings_track_live_profile_changes(self):
        store.profile["roles"] = ["Growth PM"]
        store.save_profile()
        html = self.client.get("/components/serpapi").get_data(as_text=True)
        self.assertIn("Growth PM", html)

        store.profile["roles"] = ["Staff PM"]
        store.save_profile()
        html = self.client.get("/components/serpapi").get_data(as_text=True)
        self.assertIn("Staff PM", html)
        self.assertNotIn("Growth PM", html)

    def test_viewing_settings_page_does_not_persist_anything(self):
        self.client.get("/components/serpapi")
        self.assertIsNone(db.get_component_config("serpapi"))

    def test_run_uses_posted_form_values_not_any_saved_config(self):
        # Save one config, then Run with different, never-saved values - the run must see the
        # posted ones, not what's on file.
        self.client.post("/components/serpapi/settings", data=SERPAPI_QUERY_FORM)

        captured = {}

        def fake_run(config, test_mode):
            captured["config"] = config
            return [], None

        with patch.dict(comp.COMPONENTS["serpapi"], {"run": fake_run}):
            resp = self.client.post("/components/serpapi/run", data={
                **SERPAPI_QUERY_FORM, "query_terms": "Never Saved Title", "mode": "test",
            })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(captured["config"]["queries"][0]["terms"], ["Never Saved Title"])

    def test_run_never_persists_settings(self):
        self.client.post("/components/serpapi/run", data={**SERPAPI_QUERY_FORM, "mode": "test"})
        self.assertIsNone(db.get_component_config("serpapi"))

    def test_save_settings_persists(self):
        self.client.post("/components/serpapi/settings", data=SERPAPI_QUERY_FORM)
        saved = db.get_component_config("serpapi")
        self.assertEqual(saved["queries"][0]["terms"], ["Made Up Title"])

    def test_saved_settings_do_not_track_further_profile_edits(self):
        """Once explicitly saved, the config is the user's own choice - unlike an unsaved one,
        it should NOT keep tracking the profile."""
        self.client.post("/components/serpapi/settings", data=SERPAPI_QUERY_FORM)
        store.profile["roles"] = ["Some Other Role"]
        store.save_profile()
        html = self.client.get("/components/serpapi").get_data(as_text=True)
        self.assertIn("Made Up Title", html)
        self.assertNotIn("Some Other Role", html)


if __name__ == "__main__":
    unittest.main()
