"""src/ai.py - suggest_roles/suggest_home_address (placeholders) + score_job (real logic),
plus _strip_html (pure helper behind extract_job_posting)."""
import unittest

from src import ai

WEIGHTS = {"role_match": 40, "location_fit": 30, "salary_fit": 20, "industry_fit": 10}


def base_profile(**overrides):
    profile = {
        "roles": ["Senior Product Manager"], "remote_ok": True, "home_address": "San Francisco, CA",
        "min_salary": 150000, "industries": [],
    }
    profile.update(overrides)
    return profile


def base_job(**overrides):
    job = {
        "title": "Senior Product Manager", "description": "own the roadmap", "remote": True,
        "location": "Remote", "salary_min": 160000, "salary_max": 200000,
    }
    job.update(overrides)
    return job


class PlaceholderTests(unittest.TestCase):
    def test_suggest_roles_and_home_address_are_deterministic(self):
        self.assertEqual(ai.suggest_roles("anything.pdf"), ai.GENERIC_ROLE_SUGGESTIONS)
        self.assertEqual(ai.suggest_home_address("anything.pdf"), "San Francisco, CA")


class ScoreJobTests(unittest.TestCase):
    def test_perfect_match_scores_100(self):
        self.assertEqual(ai.score_job(base_job(), base_profile(), WEIGHTS), 100)

    def test_role_mismatch_lowers_score(self):
        job = base_job(title="Warehouse Associate", description="lift boxes")
        self.assertLess(ai.score_job(job, base_profile(), WEIGHTS), 100)

    def test_remote_false_falls_back_to_location_text_match(self):
        job = base_job(remote=False, location="San Francisco, CA office")
        profile = base_profile(remote_ok=True)
        self.assertEqual(ai.score_job(job, profile, WEIGHTS), 97)  # text match caps location at 90, not 100

    def test_no_location_match_scores_low_location(self):
        job = base_job(remote=False, location="Berlin, Germany")
        score = ai.score_job(job, base_profile(remote_ok=False), WEIGHTS)
        self.assertLess(score, 100)

    def test_salary_below_minimum_scores_low(self):
        job = base_job(salary_min=50000, salary_max=60000)
        score_low = ai.score_job(job, base_profile(min_salary=150000), WEIGHTS)
        score_high = ai.score_job(base_job(), base_profile(min_salary=150000), WEIGHTS)
        self.assertLess(score_low, score_high)

    def test_industry_filter(self):
        profile = base_profile(industries=["Fintech"])
        job = base_job(description="payments infrastructure for fintech")
        self.assertEqual(ai.score_job(job, profile, WEIGHTS), 100)
        job_no_match = base_job(description="climate tech roadmap")
        self.assertLess(ai.score_job(job_no_match, profile, WEIGHTS), 100)

    def test_zero_weights_does_not_crash(self):
        zero_weights = {"role_match": 0, "location_fit": 0, "salary_fit": 0, "industry_fit": 0}
        score = ai.score_job(base_job(), base_profile(), zero_weights)
        self.assertIsInstance(score, int)

    def test_missing_optional_fields_use_defaults(self):
        job = {"title": "PM", "description": ""}
        score = ai.score_job(job, base_profile(), WEIGHTS)
        self.assertIsInstance(score, int)


class StripHtmlTests(unittest.TestCase):
    def test_removes_tags_scripts_and_styles(self):
        html = "<html><head><style>.a{color:red}</style></head><body><script>alert(1)</script><p>Hello  world</p></body></html>"
        self.assertEqual(ai._strip_html(html), "Hello world")


if __name__ == "__main__":
    unittest.main()
