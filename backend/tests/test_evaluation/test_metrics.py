"""Offline tests for citation-overlap proxies and R-7 percentiles."""

import unittest

from backend.app.evaluation.metrics import percentile, precision_at_k, recall_at_k


class CitationOverlapTests(unittest.TestCase):
    def test_ranked_prefix_uses_set_intersection_and_fixed_k_denominator(self):
        retrieved = ["c", "a", "a", "b"]
        gold = ["a", "b"]
        for k, precision, recall in (
            (1, 0.0, 0.0), (2, 0.5, 0.5), (3, 1 / 3, 0.5), (4, 0.5, 1.0),
        ):
            with self.subTest(k=k):
                self.assertEqual(precision_at_k(retrieved, gold, k), precision)
                self.assertEqual(recall_at_k(retrieved, gold, k), recall)
        self.assertEqual(retrieved, ["c", "a", "a", "b"])

    def test_empty_inputs_and_short_results(self):
        for retrieved, gold in (([], []), ([], ["a"]), (["a"], [])):
            with self.subTest(retrieved=retrieved, gold=gold):
                self.assertEqual(precision_at_k(retrieved, gold, 5), 0.0)
                self.assertEqual(recall_at_k(retrieved, gold, 5), 0.0)
        self.assertEqual(precision_at_k(["a"], ["a", "b"], 10), 0.1)
        self.assertEqual(recall_at_k(["a"], ["a", "b"], 10), 0.5)

    def test_nonpositive_or_noninteger_k_is_rejected(self):
        for k in (0, -1, 1.5, True):
            for metric in (precision_at_k, recall_at_k):
                with self.subTest(k=k, metric=metric.__name__), self.assertRaises(ValueError):
                    metric(["a"], ["a"], k)


class PercentileTests(unittest.TestCase):
    def test_r7_known_even_and_odd_samples_are_sorted_without_mutation(self):
        values = [30.0, 0.0, 20.0, 10.0]
        for p, expected in ((0, 0.0), (25, 7.5), (50, 15.0), (95, 28.5), (99, 29.7), (100, 30.0)):
            with self.subTest(p=p):
                self.assertAlmostEqual(percentile(values, p), expected, places=12)
        self.assertEqual(percentile(values, 25), 7.5)
        self.assertEqual(percentile(values, 50), 15.0)
        self.assertEqual(percentile([9.0, 1.0, 5.0], 50), 5.0)
        self.assertEqual(percentile([9.0, 1.0, 5.0], 25), 3.0)
        self.assertEqual(values, [30.0, 0.0, 20.0, 10.0])

    def test_empty_singleton_and_constant_samples(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([12.0], 99), 12.0)
        self.assertEqual(percentile([4.0, 4.0, 4.0], 95), 4.0)

    def test_invalid_percentiles_and_nonfinite_samples_are_rejected(self):
        for p in (-1, 101, float("nan"), float("inf")):
            with self.subTest(p=p), self.assertRaises(ValueError):
                percentile([1.0, 2.0], p)
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                percentile([1.0, value], 50)


if __name__ == "__main__":
    unittest.main()
