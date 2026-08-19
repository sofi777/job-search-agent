"""src/scanner.py run_scan() - re-scores store.jobs in place against store.profile/priority_weights."""
import unittest

from tests import db_setup  # noqa: F401  (side effect: redirects DB_PATH before store import)
from src import scanner, store


class RunScanTests(unittest.TestCase):
    def test_sets_match_on_every_job_and_updates_last_scan(self):
        self.assertGreater(len(store.jobs), 0)  # sample catalog seeded at store import
        before = store.last_scan

        result = scanner.run_scan()

        self.assertIs(result, store.jobs)
        self.assertTrue(all("match" in job and isinstance(job["match"], int) for job in store.jobs))
        self.assertIsNotNone(store.last_scan)
        self.assertNotEqual(store.last_scan, before)

    def test_is_idempotent_shape(self):
        scanner.run_scan()
        first_pass = {j["id"]: j["match"] for j in store.jobs}
        scanner.run_scan()
        second_pass = {j["id"]: j["match"] for j in store.jobs}
        self.assertEqual(first_pass, second_pass)  # same profile/weights -> same scores


if __name__ == "__main__":
    unittest.main()
