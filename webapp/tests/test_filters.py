"""Unit tests for src/filters.py. Pure functions, no mocking needed."""
import unittest

from src import filters
from src.components import base

PROFILE = {
    "roles": ["Product Manager"],
    "remote_ok": True,
    "remote_countries": ["United States"],
    "eligible_countries": ["United States"],
    "home_address": "",
    "min_salary": 150000,
}


def listing(**overrides):
    fields = {
        "title": "Senior Product Manager", "company": "Acme", "source": "Test",
        "url": "https://example.com/1", "location": "United States", "remote": False,
        "salary_min": 0, "salary_max": 0,
    }
    fields.update(overrides)
    return base.normalize_listing(**fields)


class DedupeTests(unittest.TestCase):
    def test_drops_repeated_url_keeps_first(self):
        a, b = listing(url="https://x.com/1"), listing(url="https://x.com/1", title="Other")
        kept, dropped = filters.dedupe([a, b])
        self.assertEqual(kept, [a])
        self.assertEqual(dropped, [(b, "duplicate in this run")])

    def test_distinct_urls_all_kept(self):
        a, b = listing(url="https://x.com/1"), listing(url="https://x.com/2")
        kept, dropped = filters.dedupe([a, b])
        self.assertEqual(kept, [a, b])
        self.assertEqual(dropped, [])

    def test_blank_urls_never_treated_as_duplicates_of_each_other(self):
        a, b = listing(url=""), listing(url="")
        kept, dropped = filters.dedupe([a, b])
        self.assertEqual(kept, [a, b])
        self.assertEqual(dropped, [])


class HardFilterRoleTests(unittest.TestCase):
    def test_title_matching_a_role_kept(self):
        kept, dropped = filters.apply_hard_filters([listing(title="Product Manager")], PROFILE)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_title_not_matching_any_role_dropped(self):
        kept, dropped = filters.apply_hard_filters([listing(title="Software Engineer")], PROFILE)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "role/title")

    def test_empty_roles_list_matches_everything(self):
        profile = {**PROFILE, "roles": []}
        kept, _ = filters.apply_hard_filters([listing(title="Software Engineer")], profile)
        self.assertEqual(len(kept), 1)


class HardFilterLocationTests(unittest.TestCase):
    def test_remote_job_dropped_when_remote_not_wanted(self):
        profile = {**PROFILE, "remote_ok": False}
        kept, dropped = filters.apply_hard_filters([listing(remote=True, location="Remote")], profile)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "remote not wanted")

    def test_remote_job_dropped_when_location_not_in_remote_countries(self):
        kept, dropped = filters.apply_hard_filters(
            [listing(remote=True, location="Germany")], PROFILE)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "not eligible to work remotely from this location")

    def test_remote_job_kept_when_remote_countries_unrestricted(self):
        profile = {**PROFILE, "remote_countries": []}
        kept, _ = filters.apply_hard_filters([listing(remote=True, location="Anywhere")], profile)
        self.assertEqual(len(kept), 1)

    def test_remote_job_kept_when_location_is_just_a_generic_remote_label(self):
        # "Remote" alone states no country - common, not bad data, so it's not held
        # against remote_countries the way a blank onsite location is.
        kept, _ = filters.apply_hard_filters([listing(remote=True, location="Remote")], PROFILE)
        self.assertEqual(len(kept), 1)

    def test_remote_job_dropped_when_location_names_a_different_specific_country(self):
        kept, dropped = filters.apply_hard_filters(
            [listing(remote=True, location="Remote - Germany only")], PROFILE)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "not eligible to work remotely from this location")

    def test_onsite_job_kept_when_location_eligible(self):
        kept, _ = filters.apply_hard_filters([listing(remote=False, location="United States")], PROFILE)
        self.assertEqual(len(kept), 1)

    def test_onsite_job_dropped_when_location_not_eligible(self):
        kept, dropped = filters.apply_hard_filters([listing(remote=False, location="France")], PROFILE)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "not eligible to work in this location")

    def test_onsite_job_kept_when_in_home_city(self):
        profile = {**PROFILE, "home_address": "Austin, TX"}
        kept, _ = filters.apply_hard_filters(
            [listing(remote=False, location="Austin, TX, United States")], profile)
        self.assertEqual(len(kept), 1)

    def test_onsite_job_dropped_when_outside_home_city(self):
        profile = {**PROFILE, "home_address": "Austin, TX"}
        kept, dropped = filters.apply_hard_filters(
            [listing(remote=False, location="Chicago, IL, United States")], profile)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "outside commute range")

    def test_onsite_job_kept_when_home_address_unset(self):
        # No home city on file - nothing to gate the commute check on, stays unrestricted.
        profile = {**PROFILE, "home_address": ""}
        kept, _ = filters.apply_hard_filters(
            [listing(remote=False, location="Chicago, IL, United States")], profile)
        self.assertEqual(len(kept), 1)

    def test_remote_job_ignores_home_city(self):
        # Commute range only applies to onsite roles - remote is gated by remote_countries only.
        profile = {**PROFILE, "home_address": "Austin, TX"}
        kept, _ = filters.apply_hard_filters(
            [listing(remote=True, location="United States")], profile)
        self.assertEqual(len(kept), 1)

    def test_onsite_job_kept_when_listing_city_is_bare(self):
        # home_address carries extra words the bare listing location doesn't - "Vancouver" alone
        # must still match "Vancouver, Canada", even though eligible_countries (still just
        # "United States" here) doesn't literally appear in that bare listing text either - a
        # home-city match is itself sufficient proof of eligibility.
        profile = {**PROFILE, "home_address": "Vancouver, Canada"}
        kept, _ = filters.apply_hard_filters([listing(remote=False, location="Vancouver")], profile)
        self.assertEqual(len(kept), 1)

    def test_onsite_job_kept_when_home_address_is_the_wordier_side(self):
        # Reverse of the above - the listing's short city name is the one buried in a wordier
        # home_address, not the other way around.
        profile = {**PROFILE, "home_address": "Greater Vancouver Area, Canada"}
        kept, _ = filters.apply_hard_filters([listing(remote=False, location="Vancouver, BC")], profile)
        self.assertEqual(len(kept), 1)


