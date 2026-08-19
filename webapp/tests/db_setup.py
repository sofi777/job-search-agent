"""Test DB isolation helper - never let tests touch the real data/app.db.

Importing this module redirects db.DB_PATH to a throwaway temp file immediately, before
`from src import store` (which runs import-time side effects: loads/seeds the demo user
and jobs) can run against the real one. Every test module that needs store must import
this first.

DbTestCase gives db.py-level tests a fresh, empty DB per test.
"""
import tempfile
import unittest
from pathlib import Path

from src import db

SESSION_DB_DIR = tempfile.TemporaryDirectory()
db.DB_PATH = Path(SESSION_DB_DIR.name) / "session.db"
db.init_db()


class DbTestCase(unittest.TestCase):
    """Isolated, empty DB per test - restores db.DB_PATH (back to SESSION_DB) after."""

    def setUp(self):
        self._original_db_path = db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        self._tmpdir.cleanup()
