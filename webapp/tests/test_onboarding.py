"""Onboarding wizard routes (app.py's /onboarding/<step>) - each step actually persists to the
DB (not just store.profile in memory), and a failed resume upload doesn't clobber existing
profile fields. See tests/test_onboarding_upload_ui.py for the resume dropzone markup itself.
"""
import io
import unittest

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import store

GOOD_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 44>>stream
BT /F1 12 Tf 20 100 Td (Sample resume text) Tj ET
endstream
endobj
xref
0 6
trailer<</Size 6/Root 1 0 R>>
startxref
0
%%EOF"""


def _fresh_db_row():
    """Re-read the user row straight from the DB, bypassing store.profile's in-memory cache -
    proof a value actually persisted, not just that the process-local dict was mutated."""
    return store.db.ensure_demo_user()


def _clear_profile_documents():
    """The session DB is shared across this module's tests (see tests/db_setup.py) - reset_profile()
    only resets the users row, not uploaded documents, so clear those too for test isolation."""
    for doc in store.get_profile_documents():
        store.delete_profile_document(doc["type"])


class OnboardingWizardTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        store.reset_profile()
        _clear_profile_documents()

    def tearDown(self):
        store.reset_profile()
        _clear_profile_documents()

    def test_resume_upload_success_persists_filename_roles_address(self):
        resp = self.client.post(
            "/onboarding/resume",
            data={"resume": (io.BytesIO(GOOD_PDF), "resume.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 302)
        row = _fresh_db_row()
        self.assertEqual(row["resume_filename"], "resume.pdf")
        self.assertTrue(row["roles"])
        self.assertTrue(row["home_address"])
        self.assertEqual([d["type"] for d in store.get_profile_documents()], ["resume"])

    def test_resume_upload_required_when_none_on_file(self):
        resp = self.client.post("/onboarding/resume", data={}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)  # re-renders with an error, no redirect
        self.assertIsNone(_fresh_db_row()["resume_filename"])

    def test_failed_resume_extraction_does_not_clobber_existing_fields(self):
        """Corner case: upload a corrupt .pdf after roles/home_address were already customized.
        The failed extraction must not silently overwrite them - see app.py's onboarding()."""
        store.profile["resume_filename"] = "old_resume.pdf"
        store.profile["roles"] = ["Custom Role A"]
        store.profile["home_address"] = "Custom City"
        store.save_profile()

        resp = self.client.post(
            "/onboarding/resume",
            data={"resume": (io.BytesIO(b"not a real pdf"), "bad.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)  # stays on the page so the warning is seen
        self.assertIn(b"Could", resp.data)

        row = _fresh_db_row()
        self.assertEqual(row["resume_filename"], "old_resume.pdf")
        self.assertEqual(row["roles"], ["Custom Role A"])
        self.assertEqual(row["home_address"], "Custom City")
        # nothing got indexed for the failed file
        self.assertEqual([d["type"] for d in store.get_profile_documents()], [])

    def test_optional_documents_persist_independently_of_resume(self):
        self.client.post(
            "/onboarding/resume",
            data={
                "resume": (io.BytesIO(GOOD_PDF), "resume.pdf"),
                "cover_letter_sample": (io.BytesIO(b"Dear hiring manager, ..."), "cover.txt"),
                "story_bank": (io.BytesIO(b"Led a project that shipped early."), "stories.txt"),
            },
            content_type="multipart/form-data",
        )
        doc_types = {d["type"] for d in store.get_profile_documents()}
        self.assertEqual(doc_types, {"resume", "cover_letter_sample", "story_bank"})

    def test_roles_add_and_remove_persist(self):
        self.client.post("/onboarding/roles", data={"action": "add", "new_role": "Staff Engineer"})
        self.assertIn("Staff Engineer", _fresh_db_row()["roles"])

        self.client.post("/onboarding/roles", data={"action": "remove", "role": "Staff Engineer"})
        self.assertNotIn("Staff Engineer", _fresh_db_row()["roles"])

    def test_roles_add_duplicate_is_a_no_op(self):
        before = list(_fresh_db_row()["roles"])
        self.client.post("/onboarding/roles", data={"action": "add", "new_role": before[0]})
        self.assertEqual(_fresh_db_row()["roles"].count(before[0]), 1)

    def test_location_continue_persists_address_commute_and_remote(self):
        resp = self.client.post("/onboarding/location", data={
            "action": "continue", "home_address": "Austin, TX", "commute_miles": "15", "remote_ok": "on",
        })
        self.assertEqual(resp.status_code, 302)
        row = _fresh_db_row()
        self.assertEqual(row["home_address"], "Austin, TX")
        self.assertEqual(row["commute_miles"], 15)
        self.assertTrue(row["remote_ok"])

    def test_location_remote_ok_false_when_checkbox_unchecked(self):
        self.client.post("/onboarding/location", data={
            "action": "continue", "home_address": "Austin, TX", "commute_miles": "15",
        })
        self.assertFalse(_fresh_db_row()["remote_ok"])

    def test_location_country_add_and_remove_persist(self):
        self.client.post("/onboarding/location", data={"action": "add_country", "country": "Germany"})
        self.assertIn("Germany", _fresh_db_row()["eligible_countries"])

        self.client.post("/onboarding/location", data={"action": "remove_country", "country": "Germany"})
        self.assertNotIn("Germany", _fresh_db_row()["eligible_countries"])

    def test_location_unknown_country_is_ignored(self):
        before = list(_fresh_db_row()["eligible_countries"])
        self.client.post("/onboarding/location", data={"action": "add_country", "country": "Narnia"})
        self.assertEqual(_fresh_db_row()["eligible_countries"], before)

    def test_location_add_country_carries_home_address_along(self):
        """Corner case: onboarding_location.html's script re-attaches home_address/commute_miles/
        remote_ok to the add-country form's own submit (see its {% block scripts %}) - adding a
        country must not reset a home address that was typed but not yet saved via Continue."""
        resp = self.client.post("/onboarding/location", data={
            "action": "add_country", "country": "Germany",
            "home_address": "Vancouver, Canada", "commute_miles": "30", "remote_ok": "on",
        })
        self.assertEqual(resp.status_code, 302)
        row = _fresh_db_row()
        self.assertEqual(row["home_address"], "Vancouver, Canada")
        self.assertEqual(row["commute_miles"], 30)
        self.assertTrue(row["remote_ok"])
        self.assertIn("Germany", row["eligible_countries"])

    def test_location_add_country_without_js_fields_does_not_crash_or_reset(self):
        """If JS never ran (disabled, or a future edit drops the script), the add-country POST
        won't carry home_address at all - must not crash on int(None) and must leave the
        already-saved value untouched rather than blanking it."""
        self.client.post("/onboarding/location", data={
            "action": "continue", "home_address": "Austin, TX", "commute_miles": "15",
        })
        resp = self.client.post("/onboarding/location", data={"action": "add_country", "country": "Germany"})
        self.assertEqual(resp.status_code, 302)
        row = _fresh_db_row()
        self.assertEqual(row["home_address"], "Austin, TX")
        self.assertEqual(row["commute_miles"], 15)

    def test_preferences_toggle_and_text_persist(self):
        self.client.post("/onboarding/preferences", data={"action": "toggle_industry", "industry": "Fintech"})
        self.assertIn("Fintech", _fresh_db_row()["industries"])

        self.client.post("/onboarding/preferences", data={
            "action": "continue", "industries_text": "Small team, real ownership.",
        })
        self.assertEqual(_fresh_db_row()["industries_text"], "Small team, real ownership.")

    def test_salary_persists_and_completes_onboarding(self):
        resp = self.client.post("/onboarding/salary", data={"min_salary": "150000", "currency": "EUR"})
        self.assertEqual(resp.status_code, 302)
        row = _fresh_db_row()
        self.assertEqual(row["min_salary"], 150000)
        self.assertEqual(row["currency"], "EUR")
        self.assertTrue(row["onboarding_complete"])

    def test_salary_blank_min_salary_defaults_to_zero(self):
        self.client.post("/onboarding/salary", data={"min_salary": "", "currency": "USD"})
        self.assertEqual(_fresh_db_row()["min_salary"], 0)

    def test_full_wizard_round_trip_persists_every_field(self):
        """End-to-end: walk all five steps, then re-read the user row fresh from the DB - proof
        every step's data survived, not just the in-memory store.profile dict."""
        self.client.post("/onboarding/resume", data={"resume": (io.BytesIO(GOOD_PDF), "resume.pdf")},
                          content_type="multipart/form-data")
        self.client.post("/onboarding/roles", data={"action": "add", "new_role": "Staff Engineer"})
        self.client.post("/onboarding/roles", data={"action": "continue"})
        self.client.post("/onboarding/location", data={
            "action": "continue", "home_address": "Austin, TX", "commute_miles": "20", "remote_ok": "on",
        })
        self.client.post("/onboarding/preferences", data={
            "action": "continue", "industries_text": "Climate-focused, small team.",
        })
        self.client.post("/onboarding/salary", data={"min_salary": "120000", "currency": "USD"})

        row = _fresh_db_row()
        self.assertEqual(row["resume_filename"], "resume.pdf")
        self.assertIn("Staff Engineer", row["roles"])
        self.assertEqual(row["home_address"], "Austin, TX")
        self.assertEqual(row["commute_miles"], 20)
        self.assertTrue(row["remote_ok"])
        self.assertEqual(row["industries_text"], "Climate-focused, small team.")
        self.assertEqual(row["min_salary"], 120000)
        self.assertTrue(row["onboarding_complete"])


if __name__ == "__main__":
    unittest.main()
