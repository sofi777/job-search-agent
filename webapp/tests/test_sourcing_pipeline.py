"""Fetch and filter/dedup are separate stages, never auto-chained (store.save_fetch_results
/ store.apply_run_filters) - a component's run() only fetches, and every listing it returns
is staged (status "kept") immediately, visible and addable from that component's own page.
Filtering is a distinct, explicit step against just a run_id, run only from the Filter,
dedupe & save tool page (/tools/filter_dedupe) - never from a component's own page, which
stays filter-agnostic. It flips whatever doesn't survive to status "filtered" with a reason;
kept rows are untouched. See src/filters.py and app.py's component_run / component_run_filter
/ tool_detail.
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

    def test_fetch_stages_every_listing_immediately_kept(self):
        run_id = store.start_run("serpapi", "test")
        listing = {**LISTING, "url": "https://x.com/pipeline-1"}

        # Fetch stage: every listing is already a staged, addable row - no filtering yet.
        store.save_fetch_results(run_id, [listing])
        self.assertEqual(store.get_run(run_id)["status"], "fetched")
        results = store.get_run_results(run_id)
        self.assertEqual([r["url"] for r in results], [listing["url"]])
        self.assertEqual(results[0]["status"], "kept")

        # Filter stage: called separately, with only the run_id - no knowledge of the
        # component or config that produced the fetched data. This listing survives, so
        # it stays "kept" here and is saved straight to the jobs table.
        store.apply_run_filters(run_id)
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "ok")
        self.assertEqual([r["url"] for r in store.get_run_results(run_id)], [listing["url"]])
        self.assertEqual(db.fetch_raw_results(run_id), [])  # display-only copy cleared once filtered
        self.assertIn(listing["url"], {j["url"] for j in store.jobs})

    def test_apply_run_filters_flips_what_the_profile_excludes_to_filtered(self):
        run_id = store.start_run("serpapi", "test")
        url = "https://x.com/pipeline-2"
        store.save_fetch_results(run_id, [{**LISTING, "url": url, "title": "Barista"}])
        store.apply_run_filters(run_id)
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["filtered_count"], 1)

        results = store.get_run_results(run_id)
        self.assertEqual([r["url"] for r in results], [url])  # still there, not removed
        self.assertEqual(results[0]["status"], "filtered")
        self.assertEqual(results[0]["filter_reason"], "role/title")

    def test_apply_run_filters_is_noop_before_fetch_or_after_already_filtered(self):
        run_id = store.start_run("serpapi", "test")
        store.apply_run_filters(run_id)  # nothing fetched yet
        self.assertEqual(store.get_run(run_id)["status"], "running")

        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-3", "title": "Barista"}])
        store.apply_run_filters(run_id)
        results_before = store.get_run_results(run_id)

        store.apply_run_filters(run_id)  # already "ok", not "fetched" - must not re-evaluate
        self.assertEqual(store.get_run_results(run_id), results_before)

    def test_fetch_error_with_no_listings_is_terminal_without_filtering(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [], "boom")
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "error")

        store.apply_run_filters(run_id)  # not "fetched" - must not try to filter
        self.assertEqual(store.get_run(run_id)["status"], "error")

    def test_apply_run_filters_records_a_per_job_reason_for_each_drop(self):
        run_id = store.start_run("serpapi", "test")
        kept_url, dropped_url = "https://x.com/pipeline-kept", "https://x.com/pipeline-dropped"
        store.save_fetch_results(run_id, [
            {**LISTING, "url": kept_url},
            {**LISTING, "url": dropped_url, "title": "Barista"},
        ])
        store.apply_run_filters(run_id)

        results = {r["url"]: r for r in store.get_run_results(run_id)}
        self.assertEqual(results[kept_url]["status"], "kept")
        self.assertEqual(results[dropped_url]["status"], "filtered")
        self.assertEqual(results[dropped_url]["filter_reason"], "role/title")

    def test_apply_run_filters_saves_survivors_to_the_dashboard(self):
        run_id = store.start_run("serpapi", "test")
        url = "https://x.com/pipeline-autosave"
        store.save_fetch_results(run_id, [{**LISTING, "url": url}])
        store.apply_run_filters(run_id)

        self.assertIn(url, {j["url"] for j in store.jobs})
        # Saved automatically - never shows up in the "add anyway" review list.
        self.assertNotIn(url, [r["url"] for r in store.get_staged_results_for_review()])

    def test_fetch_error_with_partial_listings_still_filters_then_reports_error(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-4"}], "partial failure")
        self.assertEqual(store.get_run(run_id)["status"], "fetched")  # still pending, error kept for later

        store.apply_run_filters(run_id)
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "error")  # original fetch error surfaces once filtering finishes
        self.assertEqual(len(store.get_run_results(run_id)), 1)  # the listing was still staged


class ComponentRunRouteTests(unittest.TestCase):
    """component_run() only fetches - it leaves a run in "fetched" (pending review) state,
    but every listing is already staged and shown on the component's own page (no filter
    button there). component_run_filter() is the separate, explicit filter step, only
    triggered from the Filter & dedupe tool."""

    def setUp(self):
        self.client = flask_app.app.test_client()
        self.client.post("/login")
        store.profile["onboarding_complete"] = True
        store.save_profile()

    def test_run_route_fetches_and_stages_immediately(self):
        resp = self.client.post("/components/serpapi/run", data={"mode": "test"})
        self.assertEqual(resp.status_code, 302)
        run_id = int(resp.headers["Location"].rsplit("run=", 1)[1])
        run = store.get_run(run_id)
        self.assertEqual(run["status"], "fetched")
        self.assertTrue(store.get_run_results(run_id))  # already staged, no filter needed to see them

    def test_run_page_shows_every_fetched_listing_no_filter_button(self):
        resp = self.client.post("/components/serpapi/run", data={"mode": "test"})
        run_id = int(resp.headers["Location"].rsplit("run=", 1)[1])

        html = self.client.get(f"/components/serpapi?run={run_id}").get_data(as_text=True)
        for r in store.get_run_results(run_id):
            self.assertIn(r["url"], html)
        self.assertNotIn(f"/components/serpapi/runs/{run_id}/filter", html)

    def test_filter_route_applies_to_a_run_fetched_by_the_run_route(self):
        resp = self.client.post("/components/serpapi/run", data={"mode": "test"})
        run_id = int(resp.headers["Location"].rsplit("run=", 1)[1])

        resp = self.client.post(f"/components/serpapi/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "ok")

    def test_filter_route_applies_to_a_run_fetched_independently(self):
        # Simulate a run fetched by something other than component_run() - just a run_id
        # with staged results already sitting on it.
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [{**LISTING, "url": "https://x.com/pipeline-route"}])

        resp = self.client.post(f"/components/serpapi/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "ok")

    def test_run_page_still_shows_a_listing_after_it_gets_filtered_out(self):
        run_id = store.start_run("serpapi", "test")
        store.save_fetch_results(run_id, [
            {**LISTING, "url": "https://x.com/pipeline-page-kept"},
            {**LISTING, "url": "https://x.com/pipeline-page-dropped", "title": "Barista"},
        ])
        self.client.post(f"/components/serpapi/runs/{run_id}/filter")

        html = self.client.get(f"/components/serpapi?run={run_id}").get_data(as_text=True)
        self.assertIn("https://x.com/pipeline-page-kept", html)
        self.assertIn("https://x.com/pipeline-page-dropped", html)  # still shown - this page ignores filter status

    def test_filter_route_rejects_mismatched_component(self):
        run_id = store.start_run("serpapi", "test")
        resp = self.client.post(f"/components/remoteok/runs/{run_id}/filter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store.get_run(run_id)["status"], "running")  # untouched


class FilterDedupeToolPageTests(unittest.TestCase):
    """The merged Filter, dedupe & save tool page (/tools/filter_dedupe): a Run button per
    pending run log row, a per-job kept/filtered breakdown once a run has been filtered,
    filtered-out results with "Add to dashboard" for manual override, and the already-saved
    log - all in one page (previously split across two tools). Survivors are saved to the
    jobs table automatically by apply_run_filters - they never need the manual button."""

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

    def test_filtered_run_shows_per_job_kept_and_filtered_verdict(self):
        run_id = store.start_run("serpapi", "test")
        store.profile["roles"] = ["Product Manager"]
        store.save_profile()
        kept_url, dropped_url = "https://x.com/tool-page-kept", "https://x.com/tool-page-dropped"
        store.save_fetch_results(run_id, [
            {**LISTING, "url": kept_url},
            {**LISTING, "url": dropped_url, "title": "Barista"},
        ])
        self.client.post(f"/components/serpapi/runs/{run_id}/filter")

        html = self.client.get("/tools/filter_dedupe").get_data(as_text=True)
        self.assertIn(kept_url, html)
        self.assertIn(dropped_url, html)
        self.assertIn("role/title", html)

    def test_filtered_out_results_shown_and_disappear_once_added(self):
        run_id = store.start_run("serpapi", "test")
        store.profile["roles"] = ["Product Manager"]
        store.save_profile()
        url = "https://x.com/tool-page-staged"
        store.save_fetch_results(run_id, [{**LISTING, "url": url, "title": "Barista"}])
        self.client.post(f"/components/serpapi/runs/{run_id}/filter")

        self.assertIn(url, [r["url"] for r in store.get_staged_results_for_review()])

        result_id = store.get_run_results(run_id)[0]["id"]
        self.client.post(f"/components/serpapi/results/{result_id}/add")
        # now on the dashboard, no longer up for manual "add anyway" review
        self.assertNotIn(url, [r["url"] for r in store.get_staged_results_for_review()])
        self.assertIn(url, {j["url"] for j in store.jobs})

    def test_survivor_never_shown_in_filtered_out_list(self):
        run_id = store.start_run("serpapi", "test")
        store.profile["roles"] = ["Product Manager"]
        store.save_profile()
        url = "https://x.com/tool-page-survivor"
        store.save_fetch_results(run_id, [{**LISTING, "url": url}])
        self.client.post(f"/components/serpapi/runs/{run_id}/filter")

        # Saved automatically - nothing left for manual review, but it does show up in the
        # "Saved to dashboard" log further down the same page.
        self.assertNotIn(url, [r["url"] for r in store.get_staged_results_for_review()])
        self.assertIn(url, {j["url"] for j in store.jobs})

    def test_save_to_database_tool_id_removed(self):
        resp = self.client.get("/tools/save_to_database")
        self.assertEqual(resp.status_code, 302)  # merged away, falls back like any unknown tool_id


if __name__ == "__main__":
    unittest.main()
