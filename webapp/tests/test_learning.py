"""src/learning.py - run_learning() replays unchecked tailoring-chat feedback through the
same reveals-a-preference judgment the live per-turn hook makes (src/tailoring.run_turn).
classify_turn/revise_preferences (real LLM calls) are mocked; everything else is real
store/db so the gating (preceding/following artifact_text), incremental marking, and
scope filtering are exercised for real.
"""
import unittest
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
from src import db, learning, store

JOB_A = store.jobs[0]["id"]
JOB_B = store.jobs[1]["id"]


def _session(job_id, tab="cover_letter"):
    return store.create_chat_session(job_id, tab, "m")


class PreferenceLearningTests(unittest.TestCase):
    def tearDown(self):
        with db.db_transaction() as conn:
            conn.execute("UPDATE preferences SET text = '', previous_text = '', updated_at = NULL")
        # Belt and suspenders: whatever a test left unchecked (e.g. a run that stopped
        # partway on purpose) shouldn't leak into the next test's "how many are pending" count.
        for job_id in (JOB_A, JOB_B):
            for m in store.get_unchecked_feedback_messages(job_id):
                store.mark_message_preference_checked(m["id"])

    def test_message_without_preceding_draft_is_skipped_not_classified(self):
        sid = _session(JOB_A)
        store.add_chat_message(sid, JOB_A, "cover_letter", "user", "make it punchier", model="m")

        with mock.patch("src.learning.agents.classify_turn") as classify, \
             mock.patch("src.learning.agents.revise_preferences") as revise:
            run_id = learning.run_learning(scope_job_id=JOB_A, mode="live")

        classify.assert_not_called()
        revise.assert_not_called()
        run = store.get_preference_learning_run(run_id)
        self.assertEqual((run["processed_count"], run["updated_count"], run["status"]), (1, 0, "ok"))

    def test_live_mode_applies_revision_and_is_incremental(self):
        sid = _session(JOB_A)
        store.add_chat_message(sid, JOB_A, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid, JOB_A, "cover_letter", "user", "be punchier", model="m")
        store.add_chat_message(sid, JOB_A, "cover_letter", "assistant", "reply2", model="m", artifact_text="v2")

        with mock.patch("src.learning.agents.classify_turn", return_value={"reveals_preference": True}), \
             mock.patch("src.learning.agents.revise_preferences",
                        return_value={"category": "cover_letter", "text": "punchier tone"}):
            run_id = learning.run_learning(scope_job_id=JOB_A, mode="live")

        run = store.get_preference_learning_run(run_id)
        self.assertEqual((run["processed_count"], run["updated_count"], run["status"]), (1, 1, "ok"))
        self.assertEqual(store.get_preferences()["cover_letter"], "punchier tone")
        results = store.get_preference_learning_run_results(run_id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category"], "cover_letter")

        # Second run: same feedback already checked, nothing left to process.
        with mock.patch("src.learning.agents.classify_turn") as classify:
            second_run_id = learning.run_learning(scope_job_id=JOB_A, mode="live")
        classify.assert_not_called()
        self.assertEqual(store.get_preference_learning_run(second_run_id)["processed_count"], 0)

    def test_test_mode_previews_without_persisting_or_marking_checked(self):
        sid = _session(JOB_A)
        store.add_chat_message(sid, JOB_A, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid, JOB_A, "cover_letter", "user", "be punchier", model="m")
        store.add_chat_message(sid, JOB_A, "cover_letter", "assistant", "reply2", model="m", artifact_text="v2")

        with mock.patch("src.learning.agents.classify_turn", return_value={"reveals_preference": True}), \
             mock.patch("src.learning.agents.revise_preferences",
                        return_value={"category": "cover_letter", "text": "punchier tone"}):
            run_id = learning.run_learning(scope_job_id=JOB_A, mode="test")

        self.assertEqual(store.get_preferences()["cover_letter"], "")
        self.assertEqual(len(store.get_preference_learning_run_results(run_id)), 1)
        self.assertEqual(len(store.get_unchecked_feedback_messages(JOB_A)), 1)  # still unchecked

    def test_scope_filters_to_one_job(self):
        sid_a = _session(JOB_A)
        store.add_chat_message(sid_a, JOB_A, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid_a, JOB_A, "cover_letter", "user", "feedback a", model="m")

        sid_b = _session(JOB_B)
        store.add_chat_message(sid_b, JOB_B, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid_b, JOB_B, "cover_letter", "user", "feedback b", model="m")

        with mock.patch("src.learning.agents.classify_turn", return_value={"reveals_preference": False}):
            run_id = learning.run_learning(scope_job_id=JOB_A, mode="live")

        self.assertEqual(store.get_preference_learning_run(run_id)["processed_count"], 1)
        self.assertEqual(len(store.get_unchecked_feedback_messages(JOB_A)), 0)
        self.assertEqual(len(store.get_unchecked_feedback_messages(JOB_B)), 1)

    def test_no_resulting_artifact_skips_revision_but_still_marks_checked(self):
        sid = _session(JOB_A)
        store.add_chat_message(sid, JOB_A, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid, JOB_A, "cover_letter", "user", "feedback", model="m")  # turn never completed

        with mock.patch("src.learning.agents.classify_turn", return_value={"reveals_preference": True}), \
             mock.patch("src.learning.agents.revise_preferences") as revise:
            run_id = learning.run_learning(scope_job_id=JOB_A, mode="live")

        revise.assert_not_called()
        run = store.get_preference_learning_run(run_id)
        self.assertEqual((run["processed_count"], run["updated_count"]), (1, 0))
        self.assertEqual(len(store.get_unchecked_feedback_messages(JOB_A)), 0)  # marked checked anyway

    def test_classification_error_stops_run_with_partial_progress(self):
        sid_a = _session(JOB_A)
        store.add_chat_message(sid_a, JOB_A, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid_a, JOB_A, "cover_letter", "user", "feedback a", model="m")

        sid_b = _session(JOB_B)
        store.add_chat_message(sid_b, JOB_B, "cover_letter", "assistant", "reply", model="m", artifact_text="v1")
        store.add_chat_message(sid_b, JOB_B, "cover_letter", "user", "feedback b", model="m")

        with mock.patch("src.learning.agents.classify_turn", side_effect=RuntimeError("boom")):
            run_id = learning.run_learning(mode="live")

        run = store.get_preference_learning_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("boom", run["error_message"])
        self.assertEqual(run["processed_count"], 0)


if __name__ == "__main__":
    unittest.main()
