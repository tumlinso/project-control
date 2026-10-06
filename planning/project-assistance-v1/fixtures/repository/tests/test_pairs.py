"""Existing baseline tests in the synthetic repository."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo.pairs import pair_sum


class PairSumBaseline(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(pair_sum([]), 0)

    def test_one_pair(self):
        self.assertEqual(pair_sum([2, 3]), 5)

    def test_two_pairs(self):
        self.assertEqual(pair_sum([2, 3, 5, 7]), 17)

    def test_negative_pair(self):
        self.assertEqual(pair_sum([-2, 2]), 0)


if __name__ == '__main__':
    unittest.main()
