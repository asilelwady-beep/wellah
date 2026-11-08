import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class TripProximityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        database = patch.object(server, 'DB', Path(temp.name) / 'test.sqlite')
        database.start()
        self.addCleanup(database.stop)
        with patch.dict(os.environ, {'WALLAHA_ADMIN_PASSWORD': 'proximity-test-only'}):
            server.init()
        with server.connect() as db:
            self.owner = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.driver = server.create_user(db, 'Test Driver', '01000000001', 'driver', 'proximity-test-only')
            self.customer = server.create_user(db, 'Test Customer', '01000000002', 'customer', 'proximity-test-only')
            self.did = db.execute("INSERT INTO drivers(user_id,name,area) VALUES(?, 'Test Driver','A')", (self.driver,)).lastrowid
            db.execute("INSERT INTO driver_shifts(driver_id,selfie,started_at) VALUES(?,'test',?)", (self.did,server.now()))
            self.oid = db.execute("""INSERT INTO orders(user_id,driver_id,kind,customer,phone,area,address,payment,
                payment_status,status,latitude,longitude,created_at,offer_until) VALUES(?,?,'products','Test','0',
                'A','Test destination','cash','confirmed','offered',30.0,31.0,?,?)""",
                (self.customer,self.did,server.now(),int(time.time())+60)).lastrowid
            for uid in (self.owner, self.driver, self.customer):
                token = hashlib.sha256(str(uid).encode()).hexdigest()
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (token,uid,int(time.time())+3600))

    def request(self, uid, path, data=None):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.headers = {'Authorization': 'Bearer '+str(uid)}
        handler.client_address = ('127.0.0.1', 0)
        handler.body = lambda: data
        result = []
        handler.respond = lambda value, code=200: result.extend((code,value))
        (handler.do_GET if data is None else handler.do_POST)()
        return result

    def post(self, uid, path, data):
        code, value = self.request(uid,path,data)
        self.assertEqual(code,200,value)
        return value

    def location(self, **changes):
        sample = dict(foreground=True, accuracy_m=8, fix_ms=time.time()*1000,lat=30.0002,lon=31.0)
        self.post(self.driver,'/api/location',{**sample,**changes})

    def alerts(self):
        code, state = self.request(self.owner,'/api/state?admin_catalog=summary')
        self.assertEqual(code,200)
        return state['trip_proximity_alerts']

    def action(self, uid, action):
        self.post(uid,'/api/order/action',{'id':self.oid,'action':action})

    def test_decline_then_reopen_and_review(self):
        self.action(self.driver,'decline_offer')
        self.location()
        first = self.alerts()
        self.assertEqual(len(first),1)
        alert = first[0]
        self.assertEqual((alert['driver_id'],alert['order_id'],alert['reason']), (self.did,self.oid,'driver_decline'))
        self.assertAlmostEqual(alert['distance_m'],22,delta=1)
        self.location(lat=30.0)
        self.assertEqual(first,self.alerts())
        review = {'order_id':self.oid,'driver_id':self.did}
        for uid in (self.driver,self.customer):
            self.assertNotIn('trip_proximity_alerts',self.request(uid,'/api/state')[1])
            self.assertEqual(self.request(uid,'/api/trip-alert/review',review)[0],403)
        self.post(self.owner,'/api/trip-alert/review',review)
        self.assertEqual(self.alerts()[0]['reviewed'],1)

    def test_invalid_samples_and_off_shift_are_ignored(self):
        old_fix = time.time()*1000
        self.action(self.customer,'customer_cancel')
        for changes in ({'foreground':False},{'accuracy_m':70},{'fix_ms':old_fix},
                        {'fix_ms':old_fix-120000},{'lat':30.001},{'lat':30.00085,'accuracy_m':15}):
            self.location(**changes)
        self.assertEqual(self.alerts(),[])
        with server.connect() as db:
            db.execute("UPDATE driver_shifts SET ended_at=? WHERE driver_id=?",(server.now(),self.did))
        self.location()
        self.assertEqual(self.alerts(),[])

    def test_owner_cancellation_and_expiry(self):
        self.action(self.owner,'cancel')
        with server.connect() as db:
            db.execute('UPDATE cancelled_trip_checks SET since=?',(time.time()-86401,))
        self.location()
        self.assertEqual(self.alerts(),[])

    def test_manual_reassignment_clears_pending_check(self):
        self.action(self.driver,'decline_offer')
        self.post(self.owner,'/api/order/reassign',{'id':self.oid,'driver_id':self.did})
        self.location()
        self.assertEqual(self.alerts(),[])


if __name__ == '__main__':
    unittest.main()
