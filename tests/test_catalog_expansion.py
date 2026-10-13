import gzip
import json
import pathlib
import sys
import tempfile
import unittest
from test_catalog_pricing_20261013 import CatalogPricingTests
from catalog_expansion import apply_catalog_expansion

class ExpansionTests(CatalogPricingTests):
    def entry_new(self, name='ستربس كوكي - 400 جم', code='123456'):
        return dict(name=name,category='سوبر ماركت',price='159.50',image=f'https://cdn.mafrservices.com/products/{code}_main.jpg',source=f'https://www.carrefouregypt.com/mafegy/ar/p/{code}',source_sku=code,observed_on='2026-10-13')
    def batch(self, rows):
        pathlib.Path(self.temp.name).joinpath('catalog-expansion-20261013.json.gz').write_bytes(gzip.compress(json.dumps(rows).encode()))
    def apply(self):apply_catalog_expansion(self.db,pathlib.Path(self.temp.name),lambda:'2026-10-13')
    def test_new_priced_product_and_idempotency(self):
        self.batch([self.entry_new()]);self.apply();self.apply()
        p=self.db.execute('SELECT * FROM products WHERE id=4').fetchone()
        self.assertEqual(p['price'],159.5);self.assertEqual(p['stock'],0);self.assertEqual(p['active'],0);self.assertEqual(p['price_pending'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM products').fetchone()[0],4)
    def test_preserves_existing_owner_price(self):
        self.batch([self.entry_new('سعر صاحب المحل')]);self.apply()
        self.assertEqual(self.db.execute('SELECT price FROM products WHERE id=2').fetchone()[0],12.5)
    def test_whole_invalid_batch_is_skipped(self):
        bad=self.entry_new(code='999999');bad['price']='NaN';self.batch([self.entry_new(),bad]);self.apply()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM products').fetchone()[0],3)

if __name__=='__main__':unittest.main()
