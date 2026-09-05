import unittest
from src.allocation import allocate


class AllocationTests(unittest.TestCase):
    def test_remainder_preserves_total_in_stable_order(self):
        self.assertEqual(allocate(11, [1, 1, 1]), [4, 4, 3])

    def test_exact_allocation(self):
        self.assertEqual(allocate(10, [1, 1]), [5, 5])
