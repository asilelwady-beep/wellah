import sqlite3
import time
import unittest
from datetime import datetime, timezone

from server import assign, refresh_offers, now


class NearestPickupDispatchTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY,disabled INTEGER DEFAULT 0);
            CREATE TABLE merchants(id INTEGER PRIMARY KEY,area TEXT,lat REAL,lon REAL);
            CREATE TABLE drivers(id INTEGER PRIMARY KEY,user_id INTEGER,lat REAL,lon REAL,location_at TEXT,area TEXT,vehicle_type TEXT DEFAULT 'موتوسيكل',featured INTEGER DEFAULT 0,identity_blocked_shift INTEGER,available INTEGER DEFAULT 1,break_until INTEGER DEFAULT 0);
            CREATE TABLE driver_shifts(driver_id INTEGER,started_at TEXT,ended_at TEXT,second_selfie TEXT);
            CREATE TABLE orders(id INTEGER PRIMARY KEY,status TEXT DEFAULT 'new',payment_status TEXT DEFAULT 'confirmed',kind TEXT DEFAULT 'products',quote_accepted INTEGER DEFAULT 1,medicine_review INTEGER DEFAULT 0,merchant_id INTEGER DEFAULT 1,shop_anywhere INTEGER DEFAULT 0,area TEXT DEFAULT 'A',pickup_lat REAL DEFAULT 29.85,pickup_lon REAL DEFAULT 31.27,latitude REAL DEFAULT 29.90,longitude REAL DEFAULT 31.27,vehicle TEXT DEFAULT 'موتوسيكل',driver_id INTEGER,offer_until INTEGER,commission_locked INTEGER DEFAULT 0,delivery_fee REAL DEFAULT 35,total REAL DEFAULT 100,commission_percent REAL,commission_cents INTEGER,driver_earning_cents INTEGER);
            CREATE TABLE order_declines(order_id INTEGER,driver_id INTEGER);
            CREATE TABLE order_offer_timeouts(order_id INTEGER,driver_id INTEGER,retry_after INTEGER,PRIMARY KEY(order_id,driver_id));
            CREATE TABLE settings(key TEXT,value TEXT);
            CREATE TABLE events(order_id INTEGER,action TEXT,at TEXT);
            INSERT INTO merchants VALUES(1,'A',29.85,31.27);
            INSERT INTO orders(id) VALUES(1);
        ''')
        self.add_driver(1,29.8501,'B',0)
        self.add_driver(2,29.9001,'A',1)
        self.db.commit()

    def tearDown(self): self.db.close()

    def add_driver(self,ident,lat,area,featured):
        self.db.execute('INSERT INTO users(id) VALUES(?)',(ident,))
        self.db.execute('INSERT INTO drivers(id,user_id,lat,lon,location_at,area,featured) VALUES(?,?,?,31.27,?,?,?)',(ident,ident,lat,now(),area,featured))
        self.db.execute('INSERT INTO driver_shifts(driver_id,started_at) VALUES(?,?)',(ident,now()))

    def chosen(self):
        assign(self.db,1)
        return self.db.execute('SELECT driver_id,status FROM orders WHERE id=1').fetchone()

    def test_supermarket_uses_shop_not_customer_or_featured_or_area(self):
        self.db.execute("INSERT INTO settings VALUES('featured_min_order','0')")
        self.assertEqual(self.chosen()['driver_id'],1)

    def test_shipment_uses_sender_pickup_not_destination(self):
        self.db.execute("UPDATE orders SET kind='delivery',merchant_id=NULL,pickup_lat=29.90,latitude=29.85 WHERE id=1")
        self.assertEqual(self.chosen()['driver_id'],2)

    def test_missing_shipment_pickup_waits_instead_of_using_destination(self):
        self.db.execute("UPDATE orders SET kind='delivery',merchant_id=NULL,pickup_lat=NULL,pickup_lon=NULL")
        result=self.chosen()
        self.assertIsNone(result['driver_id'])
        self.assertEqual(result['status'],'awaiting_driver')

    def test_named_shop_coordinates_are_used_when_stored_pickup_missing(self):
        self.db.execute('UPDATE orders SET pickup_lat=NULL,pickup_lon=NULL')
        self.assertEqual(self.chosen()['driver_id'],1)

    def test_stale_gps_is_not_treated_as_live_nearest_driver(self):
        old=datetime.fromtimestamp(time.time()-1800,timezone.utc).isoformat()
        self.db.execute('UPDATE drivers SET location_at=? WHERE id=1',(old,))
        self.assertEqual(self.chosen()['driver_id'],2)
        self.db.execute('UPDATE drivers SET location_at=?',(old,))
        self.assertIsNone(self.chosen()['driver_id'])

    def test_declined_driver_is_skipped(self):
        self.db.execute('INSERT INTO order_declines VALUES(1,1)')
        self.assertEqual(self.chosen()['driver_id'],2)

    def test_expired_offer_moves_to_next_nearest(self):
        self.assertEqual(self.chosen()['driver_id'],1)
        self.db.execute('UPDATE orders SET offer_until=? WHERE id=1',(int(time.time())-1,))
        refresh_offers(self.db)
        self.assertEqual(self.db.execute('SELECT driver_id FROM orders WHERE id=1').fetchone()[0],2)

    def test_busy_driver_cannot_receive_another_offer(self):
        self.db.execute("INSERT INTO orders(id,status,driver_id) VALUES(2,'assigned',1)")
        self.assertEqual(self.chosen()['driver_id'],2)

    def test_two_orders_reserve_different_drivers(self):
        self.db.execute('INSERT INTO orders(id) VALUES(2)')
        assign(self.db,1);assign(self.db,2)
        self.assertEqual([r[0] for r in self.db.execute('SELECT driver_id FROM orders ORDER BY id')],[1,2])

    def test_break_offline_and_closed_shift_are_ineligible(self):
        for change in ["UPDATE drivers SET break_until=9999999999 WHERE id=1","UPDATE drivers SET available=0 WHERE id=1","UPDATE driver_shifts SET ended_at='closed' WHERE driver_id=1"]:
            with self.subTest(change=change):
                self.db.execute('UPDATE drivers SET break_until=0,available=1');self.db.execute('UPDATE driver_shifts SET ended_at=NULL');self.db.execute("UPDATE orders SET status='new',driver_id=NULL,offer_until=NULL")
                self.db.execute(change)
                self.assertEqual(self.chosen()['driver_id'],2)
