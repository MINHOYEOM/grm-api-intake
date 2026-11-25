import unittest
from datetime import date, timedelta

from rum_bot_filter import kr_direct_candidate


class KrDirectCandidateTests(unittest.TestCase):
    def setUp(self):
        day = date(2026, 11, 21)
        self.inputs = {
            "snap_date": day,
            "visits": 600,
            "sample_interval": 1,
            "previous_visits": {day - timedelta(days=i): 150 for i in range(1, 8)},
            "countries": {"KR": (560, 1), "US": (40, 1)},
            "referrers": {"(direct)": (580, 1), "example.com": (20, 1)},
        }

    def candidate(self, **changes):
        return kr_direct_candidate(**(self.inputs | changes))

    def test_580_direct_visits_at_exact_share_and_baseline_thresholds(self):
        self.assertEqual(self.candidate(), 540)

    def test_rejects_marginals_whose_overlap_misses_share_by_one(self):
        self.assertIsNone(self.candidate(countries={"KR": (559, 1), "US": (41, 1)}))

    def test_500_overlap_floor_is_inclusive(self):
        history = dict.fromkeys(self.inputs["previous_visits"], 125)
        self.assertEqual(self.candidate(visits=500, previous_visits=history,
                                        countries={"KR": (500, 1)},
                                        referrers={"(direct)": (500, 1)}), 500)
        self.assertIsNone(self.candidate(visits=500, previous_visits=history,
                                         countries={"KR": (499, 1), "US": (1, 1)},
                                         referrers={"(direct)": (500, 1)}))

    def test_keeps_non_kr_direct_and_kr_referred_traffic(self):
        self.assertIsNone(self.candidate(countries={"US": (600, 1)}))
        self.assertIsNone(self.candidate(referrers={"example.com": (600, 1)}))

    def test_keeps_incomplete_or_unreconciled_groups(self):
        self.assertIsNone(self.candidate(countries={"KR": (560, 1)}))
        self.assertIsNone(self.candidate(referrers={"(direct)": (601, 1)}))
        self.assertIsNone(self.candidate(referrers={}))

    def test_keeps_sampled_or_unknown_precision_days(self):
        for interval in (None, 2):
            with self.subTest(interval=interval):
                self.assertIsNone(self.candidate(sample_interval=interval))
                self.assertIsNone(self.candidate(countries={"KR": (560, 1), "US": (40, interval)}))
                self.assertIsNone(self.candidate(referrers={"(direct)": (580, interval),
                                                             "example.com": (20, 1)}))

    def test_requires_seven_days_and_ignores_current_future_and_old_rows(self):
        day = self.inputs["snap_date"]
        history = {day - timedelta(days=i): 150 for i in range(1, 7)}
        history.update({day - timedelta(days=29): 0, day: 0, day + timedelta(days=1): 0})
        self.assertIsNone(self.candidate(previous_visits=history))
        history[day - timedelta(days=28)] = 150
        self.assertEqual(self.candidate(previous_visits=history), 540)

    def test_uses_continuous_median_and_keeps_days_below_four_times_baseline(self):
        history = dict.fromkeys(self.inputs["previous_visits"], 151)
        day = self.inputs["snap_date"]
        for offset in range(1, 5):
            history[day - timedelta(days=offset)] = 150
        self.assertEqual(self.candidate(previous_visits=history), 540)
        history[day - timedelta(days=8)] = 151  # median = 150.5
        self.assertIsNone(self.candidate(previous_visits=history))
