import json,unittest
from unittest.mock import patch
import server
import test_trip_proximity as fixtures

class OfferPackTests(unittest.TestCase):
    def setUp(self):
        self.app=fixtures.TripProximityTests();self.app.setUp();self.addCleanup(self.app.doCleanups)
        with server.connect() as db:
            db.execute("DELETE FROM catalog_imports WHERE key='owner-banner-packs-20261112-v1'")
            db.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',[('delivery_base_km','3'),('delivery_base_fee','30'),('delivery_extra_rate','5')])
            self.mid=db.execute("INSERT INTO merchants(name,category,area,address,lat,lon) VALUES('مخزن ولعه','سوبر ماركت',?,'Test street',29.81,31.21)",(server.AREAS[0],)).lastrowid
            self.beauty=db.execute("INSERT INTO merchants(name,category,area,address,lat,lon) VALUES('مخزن ولعه','مستحضرات تجميل',?,'Test street',29.81,31.21)",(server.AREAS[0],)).lastrowid
            offer={'title':'٢ كيلو سكر وباكو شاي بـ٩٩ جنيه','detail':'عرض تجريبي','visible':True,'merchant_id':self.mid,'image':'/banner-image/dac9f8a04af9fdd2bd30649c593ef219a92b2fdc43674ec4a166ce459b765cc5.jpg'}
            care={**offer,'title':'عرض الصيدلية بـ٥٩ جنيه','merchant_id':self.beauty,'image':'/banner-image/6211df7315673e2c58f527dee73f6862f4505a61cec73ba6aee9cfce74bb9502.jpg'}
            tissues={**offer,'title':'٣ عبوات مناديل بـ٢٥ جنيه','image':'/banner-image/bb7a6b2a96c6eff1bd4a521add9b1f8b5d488f01ab6c5a8cfb3761f622038a6c.jpg'}
            db.execute('UPDATE banner_settings SET offers=? WHERE id=1',(json.dumps([offer,care,tissues]),))

    def test_approved_pack_purchases_keep_store_price_and_stock(self):
        with server.connect() as db:
            server.create_approved_offer_packs(db)
            for offer,price,mid in zip(server.published_banner_offers(db),[99,59,25],[self.mid,self.beauty,self.mid]):
                product=db.execute('SELECT * FROM products WHERE id=?',(offer['purchase']['product_id'],)).fetchone()
                self.assertEqual((product['price'],product['stock'],product['merchant_id']),(price,1,mid))
            offer=server.published_banner_offers(db)[0]
            pid=offer['purchase']['product_id']
            self.assertTrue(offer['purchase']['available'])
            p=db.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
            self.assertEqual((p['price'],p['stock'],p['merchant_id']),(99,1,self.mid))
            self.assertFalse(server.sold_by_weight(p))
            before=db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
            server.create_approved_offer_packs(db)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM products').fetchone()[0],before)
        payload={'kind':'products','merchant_id':self.mid,'items':[{'product_id':pid,'quantity':1}],
                 'area':server.AREAS[0],'payment':'cash','address':'Test customer address',
                 'latitude':29.8,'longitude':31.2,'client_request_id':'offer-pack-test'}
        with patch.object(server,'fetch_road_km',return_value=3):
            with server.connect() as db:payload['delivery_quote']=server.quote_delivery(db,self.app.customer,payload)['quote_token']
            self.assertEqual(self.app.request(self.app.customer,'/api/order',{**payload,'merchant_id':self.beauty})[0],400)
            code,result=self.app.request(self.app.customer,'/api/order',payload)
            self.assertEqual(code,200,result)
            with server.connect() as db:
                order=db.execute('SELECT * FROM orders WHERE id=?',(result['id'],)).fetchone()
                item=db.execute('SELECT * FROM order_items WHERE order_id=?',(result['id'],)).fetchone()
                self.assertEqual(order['merchant_id'],self.mid);self.assertFalse(order['shop_anywhere'])
                self.assertEqual(item['product_id'],pid);self.assertEqual(item['unit_price'],99);self.assertEqual(item['quantity'],1)
                self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],0)
                self.assertFalse(server.published_banner_offers(db)[0]['purchase']['available'])
                self.assertEqual(server.banner_settings(db)['offers'][0]['product_id'],pid)
                server.create_approved_offer_packs(db)
                self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(pid,)).fetchone()[0],0)
            code,dashboard=self.app.request(self.app.owner,'/api/state?admin_catalog=summary')
            self.assertEqual(code,200)
            saved=next(o for o in dashboard['orders'] if o['id']==result['id'])
            self.assertEqual(saved['merchant_name'],'مخزن ولعه')
            self.assertEqual(saved['items'][0]['product_id'],pid)
            payload['client_request_id']='offer-pack-test-again'
            self.assertEqual(self.app.request(self.app.customer,'/api/order',payload)[0],400)

if __name__=='__main__':unittest.main()
