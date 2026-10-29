import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch
import server

class AdminCatalogRefreshTests(unittest.TestCase):
    def test_full_delta_invalidation_and_old_clients(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB', Path(tmp)/'test.sqlite3'), patch.dict(os.environ, {'WALLAHA_ADMIN_PASSWORD':'test-only-password-123'}):
            server.init()
            with server.connect() as db:
                uid=db.execute("SELECT id FROM users WHERE role='admin'").fetchone()['id']
                db.execute('INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)',(hashlib.sha256(b'test-token').hexdigest(),uid,int(time.time())+300))
            with server.connect() as reader, server.connect() as writer:
                self.assertEqual(reader.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM settings").fetchall()
                writer.execute("UPDATE settings SET value=value")
                writer.commit()  # An open dashboard read must not block saves.
                reader.rollback()
            class Quiet(server.Handler):
                def log_message(self,*args): pass
            http=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            def get(revision='',summary=False):
                req=Request(f'http://127.0.0.1:{http.server_port}/api/state'+('?admin_catalog=summary' if summary else '?admin_catalog_revision='+revision if revision else ''),headers={'Authorization':'Bearer test-token'})
                with urlopen(req,timeout=15) as response: return json.load(response)
            try:
                before=time.perf_counter();full=get();full_time=time.perf_counter()-before
                self.assertTrue(full['products'])
                lightweight=get(summary=True)
                self.assertFalse(lightweight['admin_products_loaded'])
                self.assertEqual(lightweight['products'],[])
                self.assertNotIn('admin_catalog_revision',lightweight)
                self.assertEqual(lightweight['orders'],full['orders'])
                self.assertEqual(lightweight['customers'],full['customers'])
                self.assertTrue(get()['admin_products_loaded'])
                print(f"Initial summary: {len(json.dumps(lightweight))} bytes")
                revision=full['admin_catalog_revision']
                before=time.perf_counter();delta=get(revision);delta_time=time.perf_counter()-before
                self.assertTrue(delta['admin_catalog_unchanged'])
                self.assertNotIn('products',delta)
                self.assertNotIn('draft_catalog',delta)
                self.assertIn('customers',delta)
                self.assertIn('orders',delta)
                self.assertLess(len(json.dumps(delta)),len(json.dumps(full)))
                print(f"Local fixture: full={len(json.dumps(full))} bytes/{full_time:.3f}s; unchanged={len(json.dumps(delta))} bytes/{delta_time:.3f}s")
                pid=full['products'][0]['id']
                with server.connect() as db: db.execute('UPDATE products SET price=123.45 WHERE id=?',(pid,))
                changed=get(revision)
                self.assertFalse(changed['admin_catalog_unchanged'])
                self.assertEqual(next(p for p in changed['products'] if p['id']==pid)['price'],123.45)
                for table,sql in [('categories','UPDATE categories SET active=1-active WHERE name=(SELECT name FROM categories LIMIT 1)'),('merchants',"INSERT INTO merchants(name,category,area,address,lat,lon) VALUES('test','سوبر ماركت','test','test',29.8,31.2)")]:
                    revision=changed['admin_catalog_revision']
                    with server.connect() as db: db.execute(sql)
                    changed=get(revision)
                    self.assertFalse(changed['admin_catalog_unchanged'],table)
                self.assertIn('products',get())  # Existing installed clients keep receiving full data.
                with server.connect() as db:
                    revision=server.admin_catalog_state(db)['admin_catalog_revision']
                    db.execute('UPDATE products SET price=1 WHERE id=?',(pid,));db.rollback()
                    self.assertEqual(server.admin_catalog_state(db)['admin_catalog_revision'],revision)
            finally:
                http.shutdown();http.server_close();thread.join()
