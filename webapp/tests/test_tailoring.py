"""src/tailoring.py run_turn() - one full tailoring-chat turn (classify -> retrieve ->
generate -> persist -> learn). Extracted from what was app.py's _run_pane_turn; agents.*
calls are mocked here (never hits the network), everything else is real store/db so
persistence and the preference-learning gate are exercised for real.
"""
import unittest
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
from src import store, tailoring


class RunTurnTests(unittest.TestCase):
    def setUp(self):
        self.job = store.jobs[0]
        self.session_id = store.create_chat_session(self.job["id"], "cover_letter", "m")
        self.preferences = store.get_preferences()

    def tearDown(self):
        # Belt and suspenders: whatever a test left unchecked shouldn't leak into another
        # test's "how many are pending" count - same precedent as test_learning.py.
        for m in store.get_unchecked_feedback_messages(self.job["id"]):
            store.mark_message_preference_checked(m["id"])

    @mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": False})
    @mock.patch("src.agents.run_tailor_turn")
    def test_success_saves_message_and_artifact_marks_checked(self, run_tailor_turn, classify_turn):
        run_tailor_turn.return_value = (
            {"reply": "Here's a draft.", "artifact": "Dear hiring manager..."}, "m", {"prompt_tokens": 1, "completion_tokens": 1},
        )
        chat_session = store.get_chat_session(self.session_id)
        unchecked_before = len(store.get_unchecked_feedback_messages(self.job["id"]))

        assistant_message_id, error = tailoring.run_turn(chat_session, self.job, "write me a cover letter", self.preferences)

        self.assertIsNone(error)
        self.assertIsNotNone(assistant_message_id)
        self.assertEqual(store.get_artifact_text(self.session_id), "Dear hiring manager...")
        self.assertEqual(len(store.get_chat(self.session_id)), 2)  # user + assistant
        # classify_turn always runs, regardless of whether it revealed a preference - the
        # user message is marked checked either way (see run_turn's docstring), so the
        # unchecked count doesn't grow from this turn.
        self.assertEqual(len(store.get_unchecked_feedback_messages(self.job["id"])), unchecked_before)

    @mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": False})
    @mock.patch("src.agents.run_tailor_turn", side_effect=RuntimeError("model unavailable"))
    def test_runtime_error_returns_none_and_error_leaves_message_unchecked(self, run_tailor_turn, classify_turn):
        chat_session = store.get_chat_session(self.session_id)
        unchecked_before = len(store.get_unchecked_feedback_messages(self.job["id"]))

        assistant_message_id, error = tailoring.run_turn(chat_session, self.job, "write me a cover letter", self.preferences)

        self.assertIsNone(assistant_message_id)
        self.assertIn("model unavailable", error)
        # The user's message was still saved (never lost) but never reached the "fully
        # considered" point, so it's left unmarked for a later bulk run to pick up.
        self.assertEqual(len(store.get_chat(self.session_id)), 1)
        self.assertEqual(len(store.get_unchecked_feedback_messages(self.job["id"])), unchecked_before + 1)

    @mock.patch("src.agents.revise_preferences", return_value=None)
    @mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": True})
    @mock.patch("src.agents.run_tailor_turn")
    def test_preference_learning_only_fires_with_prior_content(self, run_tailor_turn, classify_turn, revise_preferences):
        run_tailor_turn.return_value = (
            {"reply": "ok", "artifact": "revised text"}, "m", {"prompt_tokens": 1, "completion_tokens": 1},
        )
        chat_session = store.get_chat_session(self.session_id)

        # No prior artifact yet - reveals_preference is moot on a from-scratch generation
        # (see run_turn/_run_pane_turn's original gate), so revise_preferences must not run.
        tailoring.run_turn(chat_session, self.job, "first draft please", self.preferences)
        revise_preferences.assert_not_called()

        # Now there's prior content - the same "reveals_preference" classification should
        # trigger the learning call this time.
        tailoring.run_turn(chat_session, self.job, "make it shorter", self.preferences)
        revise_preferences.assert_called_once()

    @mock.patch("src.agents.revise_preferences", side_effect=RuntimeError("classifier flaked"))
    @mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": True})
    @mock.patch("src.agents.run_tailor_turn")
    def test_preference_learning_failure_does_not_fail_the_turn(self, run_tailor_turn, classify_turn, revise_preferences):
        run_tailor_turn.return_value = (
            {"reply": "ok", "artifact": "revised text"}, "m", {"prompt_tokens": 1, "completion_tokens": 1},
        )
        chat_session = store.get_chat_session(self.session_id)
        store.save_artifact(self.session_id, self.job["id"], "cover_letter", "existing draft")

        assistant_message_id, error = tailoring.run_turn(chat_session, self.job, "make it shorter", self.preferences)

        self.assertIsNone(error)
        self.assertIsNotNone(assistant_message_id)


if __name__ == "__main__":
    unittest.main()
