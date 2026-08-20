"""src/db.py - excluding component_* tables (owned by the in-progress components work)."""
import unittest

from src import db
from tests.db_setup import DbTestCase


def make_job(url="https://example.com/job-1", **overrides):
    fields = {
        "company": "Acme", "title": "Engineer", "source": "Direct", "location": "Remote",
        "remote": True, "posted": "2026-01-01", "salary_min": 100000, "salary_max": 150000,
        "currency": "USD", "url": url, "description": "desc",
    }
    fields.update(overrides)
    return db.insert_job(fields)


class UserTests(DbTestCase):
    def test_ensure_demo_user_creates_once(self):
        user = db.ensure_demo_user()
        self.assertEqual(user["email"], db.DEMO_USER_EMAIL)
        self.assertEqual(user["roles"], db.DEFAULT_ROLES)
        self.assertEqual(db.ensure_demo_user()["id"], user["id"])  # idempotent, not a duplicate insert

    def test_update_user_json_encodes_list_and_dict_fields(self):
        user = db.ensure_demo_user()
        db.update_user(user["id"], roles=["PM"], priority_weights={"role_match": 100})
        updated = db.ensure_demo_user()
        self.assertEqual(updated["roles"], ["PM"])
        self.assertEqual(updated["priority_weights"], {"role_match": 100})

    def test_update_user_no_fields_is_noop(self):
        user = db.ensure_demo_user()
        db.update_user(user["id"])  # must not raise

    def test_reset_user_restores_defaults_but_keeps_email(self):
        user = db.ensure_demo_user()
        db.update_user(user["id"], roles=["Custom"], onboarding_complete=1)
        db.reset_user(user["id"])
        reset = db.ensure_demo_user()
        self.assertEqual(reset["roles"], db.DEFAULT_ROLES)
        self.assertFalse(reset["onboarding_complete"])
        self.assertEqual(reset["email"], db.DEMO_USER_EMAIL)


class JobTests(DbTestCase):
    def test_insert_job_duplicate_url_raises(self):
        make_job(url="https://example.com/dup")
        with self.assertRaises(RuntimeError):
            make_job(url="https://example.com/dup")

    def test_fetch_jobs_returns_bool_remote_ordered_by_id(self):
        id1 = make_job(url="https://example.com/a")
        id2 = make_job(url="https://example.com/b", remote=False)
        jobs = db.fetch_jobs()
        self.assertEqual([j["id"] for j in jobs], [id1, id2])
        self.assertTrue(jobs[0]["remote"])
        self.assertFalse(jobs[1]["remote"])

    def test_upsert_sample_jobs_matches_by_url_and_keeps_stable_id(self):
        catalog = [{
            "id": 42, "company": "A", "title": "T", "source": "S", "location": "L", "remote": True,
            "posted": "2026-01-01", "salary_min": 1, "salary_max": 2, "currency": "USD",
            "url": "https://example.com/sample", "description": "d",
        }]
        db.upsert_sample_jobs(catalog)
        db.upsert_sample_jobs([{**catalog[0], "title": "Updated"}])
        jobs = db.fetch_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], 42)
        self.assertEqual(jobs[0]["title"], "Updated")
        self.assertEqual(jobs[0]["origin"], "sample")

    def test_has_sample_jobs(self):
        self.assertFalse(db.has_sample_jobs())
        db.upsert_sample_jobs([{
            "id": 1, "company": "A", "title": "T", "source": "S", "location": "L", "remote": False,
            "posted": "2026-01-01", "salary_min": 0, "salary_max": 0, "currency": "USD",
            "url": "https://example.com/s1", "description": "",
        }])
        self.assertTrue(db.has_sample_jobs())


