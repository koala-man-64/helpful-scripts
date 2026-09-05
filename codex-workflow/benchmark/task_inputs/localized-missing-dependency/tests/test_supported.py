import unittest
from src.supported import total


class SupportedPathTests(unittest.TestCase):
    def test_stdlib_path(self):
        self.assertEqual(total("count\n2\n3\n"), 5)
