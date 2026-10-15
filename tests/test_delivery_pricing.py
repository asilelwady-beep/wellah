import unittest
from decimal import Decimal

from server import calculate_distance_fee


class DistancePricingTests(unittest.TestCase):
    def test_base_fare_covers_every_distance_through_threshold(self):
        self.assertEqual(calculate_distance_fee(0.001, 3, 25, 5), Decimal("25.00"))
        self.assertEqual(calculate_distance_fee(3, 3, 25, 5), Decimal("25.00"))

    def test_each_additional_kilometer_or_fraction_adds_one_increment(self):
        self.assertEqual(calculate_distance_fee(3.001, 3, 25, 5), Decimal("30.00"))
        self.assertEqual(calculate_distance_fee(4, 3, 25, 5), Decimal("30.00"))
        self.assertEqual(calculate_distance_fee(4.001, 3, 25, 5), Decimal("35.00"))
        self.assertEqual(calculate_distance_fee(6, 3, 25, 5), Decimal("40.00"))

    def test_admin_can_choose_a_different_base_distance(self):
        self.assertEqual(calculate_distance_fee(1.5, 1, 12, 4), Decimal("16.00"))
        self.assertEqual(calculate_distance_fee(2, 1, 12, 4), Decimal("16.00"))


if __name__ == "__main__":
    unittest.main()