class ProgressTests(DbTestCase):
    def test_progress_seeded_row_and_update(self):
        user = db.ensure_demo_user()
        job_id = make_job()
        db.ensure_progress_rows(user["id"], [job_id])
        self.assertEqual(db.fetch_progress(user["id"])[job_id]["status"], "new")

        db.ensure_progress_rows(user["id"], [job_id])  # second call: no duplicate/overwrite
        db.update_progress(user["id"], job_id, status="applied", comments="sent")
        progress = db.fetch_progress(user["id"])[job_id]
        self.assertEqual(progress, {"job_id": job_id, "status": "applied", "comments": "sent"})

    def test_progress_seed_uses_sample_seed_data(self):
        user = db.ensure_demo_user()
        db.ensure_progress_rows(user["id"], [3])
        self.assertEqual(db.fetch_progress(user["id"])[3]["status"], "viewed")


class ChatSessionTests(DbTestCase):
    def test_lifecycle(self):
        job_id = make_job()
        sid = db.create_chat_session(job_id, "cover_letter", "model-a", "2026-01-01T00:00:00")
        self.assertEqual(db.get_chat_session(sid)["model"], "model-a")
        self.assertEqual([s["id"] for s in db.fetch_chat_sessions(job_id, "cover_letter")], [sid])

        db.update_chat_session_model(sid, "model-b")
        self.assertEqual(db.get_chat_session(sid)["model"], "model-b")

        db.hide_chat_session(sid)
        self.assertEqual(db.fetch_chat_sessions(job_id, "cover_letter"), [])
        self.assertEqual(db.find_hidden_chat_session(job_id, "cover_letter", "model-b"), sid)

        db.unhide_chat_session(sid)
        self.assertEqual([s["id"] for s in db.fetch_chat_sessions(job_id, "cover_letter")], [sid])

    def test_delete_chat_session(self):
        job_id = make_job()
        sid = db.create_chat_session(job_id, "qa", None, "2026-01-01T00:00:00")
        db.delete_chat_session(sid)
        self.assertIsNone(db.get_chat_session(sid))


class ChatMessageTests(DbTestCase):
    def test_order_and_get_preceding(self):
        job_id = make_job()
        sid = db.create_chat_session(job_id, "qa", None, "2026-01-01T00:00:00")
        m1 = db.add_chat_message(sid, job_id, "qa", "user", "hi", None, "2026-01-01T00:00:01")
        m2 = db.add_chat_message(sid, job_id, "qa", "assistant", "hello", "m", "2026-01-01T00:00:02")

        messages = db.fetch_chat_messages(sid)
        self.assertEqual([m["id"] for m in messages], [m1, m2])

        preceding = db.get_preceding_chat_message(sid, m2, "user")
        self.assertEqual(preceding["id"], m1)
        self.assertIsNone(db.get_preceding_chat_message(sid, m1, "user"))  # nothing before the first

    def test_rating_set_once(self):
        job_id = make_job()
        sid = db.create_chat_session(job_id, "qa", None, "2026-01-01T00:00:00")
        mid = db.add_chat_message(sid, job_id, "qa", "assistant", "hi", "m", "2026-01-01T00:00:00")
        db.set_chat_message_rating(mid, "up")
        self.assertEqual(db.get_chat_message(mid)["rating"], "up")


class ArtifactTests(DbTestCase):
    def test_upsert_bumps_version(self):
        job_id = make_job()
        sid = db.create_chat_session(job_id, "cover_letter", "m", "2026-01-01T00:00:00")
        self.assertIsNone(db.get_artifact(sid))

        db.upsert_artifact(sid, job_id, "cover_letter", "draft 1", "2026-01-01T00:00:01")
        first = db.get_artifact(sid)
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["content"], "draft 1")

        db.upsert_artifact(sid, job_id, "cover_letter", "draft 2", "2026-01-01T00:00:02")
        second = db.get_artifact(sid)
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["content"], "draft 2")
        self.assertEqual(second["id"], first["id"])  # updated in place, not a new row

    def test_qa_artifacts(self):
        job_id = make_job()
        sid = db.create_chat_session(job_id, "qa", "m", "2026-01-01T00:00:00")
        aid = db.insert_qa_artifact(sid, job_id, "Why us?", "Because", "2026-01-01T00:00:01")
        self.assertEqual([a["id"] for a in db.list_qa_artifacts(sid)], [aid])

        db.update_qa_artifact(aid, "Because reasons", "2026-01-01T00:00:02")
        updated = db.list_qa_artifacts(sid)[0]
        self.assertEqual(updated["content"], "Because reasons")
        self.assertEqual(updated["version"], 2)


