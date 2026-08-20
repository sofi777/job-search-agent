"""Unit tests for src/components/base.py. No network calls - urlopen is mocked."""
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.components import base


class FetchJsonTests(unittest.TestCase):
    @patch("src.components.base.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"a": 1}'
        self.assertEqual(base.fetch_json("http://x"), {"a": 1})

    @patch("src.components.base.urllib.request.urlopen",
           side_effect=HTTPError("http://x", 404, "Not Found", {}, None))
    def test_http_error_becomes_runtime_error(self, _):
        with self.assertRaises(RuntimeError):
            base.fetch_json("http://x")

    @patch("src.components.base.urllib.request.urlopen", side_effect=URLError("no route"))
    def test_url_error_becomes_runtime_error(self, _):
        with self.assertRaises(RuntimeError):
            base.fetch_json("http://x")

    @patch("src.components.base.urllib.request.urlopen")
    def test_bad_json_becomes_runtime_error(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b"not json"
        with self.assertRaises(RuntimeError):
            base.fetch_json("http://x")


class KeywordMatchTests(unittest.TestCase):
    def test_empty_keywords_matches_everything(self):
        self.assertTrue(base.keyword_match("anything", []))

    def test_case_insensitive_match(self):
        self.assertTrue(base.keyword_match("Senior Product Manager", ["senior"]))

    def test_no_match(self):
        self.assertFalse(base.keyword_match("Engineer", ["product manager"]))


class LocationMatchTests(unittest.TestCase):
    def test_empty_allowed_matches_everything(self):
        self.assertTrue(base.location_match("Anywhere", []))

    def test_match(self):
        self.assertTrue(base.location_match("Vancouver, BC", ["Canada", "Vancouver"]))

    def test_no_match(self):
        self.assertFalse(base.location_match("Berlin", ["Canada"]))

    def test_empty_location_string_with_filter_set(self):
        self.assertFalse(base.location_match("", ["Canada"]))


class LooksRemoteTests(unittest.TestCase):
    def test_remote_in_location_is_detected(self):
        self.assertTrue(base.looks_remote("Remote"))
        self.assertTrue(base.looks_remote("Remote - US"))
        self.assertTrue(base.looks_remote("remote, Canada"))

    def test_onsite_location_not_detected(self):
        self.assertFalse(base.looks_remote("San Francisco, CA"))

    def test_blank_location_not_detected(self):
        self.assertFalse(base.looks_remote(""))
        self.assertFalse(base.looks_remote(None))


class NormalizeListingTests(unittest.TestCase):
    def test_defaults(self):
        listing = base.normalize_listing(title="T", company="C", source="S", url="U")
        self.assertEqual(listing["location"], "Not specified")
        self.assertFalse(listing["remote"])
        self.assertEqual(listing["salary_min"], 0)

    def test_description_truncated_to_2000_chars(self):
        listing = base.normalize_listing(title="T", company="C", source="S", url="U", description="x" * 3000)
        self.assertEqual(len(listing["description"]), 2000)

    def test_none_description_does_not_crash(self):
        listing = base.normalize_listing(title="T", company="C", source="S", url="U", description=None)
        self.assertEqual(listing["description"], "")


if __name__ == "__main__":
    unittest.main()