class HardFilterSalaryTests(unittest.TestCase):
    def test_dropped_when_known_max_below_floor(self):
        kept, dropped = filters.apply_hard_filters([listing(salary_max=100000)], PROFILE)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0][1], "below minimum salary")

    def test_kept_when_max_meets_floor(self):
        kept, _ = filters.apply_hard_filters([listing(salary_max=160000)], PROFILE)
        self.assertEqual(len(kept), 1)

    def test_kept_when_salary_unknown(self):
        kept, _ = filters.apply_hard_filters([listing(salary_min=0, salary_max=0)], PROFILE)
        self.assertEqual(len(kept), 1)

    def test_no_floor_set_never_filters_on_salary(self):
        profile = {**PROFILE, "min_salary": 0}
        kept, _ = filters.apply_hard_filters([listing(salary_max=1)], profile)
        self.assertEqual(len(kept), 1)


class DescribeActiveFiltersTests(unittest.TestCase):
    def test_reflects_current_profile_values(self):
        profile = {**PROFILE, "home_address": "Austin, TX"}
        rows = {r["label"]: r["value"] for r in filters.describe_active_filters(profile)}
        self.assertEqual(rows["Roles / title"], "Product Manager")
        self.assertEqual(rows["Remote OK"], "Yes")
        self.assertEqual(rows["Commute city (onsite)"], "Austin")
        self.assertEqual(rows["Minimum salary"], "USD 150,000")

    def test_empty_lists_and_no_floor_show_as_unrestricted(self):
        profile = {
            "roles": [], "remote_ok": False, "remote_countries": [], "eligible_countries": [],
            "home_address": "", "min_salary": 0,
        }
        rows = {r["label"]: r["value"] for r in filters.describe_active_filters(profile)}
        self.assertEqual(rows["Roles / title"], "Any (no filter)")
        self.assertEqual(rows["Remote OK"], "No")
        self.assertEqual(rows["Commute city (onsite)"], "Any (no filter)")
        self.assertEqual(rows["Minimum salary"], "Any (no floor)")


class SummarizeDropsTests(unittest.TestCase):
    def test_counts_by_reason(self):
        dropped = [(listing(), "role/title"), (listing(), "role/title"), (listing(), "below minimum salary")]
        self.assertEqual(filters.summarize_drops(dropped), {"role/title": 2, "below minimum salary": 1})

    def test_empty_list(self):
        self.assertEqual(filters.summarize_drops([]), {})


class FilterAndDedupeTests(unittest.TestCase):
    def test_known_url_dropped_as_already_on_list(self):
        job = listing(url="https://x.com/1")
        kept, dropped = filters.filter_and_dedupe([job], PROFILE, known_urls={"https://x.com/1"})
        self.assertEqual(kept, [])
        self.assertEqual(dropped, [(job, "already on your list")])

    def test_full_pipeline_dedupes_then_filters(self):
        good = listing(url="https://x.com/1", title="Product Manager")
        dupe = listing(url="https://x.com/1", title="Product Manager")
        bad_role = listing(url="https://x.com/2", title="Software Engineer")
        kept, dropped = filters.filter_and_dedupe([good, dupe, bad_role], PROFILE)
        self.assertEqual(kept, [good])
        reasons = filters.summarize_drops(dropped)
        self.assertEqual(reasons, {"duplicate in this run": 1, "role/title": 1})

    def test_no_known_urls_still_works(self):
        kept, dropped = filters.filter_and_dedupe([listing(title="Product Manager")], PROFILE)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
