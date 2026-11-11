import unittest
import server
import test_trip_proximity as fixtures

class CustomerLiveSyncTests(unittest.TestCase):
    def setUp(self):
        self.app=fixtures.TripProximityTests();self.app.setUp();self.addCleanup(self.app.doCleanups)
        with server.connect() as db:
            self.mid=db.execute("INSERT INTO merchants(name,category,area,address,lat,lon) VALUES('Live Store','سوبر ماركت','A','Street',30,31)").lastrowid
            self.pid=db.execute("INSERT INTO products(name,category,price,stock,active,merchant_id,catalog_preview) VALUES('Live Product','سوبر ماركت',20,10,1,?,1)",(self.mid,)).lastrowid
    def get(self,path):
        code,data=self.app.request(self.app.customer,path);self.assertEqual(code,200);return data
    def test_saved_price_and_banner_arrive_as_small_delta(self):
        initial=self.get('/api/catalog?customer_catalog=compact')
        self.assertIn('customer_live_revision',list(initial))
        version=initial['customer_live_revision']
        code,saved=self.app.request(self.app.owner,'/api/product/update',{'id':self.pid,'price':23,'stock':10,'active':True,'merchant_id':self.mid})
        self.assertEqual(code,200,saved)
        delta=self.get('/api/customer-sync?since='+str(version))
        self.assertFalse(delta['reload_state']);self.assertTrue(delta['changed'])
        self.assertEqual(delta['changed_product_ids'],[self.pid])
        self.assertEqual(delta['products'][0]['price'],23)
        self.assertEqual(len(delta['draft_catalog']),1)
        self.assertNotIn('user',delta);self.assertNotIn('orders',delta)
        version=delta['customer_live_revision']
        quiet=self.get('/api/customer-sync?since='+str(version))
        self.assertFalse(quiet['changed']);self.assertNotIn('products',quiet)
        code,_=self.app.request(self.app.owner,'/api/admin/banner-offers',{'revision':0,'offers':[{'title':'New Live Offer','detail':'','visible':True}]})
        self.assertEqual(code,200)
        offer=self.get('/api/customer-sync?since='+str(version))
        self.assertEqual(offer['banner_offers'][0]['title'],'New Live Offer')
        self.assertEqual(offer['changed_product_ids'],[]);self.assertFalse(offer['reload_state'])
        version=offer['customer_live_revision']
        with server.connect() as db:db.execute('UPDATE products SET deleted=1,active=0,catalog_preview=0 WHERE id=?',(self.pid,))
        removed=self.get('/api/customer-sync?since='+str(version))
        self.assertEqual(removed['changed_product_ids'],[self.pid]);self.assertEqual(removed['products'],[]);self.assertEqual(removed['draft_catalog'],[])
    def test_metadata_changes_require_refresh_and_rollback_is_not_published(self):
        initial=self.get('/api/state?customer_catalog=compact');version=initial['customer_live_revision']
        with server.connect() as db:
            db.execute('UPDATE products SET price=999 WHERE id=?',(self.pid,));db.rollback()
        self.assertFalse(self.get('/api/customer-sync?since='+str(version))['changed'])
        with server.connect() as db:db.execute('UPDATE merchants SET active=0 WHERE id=?',(self.mid,))
        self.assertTrue(self.get('/api/customer-sync?since='+str(version))['reload_state'])
        self.assertTrue(self.get('/api/customer-sync?since=999999999')['reload_state'])

if __name__=='__main__':unittest.main()
