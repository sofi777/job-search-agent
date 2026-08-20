"""src/scanner.py run_scan() - single entry point for fit-scoring store.jobs. Both modes
call ai.score_job for real (mocked out here - never hits the network); "test" forces the
model to agents.DEFAULT_MODEL (free) regardless of what's passed and never touches the
dashboard, "live" uses whatever model is given and does."""
import unittest
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
from src import agents, db, scanner, store


def _delete_profile_document(doc_type):
    # Bypasses store.delete_profile_document/rag on purpose - scoring only ever reads
    # documents.content (store.get_profile_documents), never chunks/embeddings, so tests
    # skip the (slow, model-loading) rag path entirely to stay fast.
    existing = next((d for d in db.fetch_profile_documents(store.user_id) if d["type"] == doc_type), None)
    if existing:
        db.delete_document(existing["id"])


class RunScanTests(unittest.TestCase):
    def setUp(self):
        # Every sample job in test data needs a resume on file, or run_scan fails clearly
        # before scoring anything (see test_no_resume_fails_clearly below for that path).
        # Inserted directly via db.py, not store.save_profile_document, to skip rag chunking.
        db.upsert_profile_document(store.user_id, "resume", "resume.txt", "Senior PM, 8 years, fintech", "2026-01-01")

    def tearDown(self):
        _delete_profile_document("resume")
        _delete_profile_document("story_bank")

    @mock.patch("src.ai.score_job")
    def test_test_mode_calls_the_real_scorer_forced_to_the_free_default_model(self, score_job):
        score_job.return_value = {"score": 88, "summary": "Solid overlap"}
        self.assertGreater(len(store.jobs), 0)  # sample catalog seeded at store import

        run_id = scanner.run_scan(mode="test", model="some/paid-model")  # requested model must be ignored

        self.assertEqual(score_job.call_count, len(store.jobs))
        self.assertTrue(all(c.args[-1] == agents.DEFAULT_MODEL for c in score_job.call_args_list))

        run = store.get_scoring_run(run_id)
        self.assertEqual(run["mode"], "test")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["model"], agents.DEFAULT_MODEL)
        self.assertEqual(run["scored_count"], len(store.jobs))

        results = store.get_scoring_run_results(run_id)
        self.assertEqual(len(results), len(store.jobs))
        self.assertTrue(all(r["score"] == 88 for r in results))

    @mock.patch("src.ai.score_job")
    def test_test_mode_never_touches_the_dashboard(self, score_job):
        score_job.return_value = {"score": 88, "summary": "Solid overlap"}
        before = {j["id"]: (j.get("match"), j.get("match_summary")) for j in store.jobs}
        last_scan_before = store.last_scan

        scanner.run_scan(mode="test")

        after = {j["id"]: (j.get("match"), j.get("match_summary")) for j in store.jobs}
        self.assertEqual(before, after)
        self.assertEqual(store.last_scan, last_scan_before)

    @mock.patch("src.ai.score_job")
    def test_live_mode_scores_every_job_and_updates_dashboard(self, score_job):
        score_job.return_value = {"score": 91, "summary": "Great fit"}

        run_id = scanner.run_scan(mode="live", model="some/model")

        run = store.get_scoring_run(run_id)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["model"], "some/model")
        self.assertEqual(run["scored_count"], len(store.jobs))
        self.assertTrue(all(j["match"] == 91 for j in store.jobs))
        self.assertTrue(all(j["match_summary"] == "Great fit" for j in store.jobs))
        self.assertIsNotNone(store.last_scan)

    @mock.patch("src.ai.score_job")
    def test_live_mode_defaults_to_agents_default_model_when_none_given(self, score_job):
        score_job.return_value = {"score": 50, "summary": "ok"}
        scanner.run_scan(mode="live", model=None)
        self.assertEqual(store.get_latest_scoring_run()["model"], agents.DEFAULT_MODEL)

    @mock.patch("src.ai.score_job")
    def test_live_mode_passes_resume_and_profile_fields_through(self, score_job):
        score_job.return_value = {"score": 50, "summary": "ok"}
        store.profile["industries"] = ["Climate tech"]
        store.profile["industries_text"] = "mission-driven only"

        scanner.run_scan(mode="live", model="m")

        _job, resume_text, story_bank_text, industries, industries_text, model = score_job.call_args.args
        self.assertIn("Senior PM", resume_text)
        self.assertEqual(story_bank_text, "")
        self.assertEqual(industries, ["Climate tech"])
        self.assertEqual(industries_text, "mission-driven only")
        self.assertEqual(model, "m")

    @mock.patch("src.ai.score_job")
    def test_a_failure_mid_run_is_recorded_not_raised(self, score_job):
        score_job.side_effect = [{"score": 80, "summary": "ok"}, RuntimeError("model unusable")]

        run_id = scanner.run_scan(mode="live")  # must not raise

        run = store.get_scoring_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("model unusable", run["error_message"])
        self.assertEqual(run["scored_count"], 1)

    def test_no_resume_fails_clearly_without_calling_the_llm(self):
        _delete_profile_document("resume")
        with mock.patch("src.ai.agents.send_message") as send_message:
            run_id = scanner.run_scan(mode="live")
            send_message.assert_not_called()

        run = store.get_scoring_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("No resume", run["error_message"])
        self.assertEqual(run["scored_count"], 0)

    def test_no_resume_fails_clearly_in_test_mode_too(self):
        _delete_profile_document("resume")
        with mock.patch("src.ai.agents.send_message") as send_message:
            run_id = scanner.run_scan(mode="test")
            send_message.assert_not_called()

        run = store.get_scoring_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("No resume", run["error_message"])

    @mock.patch("src.ai.score_job")
    def test_pending_only_skips_terminal_status_jobs(self, score_job):
        score_job.return_value = {"score": 91, "summary": "Great fit"}
        pending_id = next(j["id"] for j in store.jobs if j["status"] not in store.TERMINAL_STATUSES)
        newly_rejected_id = next(j["id"] for j in store.jobs if j["id"] != pending_id)
        store.update_job_progress(newly_rejected_id, status="rejected")
        pending_count = sum(1 for j in store.jobs if j["status"] not in store.TERMINAL_STATUSES)

        run_id = scanner.run_scan(mode="live", model="m", pending_only=True)

        self.assertEqual(score_job.call_count, pending_count)
        run = store.get_scoring_run(run_id)
        self.assertEqual(run["scored_count"], pending_count)
        scored_job_ids = {r["job_id"] for r in store.get_scoring_run_results(run_id)}
        self.assertNotIn(newly_rejected_id, scored_job_ids)
        self.assertIn(pending_id, scored_job_ids)

    @mock.patch("src.ai.score_job")
    def test_pending_only_keeps_the_excluded_jobs_last_score(self, score_job):
        score_job.return_value = {"score": 70, "summary": "first pass"}
        scanner.run_scan(mode="live", model="m")  # scores every job once
        applied_id = store.jobs[0]["id"]
        store.update_job_progress(applied_id, status="applied")

        score_job.return_value = {"score": 95, "summary": "second pass"}
        scanner.run_scan(mode="live", model="m", pending_only=True)

        self.assertEqual(store.get_job(applied_id)["match"], 70)  # untouched, not wiped
        other_id = next(j["id"] for j in store.jobs if j["id"] != applied_id)
        self.assertEqual(store.get_job(other_id)["match"], 95)

    def test_pending_only_still_fails_clearly_without_a_resume(self):
        _delete_profile_document("resume")
        with mock.patch("src.ai.agents.send_message") as send_message:
            run_id = scanner.run_scan(mode="live", pending_only=True)
            send_message.assert_not_called()
        self.assertEqual(store.get_scoring_run(run_id)["status"], "error")


if __name__ == "__main__":
    unittest.main()
