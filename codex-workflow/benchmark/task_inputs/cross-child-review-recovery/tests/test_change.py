import unittest
from owner.change import positive_quantity


class QuantityTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(positive_quantity("7"), 7)

    def test_negative_is_rejected(self):
        with self.assertRaises(ValueError):
            positive_quantity("-3")
