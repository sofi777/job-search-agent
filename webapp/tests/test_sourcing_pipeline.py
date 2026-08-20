"""Fetch and filter/dedup are separate stages, never auto-chained (store.save_fetch_results
/ store.apply_run_filters) - a component's run() only fetches; filtering is a distinct,
explicit step against just a run_id, run from the component page or the merged
Filter, dedupe & save tool page (/tools/filter_dedupe). See src/filters.py and app.py's
component_run / component_run_filter / tool_detail.
"""
import unittest

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
import app as flask_app
from src import store, db

LISTING = {
    # remote=True + a country the default demo profile is eligible in - keeps these listings
    # out of the (unrelated) commute-range gate for onsite postings, see src/filters.py.
    "title": "Senior Product Manager", "company": "Acme", "source": "Test",
    "location": "United States", "remote": True, "posted": "2026-01-01",
    "salary_min": 0, "salary_max": 0, "currency": "USD", "description": "",
}


class ApplyRunFiltersTests(unittest.TestCase):
    def setUp(self):
        self._original_roles = list(store.profile["roles"])
        store.profile["roles"] = ["Product Manager"]
        store.save_profile()

    def tearDown(self):
        store.profile["roles"] = self._original_roles
        store.save_profile()

    def test_fetch_then_filter_are_independent_calls(self):
        run_id = store.start_run("serpapi", "test")
        listing = {**LISTING, "url": "https://x.com/pipeline-1"}

        # Fetch stage: raw results stored, nothing staged yet.
        store.save_fetch_results(run_id, [listing])
        self.assertEqual(store.get_run(run_id)["status"], "fetched")
        self.assertEqual(store.get_run_results(run_id), [])

        # Filter stage: called separately, with only the run_id - no knowledge of the
        # component or config that produced the raw data.
        store.apply_run_filters(run_id)
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "ok")
        self.assertEqual([r["url"] for r in store.get_run_results(run_id)], [listing["url"]])
        self.assertEqual(db.fetch_raw_results(run_id), [])  # cleared once filtered

    def test_apply_run_filters_drops_what_the_profile_excludes(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-2", "title": "Barista"}])
        store.apply_run_filters(run_id)
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["filtered_count"], 1)
        self.assertEqual(store.get_run_results(run_id), [])

    def test_apply_run_filters_is_noop_before_fetch_or_after_already_filtered(self):
        run_id = store.start_run("serpapi", "test")
        store.apply_run_filters(run_id)  # nothing fetched yet
        self.assertEqual(store.get_run(run_id)["status"], "running")

        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-3"}])
        store.apply_run_filters(run_id)
        staged_before = store.get_run_results(run_id)

        store.apply_run_filters(run_id)  # already "ok", not "fetched" - must not re-stage
        self.assertEqual(store.get_run_results(run_id), staged_before)

    def test_fetch_error_with_no_listings_is_terminal_without_filtering(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [], "boom")
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "error")

        store.apply_run_filters(run_id)  # not "fetched" - must not try to filter
        self.assertEqual(store.get_run(run_id)["status"], "error")

    def test_fetch_error_with_partial_listings_still_filters_then_reports_error(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-4"}], "partial failure")
        self.assertEqual(store.get_run(run_id)["status"], "fetched")  # still pending, error kept for later

        store.apply_run_filters(run_id)
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "error")  # original fetch error surfaces once filtering finishes
        self.assertEqual(len(store.get_run_results(run_id)), 1)  # but the listing was still staged


class ComponentRunRouteTests(unittest.TestCase):
    """component_run() only fetches now - it leaves a run in "fetched" (pending) state.
    component_run_filter() is the separate, explicit filter step."""

    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        store.profile["onboarding_complete"] = True
        store.save_profile()

    def test_run_route_only_fetches_leaves_run_pending(self):
        resp = self.client.post("/components/serpapi/run", data={"mode": "test"})
        self.assertEqual(resp.status_code, 302)
        run_id = int(resp.headers["Location"].rsplit("run=", 1)[1])
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "fetched")
        self.assertEqual(store.get_run_results(run_id), [])  # nothing staged until filtered

    def test_filter_route_applies_to_a_run_fetched_by_the_run_route(self):
        resp = self.client.post("/components/serpapi/run", data={"mode": "test"})
        run_id = int(resp.headers["Location"].rsplit("run=", 1)[1])

        resp = self.client.post(f"/components/serpapi/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "ok")

    def test_filter_route_applies_to_a_run_fetched_independently(self):
        # Simulate a run fetched by something other than component_run() - just a run_id
        # with raw results already sitting on it.
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-route"}])

        resp = self.client.post(f"/components/serpapi/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "ok")

    def test_filter_route_rejects_mismatched_component(self):
        run_id = store.start_run("serpapi", "test")
        resp = self.client.post(f"/components/remoteok/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "running")  # untouched


class FilterDedupeToolPageTests(unittest.TestCase):
    """The merged Filter, dedupe & save tool page (/tools/filter_dedupe): a Run button per
    pending run log row, staged-for-review results with "Add to dashboard", and the
    already-saved log - all in one page (previously split across two tools)."""

    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")

    def test_run_button_shown_only_for_fetched_runs(self):
        fetched_id = store.start_run("serpapi", "test")
        store.save_fetch_results(fetched_id, [{**LISTING, "url": "https://x.com/tool-page-1"}])

        done_id = store.start_run("serpapi", "test")
        store.save_fetch_results(done_id, [{**LISTING, "url": "https://x.com/tool-page-2"}])
        store.apply_run_filters(done_id)

        html = self.client.get("/tools/filter_dedupe").get_data(as_text=True)
        filter_url = f"/components/serpapi/runs/{fetched_id}/filter"
        done_url = f"/components/serpapi/runs/{done_id}/filter"
        self.assertIn(filter_url, html)
        self.assertNotIn(done_url, html)

    def test_run_button_applies_filters(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/tool-page-3"}])

        resp = self.client.post(f"/components/serpapi/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "ok")

    def test_staged_results_shown_and_disappear_once_added(self):
        run_id = store.start_run("serpapi", "test")
        url = "https://x.com/tool-page-staged"
        store.save_fetch_results(run_id, [{**LISTING, "url": url}])
        store.apply_run_filters(run_id)
        result_id = store.get_run_results(run_id)[0]["id"]

        html = self.client.get("/tools/filter_dedupe").get_data(as_text=True)
        self.assertIn(url, html)

        self.client.post(f"/components/serpapi/results/{result_id}/add")
        html = self.client.get("/tools/filter_dedupe").get_data(as_text=True)
        self.assertNotIn(url, html)  # now on the dashboard, no longer "staged for review"

    def test_save_to_database_tool_id_removed(self):
        resp = self.client.get("/tools/save_to_database")
        self.assertEqual(resp.status_code, 302)  # merged away, falls back like any unknown tool_id


if __name__ == "__main__":
    unittest.main()
