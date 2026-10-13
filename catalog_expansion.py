"""One-time expansion of existing catalog using verified retailer product records."""
import gzip
import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse


def apply_catalog_expansion(db, root, now):
    path = root / 'catalog-expansion-20261013.json.gz'
    if not path.is_file():
        return
    try:
        raw = path.read_bytes()
        key = 'catalog-expansion-20261013-' + hashlib.sha256(raw).hexdigest()[:16]
        if db.execute('SELECT 1 FROM catalog_imports WHERE key=?', (key,)).fetchone():
            return
        entries = json.loads(gzip.decompress(raw))
        if not isinstance(entries, list):
            raise ValueError('Expected catalog list')
        seen = set()
        validated = []
        for item in entries:
            code = str(item['source_sku'])
            price = Decimal(str(item['price']))
            source, image = urlparse(item['source']), urlparse(item['image'])
            if not code.isdigit() or code in seen or source.scheme != 'https' or source.hostname != 'www.carrefouregypt.com' or not source.path.endswith('/p/' + code):
                raise ValueError('Invalid or duplicate source identity')
            if image.scheme != 'https' or image.hostname != 'cdn.mafrservices.com':
                raise ValueError('Unexpected product image source')
            if not price.is_finite() or not 0 < price <= 10000000 or price != price.quantize(Decimal('.01')):
                raise ValueError('Invalid ordinary price')
            if not isinstance(item['name'], str) or not item['name'].strip() or item['category'] != 'سوبر ماركت' or item['observed_on'] != '2026-10-13':
                raise ValueError('Invalid catalog record')
            seen.add(code)
            validated.append((item, float(price)))
    except (OSError, EOFError, ValueError, TypeError, KeyError, InvalidOperation) as error:
        print('Catalog expansion skipped: ' + str(error))
        return
    db.execute('SAVEPOINT catalog_expansion')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS catalog_source_records (source_sku TEXT PRIMARY KEY,product_id INTEGER NOT NULL,source TEXT NOT NULL,observed_on TEXT NOT NULL,ordinary_price REAL NOT NULL)')
        images = {urlparse(p['image'] or '')._replace(query='',fragment='').geturl() for p in db.execute('SELECT image FROM products') if p['image']}
        added = updated = skipped = 0
        for item, price in validated:
            if db.execute('SELECT 1 FROM catalog_source_records WHERE source_sku=?', (item['source_sku'],)).fetchone():
                continue
            existing = db.execute('SELECT id,price,price_pending FROM products WHERE name=? AND category=?', (item['name'], item['category'])).fetchone()
            image_key = urlparse(item['image'])._replace(query='',fragment='').geturl()
            if existing:
                pid = existing['id']
                if existing['price'] == 0 and existing['price_pending']:
                    db.execute('UPDATE products SET price=?,price_pending=0 WHERE id=? AND price=0 AND price_pending=1', (price,pid))
                    updated += 1
            elif image_key in images:
                skipped += 1
                continue
            else:
                pid = db.execute('INSERT INTO products(name,category,price,stock,active,merchant_id,catalog_preview,price_pending,image) VALUES (?,?,?,100,0,NULL,1,0,?)', (item['name'],item['category'],price,item['image'])).lastrowid
                images.add(image_key)
                added += 1
            db.execute('INSERT INTO catalog_source_records VALUES (?,?,?,?,?)', (item['source_sku'],pid,item['source'],item['observed_on'],price))
        db.execute('INSERT INTO catalog_imports VALUES (?,?)', (key,now()))
        db.execute('RELEASE catalog_expansion')
        print(f'Catalog expansion applied: {added} added, {updated} priced, {skipped} duplicate photos skipped')
    except (sqlite3.Error, ValueError, TypeError) as error:
        db.execute('ROLLBACK TO catalog_expansion')
        db.execute('RELEASE catalog_expansion')
        print('Catalog expansion rolled back: ' + str(error))
