"""src/assistant.py + /assistant/* routes (the floating, cross-page chat)."""
import unittest
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import assistant, store


class HandleTurnChatTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        # Every route_assistant_turn call must be mocked in tests - it's a real send_chat
        # call otherwise, hitting the live OpenRouter API. Default it to "chat" here so each
        # test below only needs to mock what it's actually exercising.
        self.route_patcher = mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "chat", "job_query": None}]
        )
        self.route_patcher.start()
        self.addCleanup(self.route_patcher.stop)

    def test_plain_chat_turn_persists_both_messages(self):
        with mock.patch(
            "src.agents.answer_assistant_message",
            return_value=("Sure, here's an answer.", "model-x", {"prompt_tokens": 5, "completion_tokens": 3}),
        ) as mocked:
            resp = self.client.post("/assistant/message", json={"message": "what roles am I targeting?"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["user_message"]["content"], "what roles am I targeting?")
        self.assertEqual(data["assistant_messages"][0]["content"], "Sure, here's an answer.")
        self.assertEqual(data["assistant_messages"][0]["model"], "model-x")
        mocked.assert_called_once()

    def test_llm_failure_becomes_visible_reply_not_500(self):
        with mock.patch(
            "src.agents.answer_assistant_message", side_effect=RuntimeError("OpenRouter is down")
        ):
            resp = self.client.post("/assistant/message", json={"message": "hi"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("OpenRouter is down", resp.get_json()["assistant_messages"][0]["content"])

    def test_empty_message_rejected(self):
        resp = self.client.post("/assistant/message", json={"message": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_requires_login(self):
        anon = flask_app.app.test_client()
        resp = anon.post("/assistant/message", json={"message": "hi"})
        self.assertEqual(resp.status_code, 302)


class HandleTurnWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_routed_workflow_reply_is_deterministic_no_extra_llm_call(self):
        summary = {
            "per_component": {"serpapi": {"fetched": 2, "added": 2, "error": None}},
            "added": 2, "scan_run_id": 1, "rescored": 5, "scan_error": None,
        }
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "job_search_rerank", "job_query": None}]
        ) as router, mock.patch(
            "src.workflows.WORKFLOWS", {"job_search_rerank": {**assistant.workflows.WORKFLOWS["job_search_rerank"], "run": mock.Mock(return_value=summary)}}
        ), mock.patch("src.agents.answer_assistant_message") as chat_call:
            resp = self.client.post("/assistant/message", json={"message": "run job search and rerank"})

        self.assertEqual(resp.status_code, 200)
        reply = resp.get_json()["assistant_messages"][0]["content"]
        self.assertIn("2", reply)
        self.assertIn("5", reply)
        router.assert_called_once()
        chat_call.assert_not_called()  # deterministic reply, no second LLM call

    def test_rescore_jobs_action_only_rescores_no_sourcing(self):
        # "rank/rerank the jobs" must route to the rescore-only action, not
        # job_search_rerank (which would fetch and add new listings the user never asked for).
        summary = {"scan_run_id": 7, "rescored": 4, "scan_error": None}
        job_search_run = mock.Mock()
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "rescore_jobs", "job_query": None}]
        ), mock.patch("src.workflows.run_rescore_only", return_value=summary) as rescore_run, mock.patch(
            "src.workflows.WORKFLOWS",
            {"job_search_rerank": {**assistant.workflows.WORKFLOWS["job_search_rerank"], "run": job_search_run}},
        ), mock.patch("src.agents.answer_assistant_message") as chat_call:
            resp = self.client.post("/assistant/message", json={"message": "rerank the jobs"})

        self.assertEqual(resp.status_code, 200)
        reply = resp.get_json()["assistant_messages"][0]["content"]
        self.assertIn("4", reply)
        self.assertNotIn("Added", reply)
        rescore_run.assert_called_once()
        job_search_run.assert_not_called()
        chat_call.assert_not_called()


