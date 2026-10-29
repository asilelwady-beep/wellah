import hashlib,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import server

class PlatformDiscountTests(unittest.TestCase):
 def test_customer_pays_net_driver_earns_original_and_platform_funds_difference(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(server,'DB',Path(tmp)/'test.db'),patch.dict(os.environ,{'WALLAHA_ADMIN_PASSWORD':'test-only-password-123'}),patch.object(server,'fetch_road_km',return_value=3):
   server.ROUTE_DISTANCE_CACHE.clear();server.init()
   with server.connect() as db:
    uid=server.create_user(db,'Customer','01012345678','customer','test-password-123')
    duid=server.create_user(db,'Driver','01012345679','driver','test-password-123')
    did=db.execute('INSERT INTO drivers(user_id,name,phone,area,lat,lon,location_at,vehicle_type) VALUES(?,?,?,?,?,?,?,?)',(duid,'Driver','01012345679',server.AREAS[0],29.81,31.21,server.now(),'موتوسيكل')).lastrowid
    db.execute('INSERT INTO driver_shifts(driver_id,selfie,started_at) VALUES(?,?,?)',(did,'test',server.now()))
    db.execute('INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)',(hashlib.sha256(b'test-token').hexdigest(),uid,int(time.time())+300))
    db.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',[('delivery_base_km','3'),('delivery_base_fee','20'),('delivery_extra_rate','5'),('commission_ride_motorbike','0')])
   class Quiet(server.Handler):
    def log_message(self,*args):pass
   http=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
   try:
    for shipping,order,percent in [(1000,0,0),(1000,0,20),(0,1000,20),(5000,0,20),(500,500,20)]:
     with server.connect() as db:
      db.execute('UPDATE users SET shipping_discount_cents=?,order_discount_cents=? WHERE id=?',(shipping,order,uid))
      db.execute("UPDATE settings SET value=? WHERE key='commission_ride_motorbike'",(str(percent),))
      data={'kind':'delivery','pickup_lat':29.81,'pickup_lon':31.21,'latitude':29.8,'longitude':31.2,'area':server.AREAS[0],'payment':'cash','address':'Test address','destination':'Test destination','pickup':'Test pickup','shipment_type':'ملابس','sender_name':'Sender','sender_phone':'01012345678','recipient_name':'Recipient','recipient_phone':'01012345670','client_request_id':str(time.time_ns())}
      quote=server.quote_delivery(db,uid,data);data['delivery_quote']=quote['quote_token']
     req=Request(f'http://127.0.0.1:{http.server_port}/api/order',data=json.dumps(data).encode(),headers={'Authorization':'Bearer test-token','Content-Type':'application/json'})
     try:
      with urlopen(req,timeout=15) as response:result=json.load(response)
     except HTTPError as e:self.fail(e.read().decode())
     with server.connect() as db:
      o=dict(db.execute('SELECT * FROM orders WHERE id=?',(result['id'],)).fetchone())
      expected_discount=min(2000,shipping+order)
      self.assertEqual(o['total'],(2000-expected_discount)/100)
      self.assertEqual(o['original_delivery_fee'],20)
      self.assertEqual(o['driver_earning_cents'],2000-20*percent)
      self.assertEqual(o['commission_cents'],20*percent-expected_discount)
      self.assertEqual(o['total']*100,o['driver_earning_cents']+o['commission_cents'])
      # Additional fees keep the agreed rate even if settings change later.
      db.execute("UPDATE settings SET value='50' WHERE key='commission_ride_motorbike'")
      self.assertEqual(server.order_commission_split(db,o,'موتوسيكل',5),(percent,25*percent-expected_discount,2500-25*percent))
      db.execute("UPDATE orders SET status='delivered',cash_collected=1 WHERE id=?",(o['id'],))
      wallet=server.driver_wallet(db,did)
      entry=next(e for e in wallet['entries'] if e['id']==o['id'])
      self.assertEqual(entry['driver_earning_cents'],2000-20*percent)
      db.execute('UPDATE orders SET driver_earning_paid=1,cash_settled=1,commission_settled=1 WHERE id=?',(o['id'],))
      self.assertEqual(server.driver_wallet(db,did)['balance'],0)
      self.assertEqual(server.driver_wallet(db,did)['cash_due'],0)
    def post(path,payload,token='test-token'):
     req=Request(f'http://127.0.0.1:{http.server_port}/api/'+path,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
     with urlopen(req,timeout=15) as response:return json.load(response)
    def new_order():
     data['client_request_id']=str(time.time_ns())
     with server.connect() as db:data['delivery_quote']=server.quote_delivery(db,uid,data)['quote_token']
     return post('order',data)['id']
    with server.connect() as db:
     db.execute('UPDATE users SET shipping_discount_cents=0,order_discount_cents=0 WHERE id=?',(uid,))
     for kind in ('customer_shipping','customer_order','discount'):
      db.execute('INSERT INTO featured_rewards(user_id,kind,amount_cents,created_at) VALUES(?,?,100,?)',(uid,kind,server.now()))
    cancelled=new_order()
    with server.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM featured_rewards WHERE used_order_id=?',(cancelled,)).fetchone()[0],3)
    post('order/action',{'id':cancelled,'action':'customer_cancel'})
    with server.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM featured_rewards WHERE user_id=? AND used_order_id IS NULL',(uid,)).fetchone()[0],3)
    with server.connect() as db:
     db.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',[('customer_tier_threshold','2'),('customer_tier_percent','50'),('customer_tier_scope','shipping')])
    tier_order=new_order()
    with server.connect() as db:
     self.assertEqual(db.execute('SELECT customer_tier_last_reward_orders FROM users WHERE id=?',(uid,)).fetchone()[0],0)
     self.assertEqual(server.customer_discount_terms(db,uid)['tier_percent'],0)
    post('order/action',{'id':tier_order,'action':'customer_cancel'})
    with server.connect() as db:self.assertEqual(server.customer_discount_terms(db,uid)['tier_percent'],50)
    completed=new_order()
    with server.connect() as db:
     base=db.execute('SELECT tier_reward_base FROM orders WHERE id=?',(completed,)).fetchone()[0]
     db.execute("UPDATE orders SET status='on_way',driver_id=? WHERE id=?",(did,completed))
     db.execute('INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)',(hashlib.sha256(b'driver-token').hexdigest(),duid,int(time.time())+300))
    post('order/action',{'id':completed,'action':'delivered','cash_collected':True,'delivery_photo':'data:image/png;base64,iVBORw0KGgo='},'driver-token')
    with server.connect() as db:
     self.assertEqual(db.execute('SELECT customer_tier_last_reward_orders FROM users WHERE id=?',(uid,)).fetchone()[0],base)
     self.assertEqual(server.customer_discount_terms(db,uid)['tier_percent'],0)
     self.assertGreater(db.execute('SELECT COUNT(*) FROM featured_rewards WHERE used_order_id=?',(completed,)).fetchone()[0],0)
   finally:http.shutdown();http.server_close();thread.join()
