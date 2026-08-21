"""Workflows dashboard (/workflows, src/workflows.py): registry integrity, page render, and
the live workflows' runners.
"""
import unittest
from unittest import mock

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import components as comp
from src import store, tools, workflows

LISTING = {
    "title": "Senior Product Manager", "company": "Acme", "source": "Test",
    "location": "United States", "remote": True, "posted": "2026-01-01",
    "salary_min": 0, "salary_max": 0, "currency": "USD", "description": "",
}


class WorkflowsRegistryTests(unittest.TestCase):
    def test_every_component_ref_exists(self):
        for w in workflows.WORKFLOWS.values():
            for kind, ref in w["uses"]:
                if kind == "component":
                    self.assertIn(ref, comp.COMPONENTS, f"{w['name']}: unknown component {ref!r}")

    def test_every_tool_ref_exists(self):
        for w in workflows.WORKFLOWS.values():
            for kind, ref in w["uses"]:
                if kind == "tool":
                    self.assertIn(ref, tools.TOOLS, f"{w['name']}: unknown tool {ref!r}")

    def test_uses_kind_is_known(self):
        for w in workflows.WORKFLOWS.values():
            for kind, ref in w["uses"]:
                self.assertIn(kind, ("component", "tool", "other"))

    def test_every_live_workflow_has_a_run_callable(self):
        for w in workflows.WORKFLOWS.values():
            if w["status"] == "live":
                self.assertTrue(callable(w.get("run")), f"{w['name']}: status live but no run callable")


def _mock_component_run(component_id, listings=None, error=None):
    return mock.patch.dict(comp.COMPONENTS[component_id], {"run": mock.Mock(return_value=(listings or [], error))})


class WorkflowRunnerTests(unittest.TestCase):
    def setUp(self):
        self._original_roles = list(store.profile["roles"])
        store.profile["roles"] = ["Product Manager"]
        store.save_profile()

    def tearDown(self):
        store.profile["roles"] = self._original_roles
        store.save_profile()

    def test_one_component_erroring_does_not_stop_the_others(self):
        listing = {**LISTING, "url": "https://x.com/workflow-1"}
        with _mock_component_run("serpapi", error="boom"), \
             _mock_component_run("remoteok", listings=[listing]), \
             _mock_component_run("ats", listings=[]), \
             mock.patch("src.scanner.run_scan", return_value=999):
            summary = workflows.run_job_search_rerank(mode="test")

        self.assertEqual(summary["per_component"]["serpapi"]["error"], "boom")
        self.assertEqual(summary["per_component"]["serpapi"]["added"], 0)
        self.assertIsNone(summary["per_component"]["remoteok"]["error"])
        self.assertEqual(summary["per_component"]["remoteok"]["added"], 1)
        self.assertEqual(summary["added"], 1)
        self.assertTrue(any(j["url"] == listing["url"] for j in store.jobs))

    def test_never_raises_even_if_every_component_errors(self):
        with _mock_component_run("serpapi", error="a"), \
             _mock_component_run("remoteok", error="b"), \
             _mock_component_run("ats", error="c"), \
             mock.patch("src.scanner.run_scan", return_value=999):
            summary = workflows.run_job_search_rerank(mode="test")
        self.assertEqual(summary["added"], 0)
        self.assertEqual(len(summary["per_component"]), 3)

    def test_component_raising_an_exception_is_recorded_not_fatal(self):
        with mock.patch.dict(comp.COMPONENTS["serpapi"], {"run": mock.Mock(side_effect=RuntimeError("no api key"))}), \
             _mock_component_run("remoteok", listings=[]), \
             _mock_component_run("ats", listings=[]), \
             mock.patch("src.scanner.run_scan", return_value=999):
            summary = workflows.run_job_search_rerank(mode="test")
        self.assertIn("no api key", summary["per_component"]["serpapi"]["error"])


class RescoreOnlyRunnerTests(unittest.TestCase):
    def test_rescores_without_touching_any_sourcing_component(self):
        mock_runs = {cid: _mock_component_run(cid) for cid in comp.COMPONENTS}
        for patcher in mock_runs.values():
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in mock_runs.values()])

        with mock.patch("src.scanner.run_scan", return_value=999) as run_scan:
            summary = workflows.run_rescore_only(mode="test")

        for component_id in comp.COMPONENTS:
            comp.COMPONENTS[component_id]["run"].assert_not_called()
        run_scan.assert_called_once_with(mode="test")
        self.assertEqual(summary["scan_run_id"], 999)

    def test_summary_shape_matches_scoring_run(self):
        with mock.patch("src.scanner.run_scan", return_value=123), \
             mock.patch("src.store.get_scoring_run", return_value={"scored_count": 4, "error_message": None}):
            summary = workflows.run_rescore_only(mode="live")

        self.assertEqual(summary, {"scan_run_id": 123, "rescored": 4, "scan_error": None})


class WorkflowsPageTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_page_lists_every_workflow(self):
        html = self.client.get("/workflows").get_data(as_text=True)
        for w in workflows.WORKFLOWS.values():
            self.assertIn(w["name"], html)

    def test_component_and_tool_chips_link_to_their_pages(self):
        html = self.client.get("/workflows").get_data(as_text=True)
        self.assertIn("/components/serpapi", html)
        self.assertIn("/tools/tailored_generation", html)

    def test_page_lists_every_chat_action(self):
        # Same catalogue the assistant's router and "unclear" clarification use (see
        # src/assistant.py's FIXED_ACTIONS) - this page is meant to be the human-readable
        # mirror of it, so it can't silently drift from what chat actually supports.
        import html as html_module
        from src import assistant
        html = html_module.unescape(self.client.get("/workflows").get_data(as_text=True))
        for a in assistant.FIXED_ACTIONS:
            self.assertIn(a["name"], html)

    def test_requires_login(self):
        anon = flask_app.app.test_client()
        resp = anon.get("/workflows")
        self.assertEqual(resp.status_code, 302)


if __name__ == "__main__":
    unittest.main()
