import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_account_support import AccountSupportTests, server

_post=AccountSupportTests.__dict__['post']
_state_for=AccountSupportTests.__dict__['state_for']
del AccountSupportTests

class PharmacyDisabledTests(unittest.TestCase):
    post=_post
    state_for=_state_for

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        server.DB=Path(cls.temp.name)/'test.sqlite3'
        os.environ['WALLAHA_ADMIN_PASSWORD']='test-owner-password-123'
        server.init()
        with server.connect() as db:
            server.create_user(db,'عميل اختبار','01000000001','customer','customer-password-123')
            cls.mid=db.execute('INSERT INTO merchants(name,category,area,address,lat,lon) VALUES (?,?,?,?,?,?)',('صيدلية','أدوية',server.AREAS[0],'عنوان',29.85,31.27)).lastrowid
            cls.pid=db.execute('INSERT INTO products(name,category,price,stock,merchant_id) VALUES (?,?,?,?,?)',('دواء اختبار','أدوية',10,10,cls.mid)).lastrowid
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
        cls.ct=cls.post('login',{'phone':'01000000001','password':'customer-password-123'})[1]['token']

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.temp.cleanup()

    def test_pharmacy_orders_and_prescription_uploads_are_disabled(self):
        data={'client_request_id':'medicine-disabled','kind':'products','area':server.AREAS[0],'payment':'cash','address':'عنوان العميل','latitude':29.85,'longitude':31.27,'merchant_id':self.mid,'items':[{'product_id':self.pid,'quantity':1}]}
        for extra in ({}, {'prescription_only':True,'items':[]}, {'prescription':'data:image/png;base64,AAAA'}):
            with self.subTest(extra=extra):
                code,result=self.post('order',{**data,**extra},self.ct)
                self.assertEqual(code,400,result)
                self.assertIn('غير متاحة',result['error'])
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orders WHERE client_request_id='medicine-disabled'").fetchone()[0],0)

    def test_public_catalog_excludes_pharmacy_and_prescription_products(self):
        with server.connect() as db:
            db.execute("UPDATE categories SET active=1 WHERE name='أدوية'")
            db.execute("UPDATE products SET active=1,catalog_preview=1,price_pending=0 WHERE id=?",(self.pid,))
        with urlopen(self.base+'/api/catalog') as response:
            catalog=json.load(response)
        for catalog in (catalog,self.state_for(self.ct)):
            self.assertFalse(any(c['name']=='أدوية' for c in catalog['categories']))
            self.assertFalse(any(m['id']==self.mid for m in catalog['merchants']))
            for key in ('products','draft_catalog'):
                self.assertFalse(any(p['category']=='أدوية' or p['requires_prescription'] for p in catalog[key]))

