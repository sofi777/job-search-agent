"""Unit tests for src/components/remoteok.py. No network calls - base.fetch_json is mocked."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.components import remoteok


class DefaultConfigTests(unittest.TestCase):
    def test_seeds_keywords_from_profile_roles(self):
        config = remoteok.default_config({"roles": ["Group PM"]})
        self.assertIn("Group PM", config["keywords"])

    def test_falls_back_without_roles(self):
        config = remoteok.default_config({})
        self.assertTrue(config["keywords"])


class WithinDaysTests(unittest.TestCase):
    def test_no_filter_when_days_falsy(self):
        self.assertTrue(remoteok._within_days("2020-01-01T00:00:00+00:00", None))
        self.assertTrue(remoteok._within_days("2020-01-01T00:00:00+00:00", 0))

    def test_recent_date_within_range(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(remoteok._within_days(recent, 7))

    def test_old_date_outside_range(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.assertFalse(remoteok._within_days(old, 7))

    def test_unparseable_date_fails_open(self):
        self.assertTrue(remoteok._within_days("not-a-date", 7))

    def test_none_date_fails_open(self):
        self.assertTrue(remoteok._within_days(None, 7))


class RunTests(unittest.TestCase):
    def test_test_mode_returns_fixture_without_network_call(self):
        with patch("src.components.remoteok.base.fetch_json") as mock_fetch:
            listings, error = remoteok.run({}, test_mode=True)
            mock_fetch.assert_not_called()
        self.assertIsNone(error)
        self.assertEqual(listings, remoteok.FAKE_RESULTS)

    def test_live_mode_filters_by_keyword_seniority_and_skips_non_dict_entries(self):
        raw = [
            "legal notice string",  # RemoteOK's real API includes a non-dict first item
            {"position": "Senior Product Manager", "company": "Acme", "tags": [],
             "date": "2026-08-18T00:00:00+00:00", "url": "https://x/1", "id": "1"},
            {"position": "Junior Product Manager", "company": "Acme", "tags": [],
             "date": "2026-08-18T00:00:00+00:00", "id": "2"},
            {"position": "Engineer", "company": "Acme", "tags": [], "date": "2026-08-18T00:00:00+00:00", "id": "3"},
        ]
        config = {"keywords": ["product manager"], "seniority_keywords": ["senior"], "posted_within_days": None}
        with patch("src.components.remoteok.base.fetch_json", return_value=raw):
            listings, error = remoteok.run(config, test_mode=False)
        self.assertIsNone(error)
        self.assertEqual([listing["title"] for listing in listings], ["Senior Product Manager"])

    def test_live_mode_fetch_failure_returns_clean_error_not_a_crash(self):
        with patch("src.components.remoteok.base.fetch_json", side_effect=RuntimeError("boom")):
            listings, error = remoteok.run({}, test_mode=False)
        self.assertEqual(listings, [])
        self.assertEqual(error, "boom")

    def test_missing_url_falls_back_to_constructed_link(self):
        raw = [{"position": "Senior Product Manager", "company": "Acme", "tags": [], "date": "", "id": "42"}]
        config = {"keywords": [], "seniority_keywords": [], "posted_within_days": None}
        with patch("src.components.remoteok.base.fetch_json", return_value=raw):
            listings, _ = remoteok.run(config, test_mode=False)
        self.assertEqual(listings[0]["url"], "https://remoteok.com/l/42")


if __name__ == "__main__":
    unittest.main()
