import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class ChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_db = server.DB
        server.DB = Path(self.temp.name) / 'chat.sqlite3'
        with server.connect() as db:
            db.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,phone TEXT,role TEXT,username TEXT,email TEXT,disabled INTEGER DEFAULT 0);
                CREATE TABLE staff_members(user_id INTEGER,permissions TEXT);
                CREATE TABLE sessions(token_hash TEXT,user_id INTEGER,expires INTEGER);
                CREATE TABLE drivers(id INTEGER PRIMARY KEY,user_id INTEGER,identity_blocked_shift INTEGER);
                CREATE TABLE orders(id INTEGER PRIMARY KEY,user_id INTEGER,driver_id INTEGER,status TEXT);
                CREATE TABLE order_messages(id INTEGER PRIMARY KEY,order_id INTEGER,driver_id INTEGER,sender_id INTEGER,request_id TEXT,body TEXT,at TEXT,UNIQUE(sender_id,request_id));
                INSERT INTO users(id,name,phone,role) VALUES(1,'Customer','01','customer'),(2,'Driver','02','driver'),(3,'Other','03','customer'),(4,'Owner','04','admin');
                INSERT INTO drivers VALUES(7,2,NULL);
                INSERT INTO orders VALUES(61,1,7,'assigned');
            ''')
            for uid in range(1, 5):
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(('test-token-'+str(uid)).encode()).hexdigest(),uid,int(time.time())+3600))
        with server.connect() as db:
            for name in ('pickup_lat','pickup_lon','latitude','longitude'):
                db.execute('ALTER TABLE orders ADD COLUMN '+name+' REAL')
            for name in ('lat','lon'):
                db.execute('ALTER TABLE drivers ADD COLUMN '+name+' REAL')
            db.execute('ALTER TABLE drivers ADD COLUMN location_at TEXT')
            db.execute("UPDATE drivers SET lat=29.8513,lon=31.2744,location_at=?", (server.now(),))
        class Handler(server.Handler):
            def log_message(self, *args): pass
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.DB = self.previous_db
        self.temp.cleanup()

    def post(self, uid, data, path='/api/order/chat'):
        req = Request('http://127.0.0.1:'+str(self.http.server_port)+path, data=json.dumps(data).encode(), headers={'Authorization':'Bearer test-token-'+str(uid),'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=5) as response: return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)

    def test_two_way_messages_persist_and_retries_do_not_duplicate(self):
        for uid in [1,2]:
            code,result=self.post(uid,{'order_id':61,'mode':'list'})
            self.assertEqual(code,200)
            self.assertEqual(result['messages'],[])
        for uid, body in [(1,'السلام عليكم'),(2,'وعليكم السلام')]:
            data={'order_id':61,'mode':'send','body':body,'request_id':'message-request-'+str(uid)}
            code,result=self.post(uid,data)
            self.assertEqual(code,200)
            self.assertEqual(result['messages'][-1]['body'],body)
            self.assertEqual(self.post(uid,data)[0],200)
        for uid in [1,2]:
            code,result=self.post(uid,{'order_id':61,'mode':'list'})
            self.assertEqual(code,200)
            self.assertEqual(len(result['messages']),2)
        with sqlite3.connect(server.DB) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM order_messages').fetchone()[0],2)
        code,archive=self.post(4,{'order_id':61},'/api/admin/chat/archive')
        self.assertEqual(code,200)
        self.assertEqual(len(archive['messages']),2)

    def test_unrelated_customer_cannot_read_or_send(self):
        for mode in ['list','send']:
            self.assertEqual(self.post(3,{'order_id':61,'mode':mode,'body':'private','request_id':'unrelated-request'})[0],403)

    def test_completed_order_cannot_receive_new_messages(self):
        with server.connect() as db: db.execute("UPDATE orders SET status='delivered' WHERE id=61")
        self.assertEqual(self.post(2,{'order_id':61,'mode':'send','body':'late','request_id':'closed-order-request'})[0],400)

    def test_tracking_only_discloses_location_to_order_customer_after_acceptance(self):
        def get(uid):
            req=Request('http://127.0.0.1:'+str(self.http.server_port)+'/api/order/tracking?order_id=61',headers={'Authorization':'Bearer test-token-'+str(uid)})
            try:
                with urlopen(req,timeout=5) as response: return response.status,json.load(response)
            except HTTPError as error: return error.code,json.load(error)
        code,result=get(1)
        self.assertEqual(code,200)
        self.assertEqual(result['order']['driver_lat'],29.8513)
        self.assertEqual(get(3)[0],403)
        self.assertEqual(get(2)[0],403)
        for status in ['offered','delivered','cancelled']:
            with server.connect() as db: db.execute('UPDATE orders SET status=? WHERE id=61',(status,))
            self.assertIsNone(get(1)[1]['order']['driver_lat'])
