"""src/store.py - excluding sourcing-component wrappers (owned by the in-progress components
work: get_component_config, save_component_config, start_run/finish_run/save_run_result,
get_component_runs/get_run/get_latest_run/get_run_results/get_run_result, add_run_result_to_dashboard).

store's globals (profile, jobs, ...) are session-wide (see tests/db_setup.py), so tests that
mutate them restore what they changed.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
from src import store

JOB_FIELDS = {
    "company": "Acme", "title": "Engineer", "source": "Direct", "location": "Remote",
    "remote": True, "posted": "2026-01-01", "salary_min": 100000, "salary_max": 150000,
    "currency": "USD", "description": "desc",
}


class NormalizeCountryTests(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(store.normalize_country("united states"), "United States")
        self.assertEqual(store.normalize_country("  Germany  "), "Germany")
        self.assertIsNone(store.normalize_country("Narnia"))


class ProfileTests(unittest.TestCase):
    def test_save_and_reset_round_trip(self):
        original_roles = list(store.profile["roles"])
        try:
            store.profile["roles"] = ["Custom Role"]
            store.save_profile()
            self.assertEqual(store.db.ensure_demo_user()["roles"], ["Custom Role"])

            store.reset_profile()
            self.assertEqual(store.profile["roles"], store.db.DEFAULT_ROLES)
        finally:
            store.profile["roles"] = original_roles
            store.save_profile()

    def test_save_priority_weights_persists(self):
        original = dict(store.priority_weights)
        try:
            store.priority_weights = {"role_match": 100, "location_fit": 0, "salary_fit": 0, "industry_fit": 0}
            store.save_priority_weights()
            self.assertEqual(store.db.ensure_demo_user()["priority_weights"], store.priority_weights)
        finally:
            store.priority_weights = original
            store.save_priority_weights()


class IndustryOptionsTests(unittest.TestCase):
    def test_new_options_present_and_no_preference_last(self):
        for name in ("Mobility", "Govtech", "SaaS"):
            self.assertIn(name, store.INDUSTRY_OPTIONS)
        self.assertEqual(store.INDUSTRY_OPTIONS[-1], "No preference")

    def test_options_are_unique(self):
        self.assertEqual(len(store.INDUSTRY_OPTIONS), len(set(store.INDUSTRY_OPTIONS)))

    def test_existing_selection_survives_option_list_change(self):
        # corner case: a saved pick not in INDUSTRY_OPTIONS (e.g. from a removed
        # option) must not be silently dropped by save/reset round trip
        original = list(store.profile["industries"])
        try:
            store.profile["industries"] = ["Legacy Industry", "SaaS"]
            store.save_profile()
            self.assertEqual(store.db.ensure_demo_user()["industries"], ["Legacy Industry", "SaaS"])
        finally:
            store.profile["industries"] = original
            store.save_profile()


class JobTests(unittest.TestCase):
    def test_get_job_found_and_missing(self):
        existing_id = store.jobs[0]["id"]
        self.assertEqual(store.get_job(existing_id)["id"], existing_id)
        self.assertIsNone(store.get_job(-1))

    def test_job_url_exists(self):
        url = store.jobs[0]["url"]
        self.assertTrue(store.job_url_exists(url))
        self.assertFalse(store.job_url_exists("https://example.com/definitely-not-there"))

    def test_add_custom_job_and_duplicate_url(self):
        fields = {**JOB_FIELDS, "url": "https://example.com/store-test-job"}
        job_id = store.add_custom_job(fields)
        self.assertEqual(store.get_job(job_id)["company"], "Acme")
        self.assertEqual(store.get_job(job_id)["status"], "new")  # progress row auto-seeded

        with self.assertRaises(RuntimeError):
            store.add_custom_job(fields)

    def test_update_job_progress_persists_and_updates_in_memory(self):
        fields = {**JOB_FIELDS, "url": "https://example.com/store-progress-job"}
        job_id = store.add_custom_job(fields)
        store.update_job_progress(job_id, status="applied", comments="sent it")
        self.assertEqual(store.get_job(job_id)["status"], "applied")
        self.assertEqual(store.get_job(job_id)["comments"], "sent it")

        reloaded = store.reload_jobs()
        self.assertEqual(next(j for j in reloaded if j["id"] == job_id)["status"], "applied")

    def test_update_job_progress_missing_job_is_noop(self):
        store.update_job_progress(-1, status="applied")  # must not raise


class FollowedCompaniesTests(unittest.TestCase):
    def test_dedupes_and_preserves_order(self):
        original = store.get_followed_companies()
        try:
            # dedup is exact-string (post-strip), not case-insensitive.
            store.save_followed_companies(["Acme", "Acme", " Globex ", ""])
            self.assertEqual(store.get_followed_companies(), ["Acme", "Globex"])

            store.add_followed_companies(["Initech", "Acme"])
            self.assertEqual(store.get_followed_companies(), ["Acme", "Globex", "Initech"])
        finally:
            store.save_followed_companies(original)


class ChatSessionTests(unittest.TestCase):
    def test_lifecycle_through_store(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "cover_letter", "model-a")
        self.assertEqual(store.get_chat_session(sid)["model"], "model-a")
        self.assertTrue(any(s["id"] == sid for s in store.get_chat_sessions(job_id, "cover_letter")))

        store.remove_chat_session(sid)
        self.assertTrue(all(s["id"] != sid for s in store.get_chat_sessions(job_id, "cover_letter")))

    def test_switch_session_model_resurfaces_hidden_empty_pane(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "cover_letter", "model-x")
        store.add_chat_message(sid, job_id, "cover_letter", "user", "hi", model="model-x")
        store.remove_chat_session(sid)  # hidden, but has history

        fresh_sid = store.create_chat_session(job_id, "cover_letter", None)
        resolved = store.switch_session_model(fresh_sid, job_id, "cover_letter", "model-x")

        self.assertEqual(resolved, sid)  # resurfaced the hidden pane instead of the fresh blank one
        self.assertIsNone(store.get_chat_session(fresh_sid))  # blank pane was deleted
        self.assertEqual(len(store.get_chat(sid)), 1)

    def test_switch_session_model_no_hidden_match_updates_in_place(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "resume", None)
        resolved = store.switch_session_model(sid, job_id, "resume", "brand-new-model")
        self.assertEqual(resolved, sid)
        self.assertEqual(store.get_chat_session(sid)["model"], "brand-new-model")


class ChatMessageTests(unittest.TestCase):
    def test_round_trip_and_display(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "qa", "m")
        store.add_chat_message(sid, job_id, "qa", "user", "Why us?", model="m")
        store.add_chat_message(sid, job_id, "qa", "assistant", "Because.", model="m")

        chat = store.get_chat(sid)
        self.assertEqual(chat, [{"role": "user", "content": "Why us?"}, {"role": "assistant", "content": "Because."}])

        display = store.get_chat_for_display(sid)
        self.assertTrue(all("citations" in m for m in display))


class RatingTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(store, "RESULTS_FILE", Path(self._tmpdir.name) / "results.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)

    def test_rate_chat_message_appends_result_and_is_idempotent(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "qa", "m")
        store.add_chat_message(sid, job_id, "qa", "user", "Why us?", model="m")
        mid = store.add_chat_message(sid, job_id, "qa", "assistant", "Because.", model="m")

        store.rate_chat_message(mid, "up")
        results = store.load_results()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["rating"], "up")
        self.assertEqual(results[0]["question"], "Why us?")

        store.rate_chat_message(mid, "down")  # already rated: no-op, no duplicate entry
        self.assertEqual(len(store.load_results()), 1)

    def test_rate_chat_message_missing_or_non_assistant_raises(self):
        with self.assertRaises(ValueError):
            store.rate_chat_message(-1, "up")

        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "qa", "m")
        user_mid = store.add_chat_message(sid, job_id, "qa", "user", "hi", model="m")
        with self.assertRaises(ValueError):
            store.rate_chat_message(user_mid, "up")

    def test_load_results_missing_file_returns_empty(self):
        with mock.patch.object(store, "RESULTS_FILE", Path(self._tmpdir.name) / "missing.json"):
            self.assertEqual(store.load_results(), [])

    def test_load_results_corrupt_json_returns_empty(self):
        path = Path(self._tmpdir.name) / "corrupt.json"
        path.write_text("not json")
        with mock.patch.object(store, "RESULTS_FILE", path):
            self.assertEqual(store.load_results(), [])


class StatsTests(unittest.TestCase):
    def test_results_stats_percent_positive_and_empty_bucket(self):
        results = [
            {"rating": "up", "model": "a"}, {"rating": "up", "model": "a"}, {"rating": "down", "model": "a"},
            {"rating": "up", "model": "b"},
        ]
        stats = store.results_stats(results)
        self.assertEqual(stats["overall"]["total"], 4)
        self.assertEqual(stats["overall"]["percent_positive"], 75)
        self.assertEqual(stats["by_model"]["b"]["percent_positive"], 100)
        self.assertIsNone(store.results_stats([])["overall"]["percent_positive"])

    def test_usage_stats_aggregates_by_model(self):
        usage = [
            {"model": "a", "total_tokens": 100, "estimated_cost_usd": 0.01},
            {"model": "a", "total_tokens": 50, "estimated_cost_usd": 0.005},
            {"model": "b", "total_tokens": 10, "estimated_cost_usd": 0.0},
        ]
        stats = store.usage_stats(usage)
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["total_tokens"], 160)
        self.assertEqual(stats["by_model"]["a"]["calls"], 2)
        self.assertEqual(stats["by_model"]["a"]["tokens"], 150)

    def test_usage_stats_empty(self):
        stats = store.usage_stats([])
        self.assertEqual(stats, {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "by_model": {}})


class ArtifactAndQaTests(unittest.TestCase):
    def test_artifact_text_round_trip(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "cover_letter", "m")
        self.assertEqual(store.get_artifact_text(sid), "")
        store.save_artifact(sid, job_id, "cover_letter", "Dear hiring manager...")
        self.assertEqual(store.get_artifact_text(sid), "Dear hiring manager...")

    def test_qa_list_round_trip(self):
        job_id = store.jobs[0]["id"]
        sid = store.create_chat_session(job_id, "qa", "m")
        self.assertEqual(store.get_qa_list(sid), [])
        qa_id = store.add_qa(sid, job_id, "Why us?", "Because.")
        self.assertEqual(len(store.get_qa_list(sid)), 1)
        store.update_qa(qa_id, "Because reasons.")
        self.assertEqual(store.get_qa_list(sid)[0]["content"], "Because reasons.")


class PreferenceTests(unittest.TestCase):
    def test_round_trip(self):
        original = store.get_preferences()["general"]
        try:
            store.save_preference("general", "be concise")
            self.assertEqual(store.get_preferences()["general"], "be concise")
            self.assertEqual(store.get_preferences_full()["general"]["previous_text"], original)
        finally:
            store.save_preference("general", original)


class AssistantMessageTests(unittest.TestCase):
    def test_add_and_get_round_trip(self):
        job_id = store.jobs[0]["id"]
        mid = store.add_assistant_message("user", "hi", job_id=job_id, model="m")
        message = store.get_assistant_message(mid)
        self.assertEqual(message["content"], "hi")
        self.assertEqual(message["job_id"], job_id)
        self.assertIn(mid, [m["id"] for m in store.get_assistant_messages()])

    def test_get_active_job_id_ignores_null_job_rows(self):
        job_id = store.jobs[0]["id"]
        store.add_assistant_message("assistant", "about a job", job_id=job_id, model="m")
        store.add_assistant_message("user", "job-agnostic follow-up", model="m")
        self.assertEqual(store.get_active_job_id(), job_id)

    def test_assistant_model_round_trip(self):
        original = store.get_assistant_model("default-model")
        try:
            store.set_assistant_model("picked-model")
            self.assertEqual(store.get_assistant_model("default-model"), "picked-model")
        finally:
            store.set_assistant_model(original)


if __name__ == "__main__":
    unittest.main()
