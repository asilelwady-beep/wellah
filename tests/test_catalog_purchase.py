import unittest
from test_catalog_pricing_20261013 import CatalogPricingTests,server

class CatalogPurchaseTests(unittest.TestCase):
    def setUp(self):
        CatalogPricingTests.setUp(self)
        self.db.execute('UPDATE products SET price=44.5,price_pending=0,stock=100,active=1 WHERE id=1')
    def tearDown(self):CatalogPricingTests.tearDown(self)
    def test_unlinked_catalog_item_can_be_ordered(self):
        p,qty=server.catalog_order_product(self.db,1,2)
        self.assertIsNone(p['merchant_id']);self.assertEqual(qty,2)
    def test_hidden_unpriced_excess_and_fractional_pieces_rejected(self):
        for qty in [101,0,float('nan'),0.5]:
            with self.assertRaises(ValueError):server.catalog_order_product(self.db,1,qty)
        self.db.execute('UPDATE products SET active=0 WHERE id=1')
        with self.assertRaises(ValueError):server.catalog_order_product(self.db,1,1)
    def test_two_kilos_and_stock_basis(self):
        self.db.execute("UPDATE products SET name='تفاح بالوزن',price_basis_weight_kg=.25,stock=4 WHERE id=1")
        self.assertEqual(server.catalog_order_product(self.db,1,.75)[1],.75)
        with self.assertRaises(ValueError):server.catalog_order_product(self.db,1,2)
        self.db.execute('UPDATE products SET stock=100 WHERE id=1')
        self.assertEqual(server.catalog_order_product(self.db,1,2)[1],2)