class PreferenceTests(DbTestCase):
    def test_default_rows_and_update_keeps_previous(self):
        prefs = db.fetch_preferences()
        self.assertEqual(set(prefs), set(db.PREFERENCE_CATEGORIES))
        self.assertTrue(all(p["text"] == "" for p in prefs.values()))

        db.update_preference("general", "be concise", "2026-01-01T00:00:00")
        updated = db.fetch_preferences()["general"]
        self.assertEqual(updated["text"], "be concise")
        self.assertEqual(updated["previous_text"], "")

        db.update_preference("general", "be terse", "2026-01-01T00:00:01")
        updated = db.fetch_preferences()["general"]
        self.assertEqual(updated["text"], "be terse")
        self.assertEqual(updated["previous_text"], "be concise")


class DocumentTests(DbTestCase):
    def test_profile_document_replaces_previous(self):
        user = db.ensure_demo_user()
        first_id = db.upsert_profile_document(user["id"], "resume", "r1.pdf", "text1", "2026-01-01T00:00:00")
        second_id = db.upsert_profile_document(user["id"], "resume", "r2.pdf", "text2", "2026-01-01T00:00:01")
        docs = db.fetch_profile_documents(user["id"])
        self.assertEqual([d["id"] for d in docs], [second_id])
        self.assertNotEqual(first_id, second_id)

    def test_documents_job_scope(self):
        user = db.ensure_demo_user()
        job_id = make_job()
        profile_doc = db.upsert_profile_document(user["id"], "resume", "r.pdf", "t", "2026-01-01T00:00:00")
        job_doc = db.insert_document(user["id"], job_id, "attachment", "a.txt", "t", "2026-01-01T00:00:01")

        visible = {d["id"] for d in db.fetch_documents(user["id"], job_id)}
        self.assertEqual(visible, {profile_doc, job_doc})

        other_job = make_job(url="https://example.com/other")
        visible_other = {d["id"] for d in db.fetch_documents(user["id"], other_job)}
        self.assertEqual(visible_other, {profile_doc})  # job-scoped doc not visible elsewhere

    def test_delete_document_and_chunks(self):
        user = db.ensure_demo_user()
        doc_id = db.insert_document(user["id"], None, "attachment", "a.txt", "t", "2026-01-01T00:00:00")
        chunk_id = db.insert_chunk(doc_id, None, 0, "chunk text", 5, "2026-01-01T00:00:01")
        self.assertIsNotNone(db.fetch_chunk(chunk_id))

        db.delete_document_chunks(doc_id)
        self.assertIsNone(db.fetch_chunk(chunk_id))

        db.delete_document(doc_id)
        self.assertEqual(db.fetch_documents(user["id"], None), [])

    def test_orphaned_chunk_ids(self):
        user = db.ensure_demo_user()
        doc_id = db.insert_document(user["id"], None, "attachment", "a.txt", "t", "2026-01-01T00:00:00")
        chunk_id = db.insert_chunk(doc_id, None, 0, "text", 5, "2026-01-01T00:00:01")
        self.assertEqual(db.orphaned_chunk_ids(), [])

        db.delete_document(doc_id)  # chunk row survives (delete_document doesn't cascade)
        self.assertEqual(db.orphaned_chunk_ids(), [chunk_id])

        db.delete_chunks_by_ids([chunk_id])
        self.assertEqual(db.orphaned_chunk_ids(), [])

    def test_fetch_chunks_by_ids_empty_input(self):
        self.assertEqual(db.fetch_chunks_by_ids([]), {})


