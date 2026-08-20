"""Floating assistant widget markup (templates/chat_widget.html, included globally by
base.html) - renders on every logged-in page, not on the (nav-less) login page.
"""
import unittest

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app


class ChatWidgetRenderTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()

    def test_widget_renders_on_a_logged_in_page(self):
        self.client.post("/login")
        html = self.client.get("/workflows").get_data(as_text=True)
        self.assertIn("data-assistant-panel", html)
        self.assertIn("data-assistant-toggle", html)
        self.assertIn('src="/static/chat_widget.js"', html)

    def test_widget_absent_when_logged_out(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertNotIn("data-assistant-panel", html)

    def test_model_dropdown_lists_every_model_option(self):
        from src import agents
        self.client.post("/login")
        html = self.client.get("/workflows").get_data(as_text=True)
        for m in agents.MODEL_OPTIONS:
            self.assertIn(f'value="{m}"', html)


if __name__ == "__main__":
    unittest.main()
