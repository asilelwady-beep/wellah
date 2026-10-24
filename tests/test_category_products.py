import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class CategoryProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_db = server.DB
        server.DB = Path(self.temp.name) / 'catalog.sqlite3'
        with server.connect() as db:
            db.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,phone TEXT,role TEXT,username TEXT,email TEXT,disabled INTEGER DEFAULT 0);
                CREATE TABLE staff_members(user_id INTEGER,permissions TEXT);
                CREATE TABLE sessions(token_hash TEXT,user_id INTEGER,expires INTEGER);
                CREATE TABLE categories(name TEXT PRIMARY KEY,active INTEGER,deleted INTEGER DEFAULT 0);
                CREATE TABLE merchants(id INTEGER PRIMARY KEY,category TEXT,active INTEGER);
                CREATE TABLE products(id INTEGER PRIMARY KEY,name TEXT,category TEXT,price REAL,stock INTEGER,active INTEGER,deleted INTEGER DEFAULT 0,catalog_preview INTEGER DEFAULT 1,merchant_id INTEGER,price_pending INTEGER DEFAULT 0,requires_prescription INTEGER DEFAULT 0,image TEXT DEFAULT '');
                CREATE TABLE order_items(order_id INTEGER,product_id INTEGER,name TEXT,price REAL);
                INSERT INTO users(id,name,phone,role) VALUES(1,'Owner','01','admin'),(2,'Customer','02','customer'),(3,'Staff','03','admin');
                INSERT INTO staff_members VALUES(3,'["admin-products"]');
                INSERT INTO categories(name,active) VALUES('سوبر ماركت',1),('خضار',1);
                INSERT INTO merchants VALUES(1,'سوبر ماركت',1),(2,'خضار',1);
                INSERT INTO products(id,name,category,price,stock,active,merchant_id) VALUES(1,'أرز','سوبر ماركت',40,100,1,1),(2,'خيار','خضار',20,100,1,2);
                INSERT INTO order_items VALUES(7,1,'أرز',35);
            ''')
            for uid in (1, 2, 3):
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(f'catalog-token-{uid}'.encode()).hexdigest(), uid, int(time.time())+3600))
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

    def post(self, path, data, uid=1):
        req = Request(f'http://127.0.0.1:{self.http.server_port}/api/{path}', data=json.dumps(data).encode(), headers={'Authorization': f'Bearer catalog-token-{uid}', 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=5) as response: return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def product(self, pid):
        with server.connect() as db: return dict(db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone())

    def test_archive_removes_product_only_and_restore_stays_hidden(self):
        before = self.product(1)
        self.assertEqual(self.post('product/archive', {'id': 1, 'confirm_name': 'أرز'})[0], 200)
        self.assertEqual((self.product(1)['deleted'], self.product(1)['active'], self.product(1)['catalog_preview']), (1, 0, 0))
        self.assertEqual(self.product(2)['active'], 1)
        with server.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT product_id,name,price FROM order_items').fetchone()), (1, 'أرز', 35))
        self.assertEqual(self.post('product/restore', {'id': 1})[0], 200)
        restored = self.product(1)
        for key in ('name', 'price', 'stock', 'merchant_id', 'category'):
            self.assertEqual(restored[key], before[key])
        self.assertEqual((restored['deleted'], restored['active'], restored['catalog_preview']), (0, 0, 0))
        self.assertEqual(self.post('product/update', {'id': 1, 'price': 45, 'stock': 100, 'merchant_id': 1, 'active': True})[0], 200)
        self.assertEqual((self.product(1)['price'], self.product(1)['active']), (45, 1))

    def test_confirm_identity_and_authorization_before_archive(self):
        self.assertEqual(self.post('product/archive', {'id': 1, 'confirm_name': 'wrong'})[0], 400)
        for uid in (2, 3):
            self.assertIn(self.post('product/archive', {'id': 1, 'confirm_name': 'أرز'}, uid)[0], (401, 403))
            self.assertIn(self.post('product/restore', {'id': 1}, uid)[0], (401, 403))
        self.assertEqual(self.product(1)['deleted'], 0)

    def test_deleted_category_cannot_restore_and_new_product_keeps_category(self):
        self.assertEqual(self.post('product', {'name': 'جزر', 'category': 'خضار', 'price': 12, 'stock': 100, 'merchant_id': 2})[0], 200)
        self.assertEqual(self.product(3)['category'], 'خضار')
        self.post('product/archive', {'id': 1, 'confirm_name': 'أرز'})
        with server.connect() as db: db.execute("UPDATE categories SET deleted=1 WHERE name='سوبر ماركت'")
        self.assertEqual(self.post('product/restore', {'id': 1})[0], 400)
        self.assertEqual(self.product(1)['deleted'], 1)
