import unittest
from src.route import ROUTES


class RouteTests(unittest.TestCase):
    def test_readiness_route_is_present(self):
        self.assertEqual(ROUTES.get("ready"), "/ready")
