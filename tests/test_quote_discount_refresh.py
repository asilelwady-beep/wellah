import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import server

class QuoteDiscountRefreshTests(unittest.TestCase):
 def test_reprice_same_route_after_discount_change(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(server,'DB',Path(tmp)/'test.db'),patch.dict(os.environ,{'WALLAHA_ADMIN_PASSWORD':'test-only-password-123'}),patch.object(server,'fetch_road_km',return_value=3) as road:
   server.ROUTE_DISTANCE_CACHE.clear();server.init()
   with server.connect() as db:
    uid=server.create_user(db,'Test','01012345678','customer','test-password-123')
    db.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',[('delivery_base_km','3'),('delivery_base_fee','30'),('delivery_extra_rate','5')])
    data={'kind':'delivery','pickup_lat':29.81,'pickup_lon':31.21,'latitude':29.8,'longitude':31.2}
    first=server.quote_delivery(db,uid,data);self.assertEqual(first['fee'],30)
    db.execute('UPDATE users SET shipping_discount_cents=1500 WHERE id=?',(uid,))
    discounted=server.quote_delivery(db,uid,data);self.assertEqual(discounted['fee'],15);self.assertEqual(discounted['original_fee'],30)
    self.assertEqual(road.call_count,1)
    with self.assertRaises(ValueError):server.verify_delivery_quote(db,uid,{**data,'delivery_quote':first['quote_token']})
    self.assertEqual(server.verify_delivery_quote(db,uid,{**data,'delivery_quote':discounted['quote_token']})['fee'],15)
    db.execute('UPDATE users SET shipping_discount_cents=5000 WHERE id=?',(uid,))
    self.assertEqual(server.quote_delivery(db,uid,data)['fee'],0)
    db.execute('UPDATE users SET shipping_discount_cents=0,order_discount_cents=500 WHERE id=?',(uid,))
    order=server.quote_delivery(db,uid,data);self.assertEqual(order['fee'],30);self.assertEqual(order['customer_order_discount_cents'],500)
    db.execute("INSERT INTO featured_rewards(user_id,kind,amount_cents,created_at) VALUES(?,'customer_shipping',1000,?)",(uid,server.now()))
    self.assertEqual(server.quote_delivery(db,uid,data)['fee'],20)
    self.assertEqual(road.call_count,1)