class CitationTests(DbTestCase):
    def test_round_trip(self):
        user = db.ensure_demo_user()
        job_id = make_job()
        doc_id = db.insert_document(user["id"], job_id, "attachment", "a.txt", "t", "2026-01-01T00:00:00")
        chunk_id = db.insert_chunk(doc_id, job_id, 0, "text", 5, "2026-01-01T00:00:01")
        sid = db.create_chat_session(job_id, "qa", "m", "2026-01-01T00:00:00")
        mid = db.add_chat_message(sid, job_id, "qa", "assistant", "hi", "m", "2026-01-01T00:00:02")

        db.insert_citation(mid, 1, chunk_id, 0.9, "2026-01-01T00:00:03")
        result = db.fetch_citations_for_messages([mid])
        self.assertEqual(result[mid][0]["chunk_id"], chunk_id)
        self.assertEqual(result[mid][0]["filename"], "a.txt")
        self.assertEqual(db.fetch_citations_for_messages([]), {})


class SettingsTests(DbTestCase):
    def test_get_default_and_set(self):
        self.assertEqual(db.get_setting("nope", "fallback"), "fallback")
        self.assertEqual(db.get_setting("chunk_size_tokens"), "128")
        db.set_setting("chunk_size_tokens", "256")
        self.assertEqual(db.get_setting("chunk_size_tokens"), "256")


class ComponentRunTests(DbTestCase):
    """fetch_all_runs / fetch_run_modes_for_urls - used by the tool admin pages
    (app.py's tool_detail(), src/tools.py)."""

    def test_fetch_all_runs_spans_every_component_newest_first(self):
        r1 = db.insert_run("serpapi", "t1", "test")
        r2 = db.insert_run("remoteok", "t2", "live")
        db.finish_run(r1, "t1b", "ok", 3)
        db.finish_run(r2, "t2b", "ok", 1)
        runs = db.fetch_all_runs()
        self.assertEqual([r["id"] for r in runs], [r2, r1])
        self.assertEqual({r["component_id"] for r in runs}, {"serpapi", "remoteok"})

    def test_fetch_run_modes_for_urls_picks_most_recent_run(self):
        r1 = db.insert_run("serpapi", "t1", "test")
        db.insert_run_result(r1, {"title": "T", "company": "C", "source": "S",
                                   "location": "L", "url": "https://x.com/1"})
        r2 = db.insert_run("serpapi", "t2", "live")
        db.insert_run_result(r2, {"title": "T", "company": "C", "source": "S",
                                   "location": "L", "url": "https://x.com/1"})
        self.assertEqual(db.fetch_run_modes_for_urls(["https://x.com/1"]), {"https://x.com/1": "live"})

    def test_fetch_run_modes_for_urls_empty_input(self):
        self.assertEqual(db.fetch_run_modes_for_urls([]), {})

    def test_fetch_run_modes_for_urls_unknown_url_omitted(self):
        self.assertEqual(db.fetch_run_modes_for_urls(["https://nope.com"]), {})

    def test_save_and_fetch_raw_results_round_trip(self):
        run_id = db.insert_run("serpapi", "t1", "test")
        self.assertEqual(db.fetch_raw_results(run_id), [])  # nothing fetched yet

        listings = [{"title": "A", "url": "https://x.com/1"}, {"title": "B", "url": "https://x.com/2"}]
        db.save_raw_results(run_id, listings, "fetched")
        self.assertEqual(db.fetch_raw_results(run_id), listings)

        run = db.fetch_run(run_id)
        self.assertEqual(run["status"], "fetched")
        self.assertEqual(run["fetched_count"], 2)
        self.assertNotIn("raw_results_json", run)  # heavy blob excluded from the run row itself

    def test_save_raw_results_error_status_and_message(self):
        run_id = db.insert_run("serpapi", "t1", "test")
        db.save_raw_results(run_id, [], "error", "boom")
        run = db.fetch_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertEqual(run["error_message"], "boom")

    def test_clear_raw_results(self):
        run_id = db.insert_run("serpapi", "t1", "test")
        db.save_raw_results(run_id, [{"title": "A", "url": "https://x.com/1"}], "fetched")
        db.clear_raw_results(run_id)
        self.assertEqual(db.fetch_raw_results(run_id), [])

    def test_fetch_raw_results_unknown_run(self):
        self.assertEqual(db.fetch_raw_results(-1), [])


if __name__ == "__main__":
    unittest.main()
