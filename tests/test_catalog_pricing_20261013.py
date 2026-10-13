import importlib.util
import json
import gzip
import pathlib
import sqlite3
import sys
import tempfile
import unittest

APP = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
spec = importlib.util.spec_from_file_location('catalog_price_app',APP/'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class CatalogPricingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = server.ROOT
        server.ROOT = pathlib.Path(self.temp.name)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE products(id INTEGER PRIMARY KEY,name TEXT,category TEXT,price REAL,price_pending INTEGER,stock REAL,active INTEGER,merchant_id INTEGER,catalog_preview INTEGER,image TEXT,price_basis_weight_kg REAL);
            CREATE TABLE catalog_imports(key TEXT PRIMARY KEY,imported_at TEXT);
            CREATE TABLE categories(name TEXT,active INTEGER,sort_order INTEGER);
            INSERT INTO categories VALUES ('سوبر ماركت',1,1);
            INSERT INTO products VALUES (1,'أرز 1 كجم','سوبر ماركت',0,1,0,0,NULL,1,'/photo.jpg',NULL);
            INSERT INTO products VALUES (2,'سعر صاحب المحل','سوبر ماركت',12.50,0,8,1,7,1,'/photo.jpg',NULL);
            INSERT INTO products VALUES (3,'منتج عدل صاحبه اسمه','سوبر ماركت',0,1,0,0,NULL,1,'/photo.jpg',NULL);
        ''')

    def tearDown(self):
        self.db.close()
        server.ROOT = self.previous_root
        self.temp.cleanup()

    def entry(self,pid=1,name='أرز 1 كجم',price='44.50'):
        return {'id':pid,'name':name,'category':'سوبر ماركت','price':price,'source':'https://www.carrefouregypt.com/mafegy/en/p/17997','observed_on':'2026-10-13'}

    def write_batch(self,entries):
        (server.ROOT/'verified-market-prices-20261013.json.gz').write_bytes(gzip.compress(json.dumps(entries,ensure_ascii=False).encode()))

    def test_fills_price_and_preserves_identity_stock_and_visibility(self):
        before=dict(self.db.execute('SELECT * FROM products WHERE id=1').fetchone())
        self.write_batch([self.entry()]);server.apply_verified_catalog_prices(self.db)
        after=dict(self.db.execute('SELECT * FROM products WHERE id=1').fetchone())
        self.assertEqual(after['price'],44.50);self.assertEqual(after['price_pending'],0)
        for key in ('id','name','category','stock','active','merchant_id','image'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM products').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM product_price_import_history').fetchone()[0],1)

    def test_weighted_price_keeps_explicit_basis(self):
        self.db.execute("UPDATE products SET name='تفاح بالوزن' WHERE id=1")
        item=self.entry(name='تفاح بالوزن',price='120.00');item['weight_basis_kg']=1
        self.write_batch([item]);server.apply_verified_catalog_prices(self.db)
        product=self.db.execute('SELECT * FROM products WHERE id=1').fetchone()
        self.assertTrue(server.sold_by_weight(product))
        self.assertEqual(server.weight_basis(product),1)
        self.assertEqual(product['price']/server.weight_basis(product)*0.25,30)

    def test_keeps_owner_price_and_skips_changed_identity(self):
        self.write_batch([self.entry(2,'سعر صاحب المحل'),self.entry(3,'الاسم السابق')])
        server.apply_verified_catalog_prices(self.db)
        self.assertEqual(self.db.execute('SELECT price FROM products WHERE id=2').fetchone()[0],12.5)
        self.assertEqual(self.db.execute('SELECT price FROM products WHERE id=3').fetchone()[0],0)

    def test_redeploy_does_not_repeat_or_overwrite_price(self):
        self.write_batch([self.entry()]);server.apply_verified_catalog_prices(self.db)
        self.db.execute('UPDATE products SET price=50 WHERE id=1')
        server.apply_verified_catalog_prices(self.db)
        self.assertEqual(self.db.execute('SELECT price FROM products WHERE id=1').fetchone()[0],50)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM product_price_import_history').fetchone()[0],1)

    def test_invalid_batch_does_not_apply_partial_updates(self):
        self.write_batch([self.entry(),self.entry(2,'سعر صاحب المحل','NaN')])
        server.apply_verified_catalog_prices(self.db)
        self.assertEqual(self.db.execute('SELECT price FROM products WHERE id=1').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM catalog_imports').fetchone()[0],0)

    def test_audit_failure_rolls_back_only_price_batch(self):
        self.db.execute('CREATE TABLE product_price_import_history (import_key TEXT,product_id INTEGER,name TEXT,old_price REAL,new_price REAL,source TEXT,observed_on TEXT,applied_at TEXT,PRIMARY KEY(import_key,product_id))')
        self.db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON product_price_import_history BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        self.write_batch([self.entry()]);server.apply_verified_catalog_prices(self.db)
        self.assertEqual(self.db.execute('SELECT price FROM products WHERE id=1').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT price_pending FROM products WHERE id=1').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM catalog_imports').fetchone()[0],0)

    def test_priced_catalog_stays_visible_without_becoming_orderable(self):
        self.write_batch([self.entry()]);server.apply_verified_catalog_prices(self.db)
        item=next(p for p in server.preview_catalog(self.db) if p['id']==1)
        self.assertEqual(item['price'],44.50)
        self.assertEqual(item['stock'],0)
        self.assertEqual(item['active'],0)
        self.assertEqual(item['price_pending'],0)


if __name__=='__main__':unittest.main()
