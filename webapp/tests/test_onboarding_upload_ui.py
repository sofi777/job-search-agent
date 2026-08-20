"""Upload dropzone UI (onboarding resume step) - the custom file-picker markup that replaces
the native file input control: existing-file state (filename shown, checkmark visible,
"Replace file"), empty state ("Choose file", checkmark hidden), and the shared upload.js
contract these templates rely on.
"""
import re
import subprocess
import unittest
from pathlib import Path

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import store

UPLOAD_JS = Path(__file__).resolve().parent.parent / "static" / "upload.js"

CHECK_SPAN_RE = re.compile(r'data-role="check"[^>]*>')


class OnboardingResumeDropzoneTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self._original_resume = store.profile.get("resume_filename")

    def tearDown(self):
        store.profile["resume_filename"] = self._original_resume
        store.save_profile()

    def _get(self):
        return self.client.get("/onboarding/resume").get_data(as_text=True)

    def test_empty_state_shows_choose_file_and_hidden_check(self):
        store.profile["resume_filename"] = None
        store.save_profile()
        html = self._get()
        self.assertIn("data-upload-form", html)
        self.assertIn('data-field="resume"', html)
        self.assertIn("Choose file", html)
        self.assertNotIn("Replace file", html)
        self.assertIn("hidden", CHECK_SPAN_RE.search(html).group())
        self.assertIn('class="visually-hidden-file"', html)

    def test_existing_file_shows_name_and_visible_check_no_replace_error(self):
        store.profile["resume_filename"] = "resume.pdf"
        store.save_profile()
        html = self._get()
        self.assertIn("resume.pdf", html)
        self.assertIn("Replace file", html)
        self.assertNotIn("hidden", CHECK_SPAN_RE.search(html).group())
        # "No file chosen" is the browser's native, unstyleable label for an empty file input -
        # it must never appear because the real input is visually hidden behind a custom button.
        self.assertNotIn("No file chosen", html)

    def test_script_included(self):
        self.assertIn("/static/upload.js", self._get())


class UploadJsContractTests(unittest.TestCase):
    """upload.js is plain browser JS with no test runner in this repo - check it at least
    parses cleanly and still exposes the hooks the templates rely on."""

    def test_valid_syntax(self):
        result = subprocess.run(["node", "--check", str(UPLOAD_JS)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exposes_expected_hooks(self):
        source = UPLOAD_JS.read_text()
        for hook in (
            "data-upload-form", "[data-field]", "data-role=name", "data-role=check",
            "data-role=progress-fill", "XMLHttpRequest", "submitBtn.disabled = true",
        ):
            self.assertIn(hook, source)


if __name__ == "__main__":
    unittest.main()
