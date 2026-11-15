import unittest

import numpy as np
import pandas as pd

from scoreboard import BASELINE, MODEL, build_scores, comparisons, crps_samples, score


def brute_crps(x: np.ndarray, y: float) -> float:
    return np.abs(x - y).mean() - np.abs(x[:, None] - x[None, :]).mean() / 2


def samples_frame(model_values: dict[str, list[float]], ref: str, week: str) -> pd.DataFrame:
    rows = [{"model": m, "reference_date": pd.Timestamp(ref), "target_end_date": pd.Timestamp(week),
             "location": "CD", "output_type_id": i + 1, "value": v}
            for m, values in model_values.items() for i, v in enumerate(values)]
    return pd.DataFrame(rows).pivot_table(index=["model", "reference_date", "target_end_date", "location"],
                                          columns="output_type_id", values="value")


class CrpsTest(unittest.TestCase):
    def test_should_match_pairwise_formula_when_samples_are_random(self):
        rng = np.random.default_rng(1)
        x, y = rng.gamma(2, 10, size=(5, 100)), rng.gamma(2, 10, size=5)
        expected = [brute_crps(x[i], y[i]) for i in range(5)]
        np.testing.assert_allclose(crps_samples(x, y), expected)

    def test_should_equal_absolute_error_when_forecast_is_a_point(self):
        np.testing.assert_allclose(crps_samples(np.full((1, 100), 7.0), np.array([3.0])), [4.0])


class ScoreTest(unittest.TestCase):
    truth = pd.DataFrame({"target_end_date": [pd.Timestamp("2026-10-24")], "location": ["CD"], "observation": [99]})

    def test_should_favour_the_closer_forecast_when_scored_on_log_scale(self):
        s = samples_frame({MODEL: [99.0] * 4, BASELINE: [9.0] * 4}, "2026-11-07", "2026-10-24")
        crps = score(s, self.truth, pd.Timestamp("2026-11-08"))
        row = comparisons(crps)[0]
        self.assertEqual((row["other"], row["cells"]), (BASELINE, 1))
        self.assertEqual(row["ratio"], 0.0)  # ours is perfect
        self.assertIsNone(row["geo"])  # respicast's geometric mean can't use a zero score
        s = samples_frame({MODEL: [90.0] * 4, BASELINE: [9.0] * 4}, "2026-11-07", "2026-10-24")
        r = comparisons(score(s, self.truth, pd.Timestamp("2026-11-08")))[0]["ratio"]
        self.assertAlmostEqual(r, np.log(100 / 91) / np.log(100 / 10))

    def test_should_report_pending_when_week_is_not_mature(self):
        s = samples_frame({MODEL: [90.0] * 4, BASELINE: [9.0] * 4}, "2026-11-07", "2026-10-24")
        crps = score(s, self.truth, pd.Timestamp("2026-10-31"))  # week ended 7 days before the release
        scores = build_scores(comparisons(crps), "2026-11-15")
        self.assertEqual(scores["headline"], "Live scores pending: no truth yet for our rounds")
        self.assertEqual(scores["rows"], [])

    def test_should_cap_card_at_six_rows_and_count_peers_when_many_models(self):
        rows = [{"other": BASELINE, "ratio": 0.5, "cells": 10, "rounds": 1}] + [
            {"other": f"m{i}", "ratio": 0.8 + i / 10, "cells": 100, "rounds": 1} for i in range(8)]
        scores = build_scores(rows, "2026-11-15")
        self.assertEqual(len(scores["rows"]), 6)
        self.assertEqual(scores["headline"], "Better than 2 of 8 hub models; 50% less error than the baseline")


if __name__ == "__main__":
    unittest.main()