class HandleTurnChainingTests(unittest.TestCase):
    """A single message can plan into more than one step (see agents.route_assistant_turn,
    MAX_CHAIN_STEPS) - handle_turn must run them in order, persist one message per step, and
    stop at the first step that fails instead of running the rest against a bad result."""

    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self.job = store.jobs[0]
        self._original_status = self.job["status"]

    def tearDown(self):
        store.update_job_progress(self.job["id"], status=self._original_status)

    def test_two_step_chain_runs_both_and_returns_two_messages(self):
        summary = {"scan_run_id": 7, "rescored": 4, "scan_error": None}
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[
                {"action": "rescore_jobs", "job_query": None},
                {"action": "job_status", "job_query": self.job["company"], "status": "applied"},
            ],
        ), mock.patch("src.workflows.run_rescore_only", return_value=summary) as rescore_run:
            resp = self.client.post(
                "/assistant/message",
                json={"message": f"rerank all jobs then mark {self.job['company']} applied"},
            )
        self.assertEqual(resp.status_code, 200)
        messages = resp.get_json()["assistant_messages"]
        self.assertEqual(len(messages), 2)
        self.assertIn("4", messages[0]["content"])
        self.assertIn("applied", messages[1]["content"])
        rescore_run.assert_called_once()
        self.assertEqual(store.get_job(self.job["id"])["status"], "applied")

    def test_chain_stops_after_a_step_that_cannot_resolve_a_job(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[
                {"action": "job_status", "job_query": "a job that does not exist anywhere", "status": "applied"},
                {"action": "job_status", "job_query": self.job["company"], "status": "rejected"},
            ],
        ):
            resp = self.client.post(
                "/assistant/message", json={"message": "mark a fake job applied then mark the real one rejected"}
            )
        self.assertEqual(resp.status_code, 200)
        messages = resp.get_json()["assistant_messages"]
        self.assertEqual(len(messages), 1)  # second step never ran
        self.assertEqual(store.get_job(self.job["id"])["status"], self._original_status)

    def test_job_resolved_by_an_earlier_step_carries_to_a_job_agnostic_later_step(self):
        fields = {
            "company": "Chain Co", "title": "Chained Role", "source": "Direct link",
            "location": "Remote", "remote": True, "posted": "2026-01-01",
            "salary_min": 0, "salary_max": 0, "currency": "USD", "description": "",
            "url": "https://example.com/chain-test-1",
        }
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[
                {"action": "add_job_url", "url": fields["url"]},
                {"action": "job_status", "job_query": None, "status": "applied"},
            ],
        ), mock.patch("src.ai.extract_job_posting", return_value=fields):
            resp = self.client.post(
                "/assistant/message", json={"message": f"add {fields['url']} then mark it applied"}
            )
        self.assertEqual(resp.status_code, 200)
        messages = resp.get_json()["assistant_messages"]
        self.assertEqual(len(messages), 2)
        added_job_id = messages[0]["job_id"]
        self.assertIsNotNone(added_job_id)
        self.assertEqual(store.get_job(added_job_id)["status"], "applied")


class ResolveJobTests(unittest.TestCase):
    JOBS = [
        {"id": 1, "title": "Senior Product Manager", "company": "Acme", "match": 60},
        {"id": 2, "title": "Group Product Manager", "company": "Ramp", "match": 90},
        {"id": 3, "title": "Product Lead", "company": "Notion", "match": 75},
    ]

    def test_exact_substring_match(self):
        job, candidates = assistant.resolve_job("Notion", self.JOBS)
        self.assertEqual(job["id"], 3)
        self.assertIsNone(candidates)

    def test_rank_phrase_top_job_uses_highest_match(self):
        job, _ = assistant.resolve_job("the top job", self.JOBS)
        self.assertEqual(job["id"], 2)  # Ramp, match=90

    def test_rank_number(self):
        job, _ = assistant.resolve_job("#2", self.JOBS)
        self.assertEqual(job["id"], 3)  # second-highest match (75)

    def test_ambiguous_match_returns_candidates_not_a_guess(self):
        jobs = self.JOBS + [{"id": 4, "title": "Product Manager", "company": "Acme Robotics", "match": 50}]
        job, candidates = assistant.resolve_job("Acme", jobs)
        self.assertIsNone(job)
        self.assertEqual({c["id"] for c in candidates}, {1, 4})

    def test_no_match_returns_empty_candidates(self):
        job, candidates = assistant.resolve_job("a role that does not exist", self.JOBS)
        self.assertIsNone(job)
        self.assertEqual(candidates, [])

    def test_empty_query_returns_empty_candidates(self):
        job, candidates = assistant.resolve_job(None, self.JOBS)
        self.assertIsNone(job)
        self.assertEqual(candidates, [])


class HandleTurnCoverLetterTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self.job = store.jobs[0]

    def tearDown(self):
        for s in store.get_chat_sessions(self.job["id"], "cover_letter"):
            store.remove_chat_session(s["id"])

    def _mock_generation(self, artifact="Dear hiring manager, ..."):
        return (
            mock.patch(
                "src.agents.route_assistant_turn",
                return_value=[{"action": "cover_letter", "job_query": self.job["company"]}],
            ),
            mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": False}),
            mock.patch(
                "src.agents.run_tailor_turn",
                return_value=({"reply": "Here you go.", "artifact": artifact}, "m", {"prompt_tokens": 1, "completion_tokens": 1}),
            ),
        )

    def test_draft_mirrors_into_thread_and_rating_hits_real_message(self):
        p1, p2, p3 = self._mock_generation()
        with p1, p2, p3:
            resp = self.client.post("/assistant/message", json={"message": f"draft a cover letter for the {self.job['company']} job"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["assistant_messages"][0]
        self.assertEqual(data["job_id"], self.job["id"])
        self.assertIsNotNone(data["linked_chat_message_id"])
        self.assertEqual(data["artifact_text"], "Dear hiring manager, ...")

        rate_resp = self.client.post(f"/messages/{data['linked_chat_message_id']}/rate", json={"rating": "up"})
        self.assertEqual(rate_resp.status_code, 200)
        real_message = store.get_chat_message(data["linked_chat_message_id"])
        self.assertEqual(real_message["rating"], "up")

    def test_second_turn_reuses_same_session(self):
        p1, p2, p3 = self._mock_generation()
        with p1, p2, p3:
            self.client.post("/assistant/message", json={"message": "draft a cover letter"})
        sessions_after_first = store.get_chat_sessions(self.job["id"], "cover_letter")
        self.assertEqual(len(sessions_after_first), 1)

        p1, p2, p3 = self._mock_generation(artifact="revised text")
        with p1, p2, p3:
            self.client.post("/assistant/message", json={"message": "make it shorter"})
        sessions_after_second = store.get_chat_sessions(self.job["id"], "cover_letter")
        self.assertEqual(len(sessions_after_second), 1)
        self.assertEqual(sessions_after_second[0]["id"], sessions_after_first[0]["id"])

    def test_unresolvable_job_asks_for_clarification_no_generation_call(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "cover_letter", "job_query": "a job that does not exist anywhere"}],
        ), mock.patch("src.agents.run_tailor_turn") as run_tailor_turn:
            resp = self.client.post("/assistant/message", json={"message": "draft a cover letter for that one job"})
        self.assertEqual(resp.status_code, 200)
        run_tailor_turn.assert_not_called()

    def test_active_job_carries_across_a_job_agnostic_turn_in_between(self):
        # Turn 1: job named explicitly - drafts for self.job and sets it as the active job.
        p1, p2, p3 = self._mock_generation()
        with p1, p2, p3:
            self.client.post("/assistant/message", json={"message": f"draft a cover letter for the {self.job['company']} job"})
        self.assertEqual(store.get_active_job_id(), self.job["id"])

        # Turn 2: a job-agnostic workflow turn in between - must not clobber the active job.
        summary = {"per_component": {}, "added": 0, "scan_run_id": None, "rescored": 0, "scan_error": None}
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "job_search_rerank", "job_query": None}]
        ), mock.patch(
            "src.workflows.WORKFLOWS", {"job_search_rerank": {**assistant.workflows.WORKFLOWS["job_search_rerank"], "run": mock.Mock(return_value=summary)}}
        ):
            self.client.post("/assistant/message", json={"message": "run job search and rerank"})
        self.assertEqual(store.get_active_job_id(), self.job["id"])  # unchanged

        # Turn 3: feedback with no job named - should still resolve back to self.job.
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "cover_letter", "job_query": None}]
        ), mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": False}), \
             mock.patch(
                 "src.agents.run_tailor_turn",
                 return_value=({"reply": "Shortened.", "artifact": "shorter text"}, "m", {"prompt_tokens": 1, "completion_tokens": 1}),
             ):
            resp = self.client.post("/assistant/message", json={"message": "make it shorter"})
        self.assertEqual(resp.get_json()["assistant_messages"][0]["job_id"], self.job["id"])
        self.assertEqual(len(store.get_chat_sessions(self.job["id"], "cover_letter")), 1)  # same session reused


