import unittest
import server
import test_trip_proximity as fixtures

class CustomerCatalogRefreshTests(unittest.TestCase):
    def setUp(self):
        self.app=fixtures.TripProximityTests();self.app.setUp()
        self.addCleanup(self.app.doCleanups)
        with server.connect() as db:
            mid=db.execute("INSERT INTO merchants(name,category,area,address,lat,lon) VALUES('Test','سوبر ماركت','A','Street',30,31)").lastrowid
            db.execute("UPDATE products SET merchant_id=?,active=1,stock=10 WHERE id=(SELECT id FROM products WHERE category='سوبر ماركت' LIMIT 1)",(mid,))

    def get(self,path):
        code,data=self.app.request(self.app.customer,path)
        self.assertEqual(code,200)
        return data

    def test_compact_catalog_preserves_products_and_refreshes_stock(self):
        for route in ['/api/catalog','/api/state']:
            old=self.get(route)
            full=self.get(route+'?customer_catalog=compact')
            self.assertIn('customer_catalog_revision',list(full))
            self.assertEqual(full['products'],old['products'])
            self.assertEqual(full['draft_catalog_ids'],[p['id'] for p in old['draft_catalog']])
            self.assertFalse({p['id'] for p in full['products']} & {p['id'] for p in full['draft_catalog']})
            rev=full['customer_catalog_revision']
            delta=self.get(route+'?customer_catalog=compact&customer_catalog_revision='+rev)
            self.assertTrue(delta['customer_catalog_unchanged'])
            self.assertNotIn('products',delta)
            self.assertNotIn('draft_catalog',delta)
            self.assertIn('banner_offers',delta)
            if route=='/api/state':self.assertEqual(delta['orders'],old['orders'])
            with server.connect() as db:
                pid=old['draft_catalog'][0]['id']
                db.execute('UPDATE products SET stock=stock+1 WHERE id=?',(pid,))
            changed=self.get(route+'?customer_catalog=compact&customer_catalog_revision='+rev)
            self.assertFalse(changed['customer_catalog_unchanged'])
            self.assertNotEqual(changed['customer_catalog_revision'],rev)
            self.assertIn('products',self.get(route))

if __name__=='__main__':unittest.main()
