"""homefield_stats against independent references (exact enumeration, published formulas)."""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import homefield_stats as hs  # noqa: E402


class TestStats(unittest.TestCase):
    def test_e2_pass_at_k_unbiased_estimator(self):
        # n=10, c=3: pass@1 = 0.3; pass@5 = 1 - C(7,5)/C(10,5) = 1 - 21/252
        self.assertAlmostEqual(hs.pass_at_k(10, 3, 1), 0.3)
        self.assertAlmostEqual(hs.pass_at_k(10, 3, 5), 1 - 21 / 252)
        self.assertEqual(hs.pass_at_k(10, 8, 5), 1.0)  # fewer than k failures: always a success
        # it really is unbiased: the mean over all k-subsets of attempts equals the estimator
        from itertools import combinations
        outcomes = [1, 0, 0, 1, 0, 0, 0, 1, 0, 0]
        subsets = list(combinations(outcomes, 4))
        self.assertAlmostEqual(sum(any(s) for s in subsets) / len(subsets), hs.pass_at_k(10, 3, 4))

    def test_e5_mcnemar_exact_reference(self):
        # b=12, c=3: two-sided exact p = 2 * P(X <= 3), X ~ Binomial(15, 0.5) = 0.0352
        self.assertAlmostEqual(hs.mcnemar_exact(12, 3), 0.0352, places=4)
        self.assertEqual(hs.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(hs.mcnemar_exact(5, 5), 1.0)

    def test_permutation_exact_small(self):
        # 4 tasks all +1: only the all-plus and all-minus sign patterns are as extreme -> 2/16
        self.assertAlmostEqual(hs.paired_permutation([1, 1, 1, 1]), 2 / 16)
        self.assertEqual(hs.paired_permutation([0, 0]), 1.0)

    def test_bootstrap_ci_contains_truth(self):
        a, b = [1.0] * 20, [0.0] * 20
        lo, hi = hs.bootstrap_diff_ci(a, b)
        self.assertEqual((lo, hi), (1.0, 1.0))
        rng = random.Random(1)
        a = [rng.random() for _ in range(40)]
        lo, hi = hs.bootstrap_diff_ci(a, a)
        self.assertEqual((lo, hi), (0.0, 0.0))

    def test_holm(self):
        self.assertEqual([round(x, 6) for x in hs.holm([0.01, 0.04, 0.03])], [0.03, 0.06, 0.06])

    def test_wilson_vendored(self):
        self.assertEqual(tuple(round(x, 3) for x in hs.wilson(17, 20)), (0.640, 0.948))


if __name__ == "__main__":
    unittest.main()
