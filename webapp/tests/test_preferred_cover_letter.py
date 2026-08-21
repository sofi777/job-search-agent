"""Preferred ("ready to send") cover letter - app.py's toggle route, tailor.html's badge,
and the assistant's "show_preferred" chat action. Store-level round trip is covered in
tests/test_store.py's PreferredCoverLetterTests.
"""
import unittest
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import assistant, store


class ToggleRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self.job_id = store.jobs[0]["id"]
        self.session_id = store.create_chat_session(self.job_id, "cover_letter", "model-a")
        store.save_artifact(self.session_id, self.job_id, "cover_letter", "Dear hiring manager...")

    def tearDown(self):
        for s in store.get_chat_sessions(self.job_id, "cover_letter"):
            store.remove_chat_session(s["id"])
        store.unmark_preferred_cover_letter(self.job_id)

    def _toggle(self, session_id):
        return self.client.post(f"/jobs/{self.job_id}/tailor/cover_letter/session/{session_id}/prefer")

    def test_marks_then_unmarks(self):
        resp = self._toggle(self.session_id)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_preferred_cover_letter(self.job_id)["session_id"], self.session_id)

        self._toggle(self.session_id)  # second click unmarks
        self.assertIsNone(store.get_preferred_cover_letter(self.job_id))

    def test_marking_another_pane_replaces_it(self):
        other_id = store.create_chat_session(self.job_id, "cover_letter", "model-b")
        store.save_artifact(other_id, self.job_id, "cover_letter", "Other draft...")

        self._toggle(self.session_id)
        self._toggle(other_id)
        self.assertEqual(store.get_preferred_cover_letter(self.job_id)["session_id"], other_id)

    def test_session_belonging_to_another_job_is_a_no_op(self):
        other_job_id = store.jobs[1]["id"]
        resp = self.client.post(f"/jobs/{other_job_id}/tailor/cover_letter/session/{self.session_id}/prefer")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(store.get_preferred_cover_letter(other_job_id))

    def test_requires_login(self):
        anon = flask_app.app.test_client()
        resp = anon.post(f"/jobs/{self.job_id}/tailor/cover_letter/session/{self.session_id}/prefer")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(store.get_preferred_cover_letter(self.job_id))

    def test_tailor_page_shows_ready_to_send_badge(self):
        self._toggle(self.session_id)
        resp = self.client.get(f"/jobs/{self.job_id}/tailor?tab=cover_letter")
        self.assertIn(b"Ready to send", resp.data)
        self.assertIn(b"Unmark ready to send", resp.data)

    def test_job_detail_shows_preview_card(self):
        self._toggle(self.session_id)
        resp = self.client.get(f"/jobs/{self.job_id}")
        self.assertIn(b"Cover letter ready to send", resp.data)
        self.assertIn(b"Dear hiring manager...", resp.data)

    def test_job_detail_hides_card_when_nothing_marked(self):
        resp = self.client.get(f"/jobs/{self.job_id}")
        self.assertNotIn(b"Cover letter ready to send", resp.data)


class GetOrCreateSessionPrefersMarkedTests(unittest.TestCase):
    def setUp(self):
        self.job_id = store.jobs[0]["id"]

    def tearDown(self):
        for s in store.get_chat_sessions(self.job_id, "cover_letter"):
            store.remove_chat_session(s["id"])
        store.unmark_preferred_cover_letter(self.job_id)

    def test_prefers_marked_session_over_first_pane(self):
        first = store.create_chat_session(self.job_id, "cover_letter", "model-a")
        preferred = store.create_chat_session(self.job_id, "cover_letter", "model-b")
        store.mark_preferred_cover_letter(self.job_id, preferred)

        resolved = assistant.get_or_create_cover_letter_session(self.job_id, "model-c")
        self.assertEqual(resolved["id"], preferred)
        self.assertEqual(resolved["model"], "model-c")  # still retargeted to the widget's model
        self.assertEqual(first, first)  # first pane exists but wasn't picked


class HandleTurnShowPreferredTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self.job = store.jobs[0]

    def tearDown(self):
        for s in store.get_chat_sessions(self.job["id"], "cover_letter"):
            store.remove_chat_session(s["id"])
        store.unmark_preferred_cover_letter(self.job["id"])

    def _route(self, job_query=None):
        return mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "show_preferred", "job_query": job_query}],
        )

    def test_shows_marked_letter_no_generation_call(self):
        sid = store.create_chat_session(self.job["id"], "cover_letter", "model-a")
        store.save_artifact(sid, self.job["id"], "cover_letter", "Dear hiring manager...")
        store.mark_preferred_cover_letter(self.job["id"], sid)

        with self._route(self.job["company"]), mock.patch("src.agents.run_tailor_turn") as run_tailor_turn:
            resp = self.client.post("/assistant/message", json={"message": "show me the preferred letter"})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["assistant_messages"][0]
        self.assertEqual(data["job_id"], self.job["id"])
        self.assertEqual(data["artifact_text"], "Dear hiring manager...")
        run_tailor_turn.assert_not_called()

    def test_no_preferred_letter_yet_says_so(self):
        with self._route(self.job["company"]):
            resp = self.client.post("/assistant/message", json={"message": "show me the preferred letter"})
        data = resp.get_json()["assistant_messages"][0]
        self.assertIn("No cover letter is marked ready to send", data["content"])
        self.assertIsNone(data["artifact_text"])

    def test_unresolvable_job_asks_for_clarification(self):
        with self._route("a job that does not exist anywhere"):
            resp = self.client.post("/assistant/message", json={"message": "show me the preferred letter"})
        data = resp.get_json()["assistant_messages"][0]
        self.assertIsNone(data["job_id"])

    def test_feedback_after_showing_continues_the_preferred_session(self):
        sid = store.create_chat_session(self.job["id"], "cover_letter", "model-a")
        store.save_artifact(sid, self.job["id"], "cover_letter", "Dear hiring manager...")
        store.mark_preferred_cover_letter(self.job["id"], sid)

        with self._route(self.job["company"]):
            self.client.post("/assistant/message", json={"message": "show me the preferred letter"})

        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "cover_letter", "job_query": None}]
        ), mock.patch(
            "src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": False}
        ), mock.patch(
            "src.agents.run_tailor_turn",
            return_value=({"reply": "Shortened.", "artifact": "shorter text"}, "m", {"prompt_tokens": 1, "completion_tokens": 1}),
        ):
            self.client.post("/assistant/message", json={"message": "make it shorter"})

        self.assertEqual(store.get_preferred_cover_letter(self.job["id"])["content"], "shorter text")
        self.assertEqual(len(store.get_chat_sessions(self.job["id"], "cover_letter")), 1)  # same session, no fork


if __name__ == "__main__":
    unittest.main()
