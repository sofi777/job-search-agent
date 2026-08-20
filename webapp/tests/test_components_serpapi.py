"""Unit tests for src/components/serpapi.py. No real SerpAPI calls - base.fetch_json is mocked."""
import os
import unittest
import urllib.parse
from unittest.mock import patch

from src.components import serpapi


class DefaultConfigTests(unittest.TestCase):
    def test_seeds_location_from_home_address_not_eligible_countries(self):
        config = serpapi.default_config({
            "roles": ["Senior PM"], "home_address": "Austin, TX", "eligible_countries": ["Canada"],
        })
        self.assertEqual(config["queries"][0]["terms"], ["Senior PM"])
        self.assertEqual(config["queries"][0]["location"], "Austin, TX")

    def test_defaults_to_local_only_work_mode(self):
        config = serpapi.default_config({"home_address": "Austin, TX"})
        self.assertEqual(config["queries"][0]["work_mode"], "local")

    def test_falls_back_to_eligible_countries_without_home_address(self):
        config = serpapi.default_config({"eligible_countries": ["Canada"]})
        self.assertEqual(config["queries"][0]["location"], "Canada")

    def test_falls_back_without_profile_data(self):
        config = serpapi.default_config({})
        self.assertTrue(config["queries"][0]["terms"])
        self.assertTrue(config["queries"][0]["location"])

    def test_includes_followed_companies_filters(self):
        config = serpapi.default_config({})
        self.assertEqual(config["followed_companies_filters"]["date_posted"], "month")
        self.assertEqual(config["followed_companies_filters"]["work_mode"], "any")


class WorkModeTests(unittest.TestCase):
    def test_reads_work_mode_field(self):
        self.assertEqual(serpapi._work_mode({"work_mode": "remote"}), "remote")
        self.assertEqual(serpapi._work_mode({"work_mode": "local"}), "local")

    def test_unknown_work_mode_falls_back_to_any(self):
        self.assertEqual(serpapi._work_mode({"work_mode": "bogus"}), "any")

    def test_missing_work_mode_falls_back_to_any(self):
        self.assertEqual(serpapi._work_mode({}), "any")

    def test_legacy_remote_only_bool_still_honored(self):
        self.assertEqual(serpapi._work_mode({"remote_only": True}), "remote")
        self.assertEqual(serpapi._work_mode({"remote_only": False}), "any")


class BestUrlTests(unittest.TestCase):
    def test_skips_blocked_aggregator_and_picks_next_link(self):
        job = {"apply_options": [{"link": "https://sercanto.com/x"}, {"link": "https://realboard.com/y"}]}
        self.assertEqual(serpapi._best_url(job), "https://realboard.com/y")

    def test_falls_back_to_share_link_when_all_options_blocked(self):
        job = {"apply_options": [{"link": "https://jooble.org/x"}], "share_link": "https://google.com/share"}
        self.assertEqual(serpapi._best_url(job), "https://google.com/share")

    def test_falls_back_to_share_link_when_no_apply_options(self):
        job = {"share_link": "https://google.com/share"}
        self.assertEqual(serpapi._best_url(job), "https://google.com/share")

    def test_empty_string_when_nothing_available(self):
        self.assertEqual(serpapi._best_url({}), "")


class ToListingTests(unittest.TestCase):
    def test_work_from_home_flag_marks_remote(self):
        job = {"title": "PM", "company_name": "Acme", "location": "United States",
               "detected_extensions": {"work_from_home": True}}
        self.assertTrue(serpapi._to_listing(job, "Search")["remote"])

    def test_remote_location_text_marks_remote_without_the_flag(self):
        job = {"title": "PM", "company_name": "Acme", "location": "Remote"}
        self.assertTrue(serpapi._to_listing(job, "Search")["remote"])

    def test_onsite_location_not_marked_remote(self):
        job = {"title": "PM", "company_name": "Acme", "location": "San Francisco, CA"}
        self.assertFalse(serpapi._to_listing(job, "Search")["remote"])


