"""src/ai.py - suggest_roles/suggest_home_address (placeholders), score_job (real LLM call,
agents.send_message mocked out - never hits the network), plus _strip_html (pure helper
behind extract_job_posting)."""
import json
import unittest
from unittest import mock

from src import ai


def job(**overrides):
    j = {"title": "Senior Product Manager", "description": "own the roadmap"}
    j.update(overrides)
    return j


def mock_reply(score, summary="fits well"):
    return (json.dumps({"score": score, "summary": summary}), "some/model", {})


class PlaceholderTests(unittest.TestCase):
    def test_suggest_roles_and_home_address_are_deterministic(self):
        self.assertEqual(ai.suggest_roles("anything.pdf"), ai.GENERIC_ROLE_SUGGESTIONS)
        self.assertEqual(ai.suggest_home_address("anything.pdf"), "San Francisco, CA")


class ScoreJobTests(unittest.TestCase):
    @mock.patch("src.ai.agents.send_message")
    def test_parses_score_and_summary_from_reply(self, send_message):
        send_message.return_value = mock_reply(82, "Strong resume overlap")
        result = ai.score_job(job(), "resume text", "story text", ["Fintech"], "care about impact")
        self.assertEqual(result, {"score": 82, "summary": "Strong resume overlap"})

    @mock.patch("src.ai.agents.send_message")
    def test_passes_model_through(self, send_message):
        send_message.return_value = mock_reply(50)
        ai.score_job(job(), "resume", "", [], "", model="custom/model")
        self.assertEqual(send_message.call_args.args[1], "custom/model")

    @mock.patch("src.ai.agents.send_message")
    def test_score_clamped_above_100(self, send_message):
        send_message.return_value = mock_reply(150)
        self.assertEqual(ai.score_job(job(), "resume", "", [], "")["score"], 100)

    @mock.patch("src.ai.agents.send_message")
    def test_score_clamped_below_0(self, send_message):
        send_message.return_value = mock_reply(-20)
        self.assertEqual(ai.score_job(job(), "resume", "", [], "")["score"], 0)

    @mock.patch("src.ai.agents.send_message")
    def test_missing_story_bank_and_industries_still_builds_prompt(self, send_message):
        send_message.return_value = mock_reply(60)
        result = ai.score_job(job(), "resume", "", [], "")
        self.assertEqual(result["score"], 60)
        prompt = send_message.call_args.args[0]
        self.assertIn("(none provided)", prompt)
        self.assertIn("(none specified)", prompt)

    @mock.patch("src.ai.agents.send_message")
    def test_malformed_json_reply_raises(self, send_message):
        send_message.return_value = ("not json", "some/model", {})
        with self.assertRaises(RuntimeError):
            ai.score_job(job(), "resume", "", [], "")

    @mock.patch("src.ai.agents.send_message")
    def test_missing_keys_in_reply_default_to_zero_and_empty(self, send_message):
        send_message.return_value = (json.dumps({}), "some/model", {})
        self.assertEqual(ai.score_job(job(), "resume", "", [], ""), {"score": 0, "summary": ""})


class StripHtmlTests(unittest.TestCase):
    def test_removes_tags_scripts_and_styles(self):
        html = "<html><head><style>.a{color:red}</style></head><body><script>alert(1)</script><p>Hello  world</p></body></html>"
        self.assertEqual(ai._strip_html(html), "Hello world")


if __name__ == "__main__":
    unittest.main()
