"""Unit tests for src/components/ats.py. No real network calls - base.fetch_json is mocked."""
import unittest
from unittest.mock import patch

from src.components import ats


class DefaultConfigTests(unittest.TestCase):
    def test_seeds_companies_from_followed_companies_with_blank_platform_slug(self):
        config = ats.default_config({"followed_companies": ["Acme"], "eligible_countries": ["Canada"]})
        self.assertEqual(config["companies"], [{"name": "Acme", "platform": "", "slug": ""}])
        self.assertEqual(config["location_filter"], ["Canada"])


class MatchesTests(unittest.TestCase):
    def test_keyword_and_seniority_both_required(self):
        self.assertTrue(ats._matches("Senior Product Manager", ["product manager"], ["senior"]))
        self.assertFalse(ats._matches("Product Manager", ["product manager"], ["senior"]))
        self.assertFalse(ats._matches("Senior Engineer", ["product manager"], ["senior"]))

    def test_no_seniority_filter_means_keyword_alone_is_enough(self):
        self.assertTrue(ats._matches("Product Manager", ["product manager"], []))


class RunSkipAndErrorTests(unittest.TestCase):
    def test_company_missing_platform_or_slug_is_skipped_not_errored(self):
        config = {"companies": [{"name": "Acme", "platform": "", "slug": ""}],
                  "keywords": [], "seniority_keywords": [], "location_filter": []}
        listings, error = ats.run(config, test_mode=False)
        self.assertEqual(listings, [])
        self.assertIsNone(error)

    def test_unknown_platform_is_a_collected_error_not_a_crash(self):
        config = {"companies": [{"name": "Acme", "platform": "bamboohr", "slug": "acme"}],
                  "keywords": [], "seniority_keywords": [], "location_filter": []}
        listings, error = ats.run(config, test_mode=False)
        self.assertEqual(listings, [])
        self.assertIn("unknown platform", error)

    def test_one_failing_company_does_not_block_the_others(self):
        config = {
            "companies": [
                {"name": "Bad Co", "platform": "lever", "slug": "badco"},
                {"name": "Good Co", "platform": "lever", "slug": "goodco"},
            ],
            "keywords": [], "seniority_keywords": [], "location_filter": [],
        }
        with patch("src.components.ats.base.fetch_json", side_effect=[RuntimeError("404"), []]):
            listings, error = ats.run(config, test_mode=False)
        self.assertEqual(listings, [])
        self.assertIn("Bad Co: 404", error)

    def test_test_mode_returns_fixture_without_network_call(self):
        with patch("src.components.ats.base.fetch_json") as mock_fetch:
            listings, error = ats.run({}, test_mode=True)
            mock_fetch.assert_not_called()
        self.assertEqual(listings, ats.FAKE_RESULTS)
        self.assertIsNone(error)


class FetcherTests(unittest.TestCase):
    def test_lever_maps_fields_and_filters_by_location(self):
        jobs = [
            {"text": "Senior Product Manager", "categories": {"location": "Toronto"},
             "hostedUrl": "https://x/1", "createdAt": 1700000000000, "descriptionPlain": "d"},
            {"text": "Senior Product Manager", "categories": {"location": "Berlin"}, "hostedUrl": "https://x/2"},
        ]
        with patch("src.components.ats.base.fetch_json", return_value=jobs):
            listings = ats._fetch_lever("slug", "Acme", ["product manager"], ["senior"], ["Canada", "Toronto"])
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["company"], "Acme")
        self.assertEqual(listings[0]["url"], "https://x/1")
        self.assertTrue(listings[0]["posted"])
        self.assertFalse(listings[0]["remote"])

    def test_lever_detects_remote_from_location_text(self):
        jobs = [{"text": "Senior Product Manager", "categories": {"location": "Remote - US"}, "hostedUrl": "https://x/1"}]
        with patch("src.components.ats.base.fetch_json", return_value=jobs):
            listings = ats._fetch_lever("slug", "Acme", [], [], [])
        self.assertTrue(listings[0]["remote"])

    def test_greenhouse_falls_back_to_second_endpoint(self):
        jobs = {"jobs": [{"title": "Senior Product Manager", "location": {"name": "Remote"},
                           "absolute_url": "https://x/1", "updated_at": "2026-08-01T00:00:00Z", "content": "d"}]}
        with patch("src.components.ats.base.fetch_json", side_effect=[RuntimeError("404"), jobs]):
            listings = ats._fetch_greenhouse("slug", "Acme", ["product manager"], [], [])
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["posted"], "2026-08-01")
        self.assertTrue(listings[0]["remote"])

    def test_greenhouse_raises_when_both_endpoints_fail(self):
        with patch("src.components.ats.base.fetch_json", side_effect=[RuntimeError("404"), RuntimeError("404")]):
            with self.assertRaises(RuntimeError):
                ats._fetch_greenhouse("slug", "Acme", [], [], [])

    def test_ashby_handles_dict_and_string_location(self):
        jobs = {"jobPostings": [
            {"title": "Senior Product Manager", "location": {"name": "Remote"}, "jobUrl": "https://x/1"},
            {"title": "Senior Product Manager", "location": "Remote Canada", "jobUrl": "https://x/2"},
        ]}
        with patch("src.components.ats.base.fetch_json", return_value=jobs):
            listings = ats._fetch_ashby("slug", "Acme", ["product manager"], ["senior"], [])
        self.assertEqual(len(listings), 2)

    def test_workable_maps_remote_flag_and_shortcode_url(self):
        jobs = {"jobs": [{"title": "Senior Product Manager", "location": {"city": ""},
                           "remote": True, "shortcode": "abc123"}]}
        with patch("src.components.ats.base.fetch_json", return_value=jobs):
            listings = ats._fetch_workable("slug", "Acme", ["product manager"], [], [])
        self.assertEqual(listings[0]["location"], "Remote")
        self.assertEqual(listings[0]["url"], "https://apply.workable.com/slug/j/abc123")


if __name__ == "__main__":
    unittest.main()