class FetchQueryParamsTests(unittest.TestCase):
    def test_terms_joined_by_match_and_chips_built_from_date_and_employment_type(self):
        q = {"label": "PM", "terms": ["PM", "Product Lead"], "match": "AND", "location": "Canada",
             "date_posted": "week", "employment_types": ["FULLTIME", "CONTRACTOR"], "work_mode": "remote"}
        with patch("src.components.serpapi.base.fetch_json", return_value={"jobs_results": []}) as mock_fetch:
            serpapi._fetch_query(q, "fake-key")
        url = mock_fetch.call_args[0][0]
        self.assertIn(urllib.parse.quote_plus('"PM" AND "Product Lead"'), url)
        self.assertIn("chips=date_posted%3Aweek%2Cemployment_type%3AFULLTIME%2Cemployment_type%3ACONTRACTOR", url)
        self.assertIn("ltype=1", url)
        self.assertIn("location=Canada", url)

    def test_date_posted_any_omits_that_chip_but_keeps_employment_type(self):
        q = {"terms": ["PM"], "match": "OR", "date_posted": "any", "employment_types": ["FULLTIME"]}
        with patch("src.components.serpapi.base.fetch_json", return_value={"jobs_results": []}) as mock_fetch:
            serpapi._fetch_query(q, "fake-key")
        url = mock_fetch.call_args[0][0]
        self.assertIn("chips=employment_type%3AFULLTIME", url)
        self.assertNotIn("date_posted", url)

    def test_no_filters_omits_chips_and_ltype_entirely(self):
        q = {"terms": ["PM"], "match": "OR", "date_posted": "any", "employment_types": []}
        with patch("src.components.serpapi.base.fetch_json", return_value={"jobs_results": []}) as mock_fetch:
            serpapi._fetch_query(q, "fake-key")
        url = mock_fetch.call_args[0][0]
        self.assertNotIn("chips=", url)
        self.assertNotIn("ltype", url)

    def test_local_only_omits_ltype_but_drops_remote_flagged_results(self):
        q = {"terms": ["PM"], "match": "OR", "date_posted": "any", "employment_types": [],
             "location": "Austin, TX", "work_mode": "local"}
        jobs = {"jobs_results": [
            {"title": "Onsite PM", "company_name": "Acme", "location": "Austin, TX"},
            {"title": "Remote PM", "company_name": "Acme", "location": "Remote"},
        ]}
        with patch("src.components.serpapi.base.fetch_json", return_value=jobs) as mock_fetch:
            listings = serpapi._fetch_query(q, "fake-key")
        url = mock_fetch.call_args[0][0]
        self.assertNotIn("ltype", url)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["title"], "Onsite PM")

    def test_any_work_mode_keeps_both_remote_and_onsite_results(self):
        q = {"terms": ["PM"], "match": "OR", "date_posted": "any", "employment_types": [], "work_mode": "any"}
        jobs = {"jobs_results": [
            {"title": "Onsite PM", "company_name": "Acme", "location": "Austin, TX"},
            {"title": "Remote PM", "company_name": "Acme", "location": "Remote"},
        ]}
        with patch("src.components.serpapi.base.fetch_json", return_value=jobs):
            listings = serpapi._fetch_query(q, "fake-key")
        self.assertEqual(len(listings), 2)


class RunTests(unittest.TestCase):
    def test_test_mode_returns_fixture_without_network_call(self):
        with patch("src.components.serpapi.base.fetch_json") as mock_fetch:
            listings, error = serpapi.run({}, test_mode=True)
            mock_fetch.assert_not_called()
        self.assertIsNone(error)
        self.assertEqual(listings, serpapi.FAKE_RESULTS)

    @patch.dict(os.environ, {}, clear=True)
    def test_live_mode_without_key_returns_clean_error_no_crash(self):
        listings, error = serpapi.run({"queries": []}, test_mode=False)
        self.assertEqual(listings, [])
        self.assertIn("SERP_API not set", error)

    @patch.dict(os.environ, {"SERP_API": "fake-key"})
    def test_live_mode_runs_each_query_plus_followed_companies_batch_with_its_own_filters(self):
        config = {
            "queries": [{"label": "Primary", "terms": ["PM"], "match": "OR", "location": "Canada",
                         "date_posted": "week", "employment_types": [], "work_mode": "any"}],
            "use_followed_companies": True,
            "followed_companies": ["Acme"],
            "followed_companies_filters": {"location": "Germany", "date_posted": "today",
                                            "employment_types": ["INTERN"], "work_mode": "remote"},
        }
        job = {"title": "PM", "company_name": "Acme", "location": "Remote", "share_link": "https://x/1"}
        with patch("src.components.serpapi.base.fetch_json", return_value={"jobs_results": [job]}) as mock_fetch:
            listings, error = serpapi.run(config, test_mode=False)
        self.assertIsNone(error)
        self.assertEqual(mock_fetch.call_count, 2)  # one query + one followed-companies batch
        self.assertEqual(len(listings), 2)
        batch_url = mock_fetch.call_args_list[1][0][0]
        self.assertIn("location=Germany", batch_url)
        self.assertIn("chips=date_posted%3Atoday%2Cemployment_type%3AINTERN", batch_url)
        self.assertIn("ltype=1", batch_url)

    @patch.dict(os.environ, {"SERP_API": "fake-key"})
    def test_one_bad_query_does_not_block_the_others(self):
        config = {
            "queries": [
                {"label": "Bad", "terms": ["x"], "match": "OR", "date_posted": "week"},
                {"label": "Good", "terms": ["y"], "match": "OR", "date_posted": "week"},
            ],
            "use_followed_companies": False,
        }
        job = {"title": "PM", "company_name": "Acme", "share_link": "https://x/1"}
        with patch("src.components.serpapi.base.fetch_json",
                   side_effect=[RuntimeError("boom"), {"jobs_results": [job]}]):
            listings, error = serpapi.run(config, test_mode=False)
        self.assertEqual(len(listings), 1)
        self.assertIn("Bad: boom", error)


if __name__ == "__main__":
    unittest.main()