class HandleTurnResumeQaTests(unittest.TestCase):
    """resume/qa share _handle_tailoring_turn with cover_letter (see
    HandleTurnCoverLetterTests) - these just confirm each tab routes independently and
    lands in its own session, not cover_letter's."""

    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self.job = store.jobs[0]

    def tearDown(self):
        for tab in ("cover_letter", "resume", "qa"):
            for s in store.get_chat_sessions(self.job["id"], tab):
                store.remove_chat_session(s["id"])

    def _mock_generation(self, action, artifact="Some content"):
        return (
            mock.patch(
                "src.agents.route_assistant_turn",
                return_value=[{"action": action, "job_query": self.job["company"]}],
            ),
            mock.patch("src.agents.classify_turn", return_value={"needs_retrieval": False, "reveals_preference": False}),
            mock.patch(
                "src.agents.run_tailor_turn",
                return_value=({"reply": "Here you go.", "artifact": artifact}, "m", {"prompt_tokens": 1, "completion_tokens": 1}),
            ),
        )

    def test_resume_action_creates_its_own_session(self):
        p1, p2, p3 = self._mock_generation("resume", artifact="Resume text")
        with p1, p2, p3:
            resp = self.client.post("/assistant/message", json={"message": f"draft a resume for the {self.job['company']} job"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["assistant_messages"][0]["artifact_text"], "Resume text")
        self.assertEqual(len(store.get_chat_sessions(self.job["id"], "resume")), 1)
        self.assertEqual(len(store.get_chat_sessions(self.job["id"], "cover_letter")), 0)

    def test_qa_action_creates_its_own_session(self):
        p1, p2, p3 = self._mock_generation("qa", artifact="Q: ...\nA: ...")
        with p1, p2, p3:
            resp = self.client.post("/assistant/message", json={"message": f"draft Q&A answers for the {self.job['company']} job"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(store.get_chat_sessions(self.job["id"], "qa")), 1)

    def test_unresolvable_job_asks_for_clarification(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "resume", "job_query": "a job that does not exist anywhere"}],
        ), mock.patch("src.agents.run_tailor_turn") as run_tailor_turn:
            resp = self.client.post("/assistant/message", json={"message": "draft a resume for that job"})
        self.assertEqual(resp.status_code, 200)
        run_tailor_turn.assert_not_called()


class HandleTurnJobStatusTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        self.job = store.jobs[0]
        self._original_status = self.job["status"]

    def tearDown(self):
        store.update_job_progress(self.job["id"], status=self._original_status)

    def test_marks_status_by_name(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "job_status", "job_query": self.job["company"], "status": "applied"}],
        ):
            resp = self.client.post("/assistant/message", json={"message": f"mark {self.job['company']} as applied"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("applied", resp.get_json()["assistant_messages"][0]["content"])
        self.assertEqual(store.get_job(self.job["id"])["status"], "applied")

    def test_unrecognized_status_is_not_written(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "job_status", "job_query": self.job["company"], "status": "ghosted"}],
        ):
            resp = self.client.post("/assistant/message", json={"message": f"mark {self.job['company']} as ghosted"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("recognize", resp.get_json()["assistant_messages"][0]["content"])
        self.assertEqual(store.get_job(self.job["id"])["status"], self._original_status)

    def test_no_status_given_asks_for_one(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "job_status", "job_query": self.job["company"], "status": None}],
        ):
            resp = self.client.post("/assistant/message", json={"message": f"update {self.job['company']}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(store.get_job(self.job["id"])["status"], self._original_status)

    def test_no_job_named_or_active_asks_for_clarification(self):
        # Force "no active job" regardless of what earlier tests in this module left active -
        # store.get_active_job_id persists across tests sharing the session DB (see
        # tests/db_setup.py), so this can't just rely on a fresh state.
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "job_status", "job_query": None, "status": "applied"}],
        ), mock.patch("src.store.get_active_job_id", return_value=None):
            resp = self.client.post("/assistant/message", json={"message": "mark it as applied"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Which job", resp.get_json()["assistant_messages"][0]["content"])


class HandleTurnAddJobUrlTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_adds_job_from_extracted_fields(self):
        fields = {
            "company": "Chat Co", "title": "Chat-Added Role", "source": "Direct link",
            "location": "Remote", "remote": True, "posted": "2026-01-01",
            "salary_min": 0, "salary_max": 0, "currency": "USD", "description": "",
            "url": "https://example.com/chat-add-test-1",
        }
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "add_job_url", "url": fields["url"]}],
        ), mock.patch("src.ai.extract_job_posting", return_value=fields) as extract:
            resp = self.client.post("/assistant/message", json={"message": f"add this job: {fields['url']}"})
        self.assertEqual(resp.status_code, 200)
        reply = resp.get_json()["assistant_messages"][0]["content"]
        self.assertIn("Chat-Added Role", reply)
        self.assertIn("Chat Co", reply)
        extract.assert_called_once_with(fields["url"])
        self.assertTrue(store.job_url_exists(fields["url"]))

    def test_duplicate_url_is_not_re_added(self):
        existing_url = store.jobs[0]["url"]
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "add_job_url", "url": existing_url}]
        ), mock.patch("src.ai.extract_job_posting") as extract:
            resp = self.client.post("/assistant/message", json={"message": f"add {existing_url}"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already", resp.get_json()["assistant_messages"][0]["content"])
        extract.assert_not_called()

    def test_extraction_failure_becomes_visible_reply(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "add_job_url", "url": "https://example.com/bad-posting"}],
        ), mock.patch("src.ai.extract_job_posting", side_effect=RuntimeError("Could not fetch that page")):
            resp = self.client.post("/assistant/message", json={"message": "add https://example.com/bad-posting"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Could not fetch", resp.get_json()["assistant_messages"][0]["content"])

    def test_no_url_extracted_asks_for_one(self):
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "add_job_url", "url": None}]
        ), mock.patch("src.ai.extract_job_posting") as extract:
            resp = self.client.post("/assistant/message", json={"message": "add a job for me"})
        self.assertEqual(resp.status_code, 200)
        extract.assert_not_called()


class HandleTurnPreferenceLearningTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_no_job_named_scopes_to_everything(self):
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "preference_learning", "job_query": None}],
        ):
            resp = self.client.post("/assistant/message", json={"message": "what have you learned from my feedback?"})
        self.assertEqual(resp.status_code, 200)
        reply = resp.get_json()["assistant_messages"][0]["content"]
        self.assertIn("your feedback message", reply)
        self.assertIsNone(resp.get_json()["assistant_messages"][0]["job_id"])

    def test_job_named_scopes_to_that_job(self):
        job = store.jobs[0]
        with mock.patch(
            "src.agents.route_assistant_turn",
            return_value=[{"action": "preference_learning", "job_query": job["company"]}],
        ):
            resp = self.client.post(
                "/assistant/message", json={"message": f"what have you learned from feedback on {job['company']}?"}
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["assistant_messages"][0]
        self.assertIn("that job's feedback message", data["content"])
        self.assertEqual(data["job_id"], job["id"])


class HandleTurnUnclearTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_unclear_action_lists_available_actions_no_chat_call(self):
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "unclear", "job_query": None}]
        ), mock.patch("src.agents.answer_assistant_message") as chat_call:
            resp = self.client.post("/assistant/message", json={"message": "add this url and rerank"})
        self.assertEqual(resp.status_code, 200)
        reply = resp.get_json()["assistant_messages"][0]["content"]
        self.assertIn("Not sure", reply)
        for a in assistant.FIXED_ACTIONS:
            self.assertIn(a["name"], reply)
        chat_call.assert_not_called()


class AssistantHistoryAndModelTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_history_reflects_persisted_messages(self):
        store.add_assistant_message("user", "hello there", model="m")
        data = self.client.get("/assistant/history").get_json()
        self.assertIn("hello there", [m["content"] for m in data["messages"]])
        self.assertIn("model", data)

    def test_model_switch_persists_and_rejects_unknown(self):
        from src import agents
        known = agents.MODEL_OPTIONS[-1]
        resp = self.client.post("/assistant/model", json={"model": known})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(store.get_assistant_model(None), known)

        resp = self.client.post("/assistant/model", json={"model": "not-a-real-model"})
        self.assertEqual(resp.status_code, 400)

    def test_model_switch_applies_to_next_turn_same_thread_no_fork(self):
        from src import agents
        picked = agents.MODEL_OPTIONS[-1]
        self.client.post("/assistant/model", json={"model": picked})
        self.assertEqual(self.client.get("/assistant/history").get_json()["model"], picked)

        before_count = len(store.get_assistant_messages())
        with mock.patch(
            "src.agents.route_assistant_turn", return_value=[{"action": "chat", "job_query": None}]
        ), mock.patch("src.agents.answer_assistant_message", return_value=("ok", picked, {})) as answer:
            self.client.post("/assistant/message", json={"message": "hi"})

        # The turn used the newly picked model...
        self.assertEqual(answer.call_args.args[-1], picked)
        # ...and it's still the same single thread: exactly 2 new rows (user + assistant), no
        # new session/table created by the switch itself.
        self.assertEqual(len(store.get_assistant_messages()), before_count + 2)


if __name__ == "__main__":
    unittest.main()
