"""src/agents.py - pure helper functions, plus send_chat/send_message with the actual
OpenRouter network call mocked out (never hits the network in tests)."""
import io
import json
import unittest
import urllib.error
from unittest import mock

from src import agents


def http_error(code, body):
    return urllib.error.HTTPError("url", code, "err", {}, io.BytesIO(body.encode()))


class PureHelperTests(unittest.TestCase):
    def test_strip_json_fence_removes_markdown_fence(self):
        self.assertEqual(agents.strip_json_fence('```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(agents.strip_json_fence('{"a": 1}'), '{"a": 1}')
        self.assertEqual(agents.strip_json_fence('```\n{"a": 1}\n```'), '{"a": 1}')

    def test_strip_citations_removes_markers_and_collapses_spaces(self):
        text = "Great fit. [Source 1] I have experience.  [Source 12]  Done."
        self.assertEqual(agents.strip_citations(text), "Great fit. I have experience. Done.")

    def test_strip_citations_empty_input(self):
        self.assertEqual(agents.strip_citations(""), "")
        self.assertIsNone(agents.strip_citations(None))

    def test_trim_history_caps_to_max_messages(self):
        history = [{"role": "user", "content": str(i)} for i in range(10)]
        trimmed = agents.trim_history(history)
        self.assertEqual(len(trimmed), agents.HISTORY_MAX_MESSAGES)
        self.assertEqual(trimmed[-1]["content"], "9")

    def test_trim_history_shorter_than_cap_unchanged(self):
        history = [{"role": "user", "content": "hi"}]
        self.assertEqual(agents.trim_history(history), history)

    def test_build_retrieval_query_folds_in_job_context(self):
        job = {"title": "PM", "description": "own the roadmap"}
        query = agents.build_retrieval_query(job, "generate the cover letter")
        self.assertEqual(query, "PM\nown the roadmap\ngenerate the cover letter")

    def test_build_retrieval_query_missing_description(self):
        query = agents.build_retrieval_query({"title": "PM"}, "hi")
        self.assertEqual(query, "PM\n\nhi")

    def test_format_retrieved_context_empty(self):
        self.assertIn("nothing relevant", agents.format_retrieved_context([]))

    def test_format_retrieved_context_numbers_and_scores_chunks(self):
        chunks = [{"score": 0.87, "filename": "resume.pdf", "text": "did stuff"}]
        formatted = agents.format_retrieved_context(chunks)
        self.assertIn("[Source 1]", formatted)
        self.assertIn("0.87", formatted)
        self.assertIn("resume.pdf", formatted)
        self.assertIn("did stuff", formatted)

    def test_build_system_content_plain_string_for_non_anthropic_model(self):
        content = agents._build_system_content("A", "B", "openai/gpt-4o-mini")
        self.assertEqual(content, "A\n\nB")

    def test_build_system_content_cache_blocks_for_anthropic_model(self):
        content = agents._build_system_content("A", "B", "anthropic/claude-sonnet-5")
        self.assertEqual(content, [
            {"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "B"},
        ])

    def test_estimate_cost_free_model_is_zero(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 1000}
        self.assertEqual(agents._estimate_cost("google/gemma-4-26b-a4b-it:free", usage), 0)

    def test_estimate_cost_paid_model(self):
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
        self.assertAlmostEqual(agents._estimate_cost("openai/gpt-4o-mini", usage), 0.15 + 0.60)

    def test_estimate_cost_unknown_model_falls_back_to_default_pricing(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 1000}
        self.assertEqual(agents._estimate_cost("some/unknown-model", usage), 0)

    def test_parse_json_reply_plain_json(self):
        self.assertEqual(agents._parse_json_reply('{"a": 1}'), {"a": 1})

    def test_parse_json_reply_strips_fence(self):
        self.assertEqual(agents._parse_json_reply('```json\n{"a": 1}\n```'), {"a": 1})

    def test_parse_json_reply_extracts_json_from_surrounding_prose(self):
        reply = 'Sure, here you go:\n{"a": 1}\nHope that helps!'
        self.assertEqual(agents._parse_json_reply(reply), {"a": 1})

    def test_parse_json_reply_unparseable_raises_unusable_reply(self):
        with self.assertRaises(agents.UnusableReply):
            agents._parse_json_reply("not json at all")

    def test_tailor_reply_incomplete_qa_new_question_without_answer(self):
        self.assertTrue(agents._tailor_reply_incomplete("qa", {"action": "new_question", "answer": ""}, ""))
        self.assertFalse(agents._tailor_reply_incomplete("qa", {"action": "new_question", "answer": "yes"}, ""))

    def test_tailor_reply_incomplete_artifact_type_first_draft_empty(self):
        self.assertTrue(agents._tailor_reply_incomplete("cover_letter", {"artifact": ""}, ""))
        self.assertFalse(agents._tailor_reply_incomplete("cover_letter", {"artifact": ""}, "existing draft"))
        self.assertFalse(agents._tailor_reply_incomplete("cover_letter", {"artifact": "new text"}, ""))

    def test_format_error_with_openrouter_error_body(self):
        body = '{"error": {"message": "bad request", "metadata": {"raw": "detail"}}}'
        msg = agents._format_error(400, body)
        self.assertIn("bad request", msg)
        self.assertIn("detail", msg)

    def test_format_error_with_non_json_body(self):
        msg = agents._format_error(500, "plain text failure")
        self.assertIn("500", msg)
        self.assertIn("plain text failure", msg)

    def test_retry_after_seconds_present_and_absent(self):
        self.assertEqual(agents._retry_after_seconds('{"error": {"metadata": {"retry_after_seconds": 7}}}'), 7)
        self.assertIsNone(agents._retry_after_seconds("not json"))
        self.assertIsNone(agents._retry_after_seconds("{}"))


class SendChatTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_raises_without_api_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                agents.send_chat([{"role": "user", "content": "hi"}])

    def test_success_logs_usage(self):
        response = {
            "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        with mock.patch.object(agents, "_post", return_value=response), \
             mock.patch.object(agents, "_log_last_call"), \
             mock.patch.object(agents, "_log_usage") as mock_log_usage:
            content, used_model, usage = agents.send_message("hello", model="openai/gpt-4o-mini")

        self.assertEqual(content, "hi there")
        self.assertEqual(used_model, "openai/gpt-4o-mini")
        self.assertEqual(usage["total_tokens"], 15)
        mock_log_usage.assert_called_once_with("openai/gpt-4o-mini", response["usage"])

    def test_null_content_raises_unusable_reply(self):
        response = {"choices": [{"message": {"content": None}, "finish_reason": "stop"}], "usage": {}}
        with mock.patch.object(agents, "_post", return_value=response), \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"):
            with self.assertRaisesRegex(agents.UnusableReply, "no reply"):
                agents.send_message("hello", model="openai/gpt-4o-mini")

    def test_length_cutoff_raises_unusable_reply(self):
        response = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {}}
        with mock.patch.object(agents, "_post", return_value=response), \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"):
            with self.assertRaisesRegex(agents.UnusableReply, "cut off"):
                agents.send_message("hello", model="openai/gpt-4o-mini")

    def test_non_rate_limit_http_error_raises_runtime_error(self):
        with mock.patch.object(agents, "_post", side_effect=http_error(500, '{"error": {"message": "boom"}}')):
            with self.assertRaises(RuntimeError):
                agents.send_message("hello", model="openai/gpt-4o-mini")

    def test_free_model_falls_back_on_rate_limit(self):
        first_model = agents.FREE_MODEL_PRIORITY[0]

        def fake_post(messages, model, api_key):
            if model == first_model:
                raise http_error(429, '{"error": {"message": "rate limited"}}')
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        with mock.patch.object(agents, "_post", side_effect=fake_post), \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"), \
             mock.patch.object(agents, "MAX_RATE_LIMIT_WAIT_SECONDS", 0), \
             mock.patch.object(agents.time, "sleep"):
            content, used_model, _usage = agents.send_message("hi", model=first_model)

        self.assertEqual(content, "ok")
        self.assertNotEqual(used_model, first_model)
        self.assertIn(used_model, agents.FREE_MODEL_PRIORITY)

    def test_free_model_falls_back_on_length_cutoff(self):
        first_model = agents.FREE_MODEL_PRIORITY[0]

        def fake_post(messages, model, api_key):
            if model == first_model:
                return {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {}}
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        with mock.patch.object(agents, "_post", side_effect=fake_post), \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"):
            content, used_model, _usage = agents.send_message("hi", model=first_model)

        self.assertEqual(content, "ok")
        self.assertNotEqual(used_model, first_model)
        self.assertIn(used_model, agents.FREE_MODEL_PRIORITY)

    def test_free_model_falls_back_on_null_content(self):
        first_model = agents.FREE_MODEL_PRIORITY[0]

        def fake_post(messages, model, api_key):
            if model == first_model:
                return {"choices": [{"message": {"content": None}, "finish_reason": "stop"}], "usage": {}}
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        with mock.patch.object(agents, "_post", side_effect=fake_post), \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"):
            content, used_model, _usage = agents.send_message("hi", model=first_model)

        self.assertEqual(content, "ok")
        self.assertNotEqual(used_model, first_model)

    def test_free_model_raises_last_error_when_every_fallback_unusable(self):
        response = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {}}
        with mock.patch.object(agents, "_post", return_value=response), \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"):
            with self.assertRaisesRegex(agents.UnusableReply, "cut off"):
                agents.send_message("hi", model=agents.FREE_MODEL_PRIORITY[0])

    def test_non_free_model_gets_no_fallback_on_length_cutoff(self):
        response = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {}}
        with mock.patch.object(agents, "_post", return_value=response) as mock_post, \
             mock.patch.object(agents, "_log_last_call"), mock.patch.object(agents, "_log_usage"):
            with self.assertRaises(agents.UnusableReply):
                agents.send_message("hi", model="openai/gpt-4o-mini")
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
