#!/usr/bin/env python3
"""Local, dependency-free prototype for Wallaha. Not a production service."""
import json
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import zipfile
import time
import math
from html import escape as xml_escape
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request, build_opener, HTTPRedirectHandler
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from threading import Lock

ROOT = Path(__file__).parent
DB = Path(os.environ.get('WALLAHA_DB_PATH', str(ROOT / 'wallaha.sqlite3')))
AREAS = ["أبو رجوان البحري", "أبو رجوان القبلي", "أبو صير", "ميت رهينة", "سقارة", "دهشور", "زاوية دهشور", "الشوبك الغربي", "الطرفاية", "المرازيق", "الشنباب", "العزيزية"]
CAIRO = ZoneInfo('Africa/Cairo')
GEOCODE_LOCK = Lock()
GEOCODE_STATE = {'last': 0.0, 'cache': {}}
MAP_LINK_HOSTS = {'maps.app.goo.gl', 'www.google.com', 'google.com', 'maps.google.com'}

class SafeMapsRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != 'https' or target.hostname not in MAP_LINK_HOSTS:
            raise ValueError('رابط الخريطة غير مدعوم')
        return super().redirect_request(request, fp, code, msg, headers, newurl)



def sold_by_weight(product):
    name, category = product['name'], product['category']
    if re.search(r'علبة|عبوة|باكت|معلب|مجمد|مجمّد|Frozen|Pack|حزمة|ربطة|قطعة|قطعتين|حبة|سبريد|شرائح جاهزة', name, re.I):
        return False
    return bool(re.search(r'خضار|فاكهة|فواكه|لحوم|أسماك|دواجن', category) or
                (category == 'سوبر ماركت' and (re.search(r'^(?:لحم|لحمة|كبدة|دجاج طازج|فراخ طازجة|سمك|بلطي|بوري|جمبري|كابوريا|ثوم طازج|لانشون|لنشون|لَنشون|بسطرمة|سلامي|مرتديلا|ديك رومي|جبنة رومي|جبن رومي|جبنة شيدر|جبن شيدر)(?:\s|$)', name) or (re.search(r'لانشون|سلامي|سلامى|بسطرمة', name) and re.search(r'بالوزن|بالكيلو', name)))))


def weight_basis(product):
    match = re.search(r'(\d+(?:[.]\d+)?)\s*(كجم|كيلو|kg|جرام|غرام|جم|g)\b', product['name'], re.I)
    if not match:
        return 1.0
    value = float(match[1])
    return (value / 1000 if match[2].lower() in ('جرام','غرام','جم','g') else value) or 1.0


def order_quantity(product, value):
    quantity = float(value)
    if not math.isfinite(quantity) or quantity <= 0 or quantity > 100000:
        raise ValueError('الكمية غير صحيحة')
    if sold_by_weight(product):
        if quantity * 4 != int(quantity * 4):
            raise ValueError('اختر الوزن بمضاعفات ربع كيلو')
    elif quantity != int(quantity):
        raise ValueError('هذا المنتج يباع بالقطعة')
    return quantity


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS area_fees (area TEXT PRIMARY KEY, fee REAL NOT NULL CHECK(fee>=0));
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, role TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (phone TEXT NOT NULL, remote TEXT NOT NULL, attempts INTEGER NOT NULL, blocked_until INTEGER NOT NULL, PRIMARY KEY(phone,remote));
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price REAL NOT NULL CHECK(price >= 0), stock INTEGER NOT NULL CHECK(stock >= 0), image TEXT DEFAULT '', active INTEGER DEFAULT 1, requires_prescription INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS services (key TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS merchants (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, area TEXT NOT NULL, address TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS order_declines (order_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, PRIMARY KEY(order_id,driver_id));
        CREATE TABLE IF NOT EXISTS drivers (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE REFERENCES users(id), name TEXT NOT NULL, phone TEXT DEFAULT '', area TEXT NOT NULL, available INTEGER DEFAULT 1, lat REAL, lon REAL, location_at TEXT);
        CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), client_request_id TEXT, kind TEXT NOT NULL, customer TEXT NOT NULL, phone TEXT NOT NULL, area TEXT NOT NULL, address TEXT NOT NULL, details TEXT DEFAULT '', vehicle TEXT DEFAULT '', pickup TEXT DEFAULT '', destination TEXT DEFAULT '', payment TEXT NOT NULL, proof TEXT DEFAULT '', reference TEXT DEFAULT '', prescription TEXT DEFAULT '', medicine_review INTEGER DEFAULT 0, cash_collected INTEGER DEFAULT 0, cash_settled INTEGER DEFAULT 0, payment_status TEXT NOT NULL, status TEXT NOT NULL, total REAL NOT NULL DEFAULT 0, delivery_fee REAL NOT NULL DEFAULT 0, quote_accepted INTEGER DEFAULT 0, driver_id INTEGER REFERENCES drivers(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_items (order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id), name TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS order_messages (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), driver_id INTEGER NOT NULL REFERENCES drivers(id), sender_id INTEGER NOT NULL REFERENCES users(id), request_id TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(sender_id,request_id));
        CREATE INDEX IF NOT EXISTS order_messages_thread ON order_messages(order_id,driver_id,id);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), action TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS driver_trip_points (driver_id INTEGER NOT NULL, order_id INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, at TEXT NOT NULL, PRIMARY KEY(driver_id,order_id));
        CREATE TABLE IF NOT EXISTS driver_distance_daily (driver_id INTEGER NOT NULL, day TEXT NOT NULL, meters REAL NOT NULL DEFAULT 0, PRIMARY KEY(driver_id,day));
        """)
        if not db.execute("SELECT 1 FROM settings WHERE key='wallet'").fetchone():
            db.executemany("INSERT INTO settings VALUES (?,?)", [("wallet", os.environ.get('WALLAHA_WALLET','')), ("whatsapp", os.environ.get('WALLAHA_WHATSAPP','')), ("delivery_fee", "20")])
        db.execute("INSERT OR IGNORE INTO settings VALUES ('per_km_rate','')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('quote_secret',?)",(secrets.token_hex(32),))
        for table,column,definition in [('orders','route_km','REAL'),('orders','km_rate','REAL'),('products','price_pending','INTEGER NOT NULL DEFAULT 0'),('order_items','unit',"TEXT NOT NULL DEFAULT 'قطعة'"),('order_items','stock_quantity','REAL')]:
            if column not in {x['name'] for x in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        for column,definition in [('driver_earning_cents' ,'INTEGER NOT NULL DEFAULT 0'),('driver_earning_paid','INTEGER NOT NULL DEFAULT 0')]:
            if column not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
                db.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
        if 'username' not in {x['name'] for x in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN username TEXT')
        if 'vehicle_type' not in {x['name'] for x in db.execute('PRAGMA table_info(drivers)')}:
            db.execute("ALTER TABLE drivers ADD COLUMN vehicle_type TEXT NOT NULL DEFAULT 'موتوسيكل'")
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username) WHERE username IS NOT NULL')
        if not db.execute("SELECT 1 FROM users WHERE username='owner'").fetchone():
            db.execute("UPDATE users SET username='owner' WHERE role='admin' AND username IS NULL")
        db.execute("INSERT OR IGNORE INTO services(key,name) VALUES ('products','المنتجات'),('delivery','توصيل أوردر'),('ride_tuktuk','مشوار توك توك'),('ride_motorbike','مشوار موتوسيكل'),('ride_car','مشوار سيارة'),('ride_microbus','مشوار ميكروباص'),('ride_bicycle','مشوار عجلة')")
        db.executemany('INSERT OR IGNORE INTO categories(name,sort_order) VALUES (?,?)', [(name,i) for i,name in enumerate(('سوبر ماركت','مطاعم','خضار','أدوية','مخبوزات وعيش','أخرى'))])
        db.execute('INSERT OR IGNORE INTO categories(name,sort_order) SELECT DISTINCT category,100 FROM products')
        if 'quote_accepted' not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
            db.execute('ALTER TABLE orders ADD COLUMN quote_accepted INTEGER DEFAULT 0')
            db.execute("UPDATE orders SET quote_accepted=1 WHERE kind='products' OR status NOT IN ('awaiting_quote','payment_review')")
        for table,column,definition in [('products','requires_prescription','INTEGER DEFAULT 0'),('orders','client_request_id','TEXT'),('orders','prescription',"TEXT DEFAULT ''"),('orders','medicine_review','INTEGER DEFAULT 0'),('orders','cash_collected','INTEGER DEFAULT 0'),('orders','cash_settled','INTEGER DEFAULT 0')]:
            if column not in {x['name'] for x in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        if 'service_key' not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
            db.execute("ALTER TABLE orders ADD COLUMN service_key TEXT DEFAULT ''")
        for column in ('shipment_type','shipment_other','sender_name','sender_phone','recipient_name','recipient_phone'):
            if column not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
                db.execute(f"ALTER TABLE orders ADD COLUMN {column} TEXT DEFAULT ''")
        for column in ('latitude','longitude'):
            if column not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
                db.execute(f'ALTER TABLE orders ADD COLUMN {column} REAL')
        if 'merchant_id' not in {x['name'] for x in db.execute('PRAGMA table_info(products)')}:
            db.execute('ALTER TABLE products ADD COLUMN merchant_id INTEGER REFERENCES merchants(id)')
        for column,definition in [('merchant_id','INTEGER REFERENCES merchants(id)'),('pickup_lat','REAL'),('pickup_lon','REAL'),('offer_until','INTEGER')]:
            if column not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
                db.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS orders_request_once ON orders(user_id,client_request_id) WHERE client_request_id IS NOT NULL')
        db.executemany('INSERT OR IGNORE INTO area_fees(area,fee) VALUES (?,?)', [(area,20) for area in AREAS])
        if 'catalog_preview' not in {x['name'] for x in db.execute('PRAGMA table_info(products)')}:
            db.execute('ALTER TABLE products ADD COLUMN catalog_preview INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE TABLE IF NOT EXISTS catalog_imports (key TEXT PRIMARY KEY, imported_at TEXT NOT NULL)')
        if not db.execute("SELECT 1 FROM catalog_imports WHERE key='starter-catalog-v1'").fetchone():
            db.execute("INSERT OR IGNORE INTO categories(name,sort_order) VALUES ('لحوم ودواجن',6)")
            starter_catalog = [('سوبر ماركت', 'أرز مصري — 1 كجم'), ('سوبر ماركت', 'أرز بسمتي — 1 كجم'), ('سوبر ماركت', 'أرز مصري — 5 كجم'), ('سوبر ماركت', 'سكر أبيض — 1 كجم'), ('سوبر ماركت', 'دقيق أبيض — 1 كجم'), ('سوبر ماركت', 'دقيق قمح كامل — 1 كجم'), ('سوبر ماركت', 'مكرونة قلم — 400 جم'), ('سوبر ماركت', 'مكرونة سباجيتي — 400 جم'), ('سوبر ماركت', 'مكرونة خواتم — 400 جم'), ('سوبر ماركت', 'مكرونة لسان عصفور — 400 جم'), ('سوبر ماركت', 'شعرية — 400 جم'), ('سوبر ماركت', 'نودلز سريعة التحضير — عبوة'), ('سوبر ماركت', 'عدس أصفر — 500 جم'), ('سوبر ماركت', 'عدس بجبة — 500 جم'), ('سوبر ماركت', 'فول جاف — 500 جم'), ('سوبر ماركت', 'فاصوليا بيضاء — 500 جم'), ('سوبر ماركت', 'لوبيا جافة — 500 جم'), ('سوبر ماركت', 'حمص جاف — 500 جم'), ('سوبر ماركت', 'فشار — 500 جم'), ('سوبر ماركت', 'برغل — 500 جم'), ('سوبر ماركت', 'شوفان — 500 جم'), ('سوبر ماركت', 'زيت عباد الشمس — 1 لتر'), ('سوبر ماركت', 'زيت ذرة — 1 لتر'), ('سوبر ماركت', 'زيت خليط — 1 لتر'), ('سوبر ماركت', 'زيت زيتون — 250 مل'), ('سوبر ماركت', 'سمن نباتي — 700 جم'), ('سوبر ماركت', 'سمن بلدي — 500 جم'), ('سوبر ماركت', 'زبدة — 200 جم'), ('سوبر ماركت', 'صلصة طماطم — 300 جم'), ('سوبر ماركت', 'خل أبيض — 1 لتر'), ('سوبر ماركت', 'ملح طعام — 1 كجم'), ('سوبر ماركت', 'فلفل أسود — 50 جم'), ('سوبر ماركت', 'كمون — 50 جم'), ('سوبر ماركت', 'كزبرة جافة — 50 جم'), ('سوبر ماركت', 'كركم — 50 جم'), ('سوبر ماركت', 'قرفة — 50 جم'), ('سوبر ماركت', 'شطة — 50 جم'), ('سوبر ماركت', 'بهارات مشكلة — 50 جم'), ('سوبر ماركت', 'بيكنج بودر — ظرف'), ('سوبر ماركت', 'فانيليا — ظرف'), ('سوبر ماركت', 'خميرة جافة — ظرف'), ('سوبر ماركت', 'شاي أسود — 250 جم'), ('سوبر ماركت', 'شاي أخضر — 25 كيس'), ('سوبر ماركت', 'قهوة تركية — 200 جم'), ('سوبر ماركت', 'قهوة سريعة الذوبان — 50 جم'), ('سوبر ماركت', 'كاكاو — 100 جم'), ('سوبر ماركت', 'لبن كامل الدسم — 1 لتر'), ('سوبر ماركت', 'لبن قليل الدسم — 1 لتر'), ('سوبر ماركت', 'لبن بودرة — 250 جم'), ('سوبر ماركت', 'زبادي سادة — عبوة'), ('سوبر ماركت', 'جبنة بيضاء — 250 جم'), ('سوبر ماركت', 'جبنة قريش — 250 جم'), ('سوبر ماركت', 'جبنة رومي — 250 جم'), ('سوبر ماركت', 'جبنة شيدر — 250 جم'), ('سوبر ماركت', 'جبنة مثلثات — عبوة'), ('سوبر ماركت', 'جبنة موزاريلا — 250 جم'), ('سوبر ماركت', 'بيض — 10 بيضات'), ('سوبر ماركت', 'بيض — طبق 30 بيضة'), ('سوبر ماركت', 'تونة قطع — عبوة'), ('سوبر ماركت', 'تونة مفتتة — عبوة'), ('سوبر ماركت', 'سردين — عبوة'), ('سوبر ماركت', 'فول معلب — عبوة'), ('سوبر ماركت', 'ذرة حلوة — عبوة'), ('سوبر ماركت', 'مربى فراولة — 350 جم'), ('سوبر ماركت', 'مربى مشمش — 350 جم'), ('سوبر ماركت', 'عسل نحل — 500 جم'), ('سوبر ماركت', 'حلاوة طحينية — 250 جم'), ('سوبر ماركت', 'طحينة — 250 جم'), ('سوبر ماركت', 'زبدة فول سوداني — 300 جم'), ('سوبر ماركت', 'كاتشب — عبوة'), ('سوبر ماركت', 'مايونيز — عبوة'), ('سوبر ماركت', 'مستردة — عبوة'), ('سوبر ماركت', 'مخلل مشكل — 500 جم'), ('سوبر ماركت', 'زيتون مخلل — 500 جم'), ('سوبر ماركت', 'بسكويت سادة — عبوة'), ('سوبر ماركت', 'بسكويت محشو — عبوة'), ('سوبر ماركت', 'شوكولاتة — قطعة'), ('سوبر ماركت', 'رقائق بطاطس — كيس'), ('سوبر ماركت', 'مياه معدنية — 1.5 لتر'), ('سوبر ماركت', 'مياه معدنية — 600 مل'), ('سوبر ماركت', 'عصير برتقال — 1 لتر'), ('سوبر ماركت', 'عصير مانجو — 1 لتر'), ('سوبر ماركت', 'مشروب غازي — 1 لتر'), ('سوبر ماركت', 'مناديل ورقية — علبة'), ('سوبر ماركت', 'مناديل مطبخ — رول'), ('سوبر ماركت', 'ورق تواليت — عبوة'), ('سوبر ماركت', 'سائل غسيل أطباق — 750 مل'), ('سوبر ماركت', 'مسحوق غسيل — 1 كجم'), ('سوبر ماركت', 'جل غسيل ملابس — 1 لتر'), ('سوبر ماركت', 'منعم ملابس — 1 لتر'), ('سوبر ماركت', 'منظف أرضيات — 1 لتر'), ('سوبر ماركت', 'مبيض ملابس — 1 لتر'), ('سوبر ماركت', 'إسفنجة أطباق — عبوة'), ('سوبر ماركت', 'أكياس قمامة — رول'), ('سوبر ماركت', 'ورق ألومنيوم — رول'), ('سوبر ماركت', 'أكياس حفظ طعام — عبوة'), ('خضار', 'طماطم — 1 كجم'), ('خضار', 'بطاطس — 1 كجم'), ('خضار', 'بصل أحمر — 1 كجم'), ('خضار', 'بصل أبيض — 1 كجم'), ('خضار', 'خيار — 1 كجم'), ('خضار', 'كوسة — 1 كجم'), ('خضار', 'جزر — 1 كجم'), ('خضار', 'باذنجان رومي — 1 كجم'), ('خضار', 'باذنجان عروس — 1 كجم'), ('خضار', 'فلفل أخضر — 1 كجم'), ('خضار', 'فلفل ألوان — 500 جم'), ('خضار', 'فلفل حار — 250 جم'), ('خضار', 'ليمون — 500 جم'), ('خضار', 'ثوم — 250 جم'), ('خضار', 'فاصوليا خضراء — 500 جم'), ('خضار', 'بسلة — 500 جم'), ('خضار', 'بامية — 500 جم'), ('خضار', 'ملوخية — 500 جم'), ('خضار', 'سبانخ — 500 جم'), ('خضار', 'كرنب — ثمرة'), ('خضار', 'قرنبيط — ثمرة'), ('خضار', 'خس — حزمة'), ('خضار', 'جرجير — حزمة'), ('خضار', 'بقدونس — حزمة'), ('خضار', 'كزبرة خضراء — حزمة'), ('خضار', 'شبت — حزمة'), ('خضار', 'نعناع — حزمة'), ('خضار', 'بصل أخضر — حزمة'), ('خضار', 'بطاطا — 1 كجم'), ('خضار', 'بنجر — 500 جم'), ('خضار', 'تفاح — 1 كجم'), ('خضار', 'موز — 1 كجم'), ('خضار', 'برتقال — 1 كجم'), ('خضار', 'يوسفي — 1 كجم'), ('خضار', 'جوافة — 1 كجم'), ('خضار', 'عنب — 1 كجم'), ('خضار', 'مانجو — 1 كجم'), ('خضار', 'فراولة — 500 جم'), ('خضار', 'بلح — 500 جم'), ('خضار', 'بطيخ — ثمرة'), ('لحوم ودواجن', 'لحم بقري مكعبات — 1 كجم'), ('لحوم ودواجن', 'لحم بقري مفروم — 1 كجم'), ('لحوم ودواجن', 'لحم بقري شرائح — 1 كجم'), ('لحوم ودواجن', 'لحم بقري للطبخ — 1 كجم'), ('لحوم ودواجن', 'لحم ضأن — 1 كجم'), ('لحوم ودواجن', 'ريش ضأن — 1 كجم'), ('لحوم ودواجن', 'كبدة بقري — 500 جم'), ('لحوم ودواجن', 'كلاوي بقري — 500 جم'), ('لحوم ودواجن', 'قلب بقري — 500 جم'), ('لحوم ودواجن', 'كفتة — 500 جم'), ('لحوم ودواجن', 'برجر لحم — 500 جم'), ('لحوم ودواجن', 'سجق بلدي — 500 جم'), ('لحوم ودواجن', 'دجاجة كاملة — بالكيلو'), ('لحوم ودواجن', 'صدور دجاج — 1 كجم'), ('لحوم ودواجن', 'أوراك دجاج — 1 كجم'), ('لحوم ودواجن', 'دبابيس دجاج — 1 كجم'), ('لحوم ودواجن', 'أجنحة دجاج — 1 كجم'), ('لحوم ودواجن', 'فيليه دجاج — 1 كجم'), ('لحوم ودواجن', 'شيش طاووق — 500 جم'), ('لحوم ودواجن', 'كبد وقوانص دجاج — 500 جم'), ('لحوم ودواجن', 'بانيه دجاج — 1 كجم'), ('لحوم ودواجن', 'بط — بالكيلو'), ('لحوم ودواجن', 'أرانب — بالكيلو'), ('لحوم ودواجن', 'سمك بلطي — 1 كجم'), ('لحوم ودواجن', 'سمك بوري — 1 كجم'), ('لحوم ودواجن', 'فيليه سمك — 500 جم'), ('لحوم ودواجن', 'جمبري — 500 جم'), ('أدوية', 'قطن طبي — عبوة'), ('أدوية', 'شاش طبي معقم — عبوة'), ('أدوية', 'شاش طبي رول — عبوة'), ('أدوية', 'ضمادات لاصقة — عبوة'), ('أدوية', 'بلاستر طبي — رول'), ('أدوية', 'رباط ضاغط — عبوة'), ('أدوية', 'رباط شاش — عبوة'), ('أدوية', 'كمامات طبية — عبوة'), ('أدوية', 'قفازات طبية — عبوة'), ('أدوية', 'ترمومتر رقمي — جهاز'), ('أدوية', 'كحول طبي 70% — 100 مل'), ('أدوية', 'جل تعقيم اليدين — 100 مل'), ('أدوية', 'مناديل مبللة — عبوة'), ('أدوية', 'فوط صحية — عبوة'), ('أدوية', 'حفاضات أطفال مقاس 1 — عبوة'), ('أدوية', 'حفاضات أطفال مقاس 2 — عبوة'), ('أدوية', 'حفاضات أطفال مقاس 3 — عبوة'), ('أدوية', 'حفاضات أطفال مقاس 4 — عبوة'), ('أدوية', 'حفاضات أطفال مقاس 5 — عبوة'), ('أدوية', 'حفاضات كبار — عبوة'), ('أدوية', 'فرشاة أسنان — قطعة'), ('أدوية', 'معجون أسنان — 75 مل'), ('أدوية', 'خيط تنظيف أسنان — عبوة'), ('أدوية', 'غسول فم — 250 مل'), ('أدوية', 'صابون يدين — قطعة'), ('أدوية', 'شامبو — 250 مل'), ('أدوية', 'شامبو أطفال — 200 مل'), ('أدوية', 'كريم ترطيب — عبوة'), ('أدوية', 'فازلين — عبوة'), ('أدوية', 'واقي شمس — عبوة')]
            for category,name in starter_catalog:
                if not db.execute('SELECT 1 FROM products WHERE name=? AND category=?',(name,category)).fetchone():
                    db.execute('INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview) VALUES (?,?,0,0,0,1,1)',(name,category))
            db.execute("INSERT INTO catalog_imports VALUES ('starter-catalog-v1',?)",(now(),))
        # Verified Egyptian retail pack photographs; sale prices and stock remain owner-controlled.
        if not db.execute("SELECT 1 FROM catalog_imports WHERE key='market-pack-photos-v1'").fetchone():
            market_products = [('سكر أبيض الضحى — 1 كجم', '/product-photo-17964.jpg', 'سكر أبيض — 1 كجم'), ('أرز مصري الضحى — 1 كجم', '/product-photo-17997.jpg', 'أرز مصري — 1 كجم'), ('زيت عباد الشمس عافية — 2.2 لتر', '/product-photo-544731.jpg', None), ('زيت عباد الشمس كريستال — 1.6 لتر', '/product-photo-488095.jpg', None), ('مكرونة مرمرية الملكة — 400 جم', '/product-photo-46905.jpg', 'مكرونة خواتم — 400 جم'), ('صلصة طماطم هارفست — 320 جم', '/product-photo-322974.jpg', None), ('تونة صن شاين إكسبريس قطعة واحدة — 160 جم', '/product-photo-399095.jpg', None), ('ملح كوكس — 400 جم', '/product-photo-641453.jpg', None)]
            for name,image,old_name in market_products:
                existing = db.execute('SELECT id FROM products WHERE name=? AND category=?',(name,'سوبر ماركت')).fetchone()
                if existing:
                    continue
                draft = db.execute("SELECT id FROM products WHERE name=? AND category='سوبر ماركت' AND catalog_preview=1 AND price_pending=1 AND active=0 AND stock=0 AND image=''",(old_name,)).fetchone() if old_name else None
                if draft:
                    db.execute('UPDATE products SET name=?,image=? WHERE id=?',(name,image,draft['id']))
                else:
                    db.execute("INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview,image) VALUES (?,'سوبر ماركت',0,0,0,1,1,?)",(name,image))
            db.execute("INSERT INTO catalog_imports VALUES ('market-pack-photos-v1',?)",(now(),))
        # Import a locally bundled, sourced photo catalog once without changing owner pricing.
        archive=ROOT/'market-catalog.zip'
        if archive.is_file() and not db.execute("SELECT 1 FROM catalog_imports WHERE key='market-photo-catalog-v2'").fetchone():
            with zipfile.ZipFile(archive) as bundle:
                manifest=json.loads(bundle.read('manifest.json'))
                bundled_names=set(bundle.namelist())
            db.execute('CREATE TABLE IF NOT EXISTS catalog_sources (product_id INTEGER PRIMARY KEY REFERENCES products(id), source_url TEXT NOT NULL, image_source_url TEXT NOT NULL)')
            for item in manifest['products']:
                image=item['image']
                if not re.fullmatch(r'/product-photo-[0-9]+[.]jpg',image) or image[1:] not in bundled_names:
                    raise ValueError('Catalog photograph is missing')
                db.execute('INSERT OR IGNORE INTO categories(name,sort_order) VALUES (?,?)',(item['category'],5))
                existing=db.execute('SELECT id FROM products WHERE image=? OR (name=? AND category=?) ORDER BY id LIMIT 1',(image,item['name'],item['category'])).fetchone()
                if existing:
                    pid=existing['id']
                else:
                    pid=db.execute('INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview,image) VALUES (?,?,0,0,0,1,1,?)',(item['name'],item['category'],image)).lastrowid
                db.execute('INSERT OR IGNORE INTO catalog_sources(product_id,source_url,image_source_url) VALUES (?,?,?)',(pid,item['source'],item['image_source']))
            for category,name in manifest['retired_placeholders']:
                db.execute("UPDATE products SET catalog_preview=0 WHERE name=? AND category=? AND catalog_preview=1 AND price_pending=1 AND active=0 AND stock=0 AND price=0 AND image='' AND merchant_id IS NULL",(name,category))
            db.execute("INSERT INTO catalog_imports VALUES ('market-photo-catalog-v2',?)",(now(),))
        health_archive=ROOT/'health-beauty-catalog.zip'
        if health_archive.is_file() and not db.execute("SELECT 1 FROM catalog_imports WHERE key='health-beauty-photos-v1'").fetchone():
            with zipfile.ZipFile(health_archive) as bundle:
                health_manifest=json.loads(bundle.read('manifest.json'))
                photo_names=set(bundle.namelist())
            db.execute("INSERT OR IGNORE INTO categories(name,sort_order) VALUES ('مستحضرات تجميل',7)")
            for image in health_manifest['move_care_images']:
                db.execute("UPDATE products SET category='مستحضرات تجميل' WHERE image=? AND category='أدوية' AND price_pending=1 AND price=0 AND stock=0 AND active=0 AND merchant_id IS NULL",(image,))
            for item in health_manifest['products']:
                image=item['image']
                if not re.fullmatch(r'/product-photo-[0-9]+[.]jpg',image) or image[1:] not in photo_names:
                    raise ValueError('Health catalog photograph is missing')
                existing=db.execute('SELECT id FROM products WHERE image=? OR (name=? AND category=?) ORDER BY id LIMIT 1',(image,item['name'],item['category'])).fetchone()
                pid=existing['id'] if existing else db.execute('INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview,image) VALUES (?,?,0,0,0,1,1,?)',(item['name'],item['category'],image)).lastrowid
                db.execute('INSERT OR IGNORE INTO catalog_sources(product_id,source_url,image_source_url) VALUES (?,?,?)',(pid,item['source'],item['image_source']))
            db.execute("INSERT INTO catalog_imports VALUES ('health-beauty-photos-v1',?)",(now(),))
        expanded_catalog=ROOT/'expanded-market-catalog.json'
        if expanded_catalog.is_file() and not db.execute("SELECT 1 FROM catalog_imports WHERE key='expanded-source-catalog-v1'").fetchone():
            manifest=json.loads(expanded_catalog.read_text())
            db.execute('CREATE INDEX IF NOT EXISTS product_image_lookup ON products(image)')
            db.execute('CREATE INDEX IF NOT EXISTS product_name_category_lookup ON products(name,category)')
            for item in manifest['products']:
                image=item['image']
                parsed=urlparse(image)
                if parsed.scheme!='https' or not parsed.hostname or item['category'] not in ('سوبر ماركت','خضار','أدوية'):
                    raise ValueError('Invalid source catalog item')
                existing=db.execute('SELECT id FROM products WHERE image=? OR (name=? AND category=?) ORDER BY id LIMIT 1',(image,item['name'],item['category'])).fetchone()
                pid=existing['id'] if existing else db.execute('INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview,image) VALUES (?,?,0,0,0,1,1,?)',(item['name'],item['category'],image)).lastrowid
                db.execute('INSERT OR IGNORE INTO catalog_sources(product_id,source_url,image_source_url) VALUES (?,?,?)',(pid,item['source'],item['image_source']))
            db.execute("INSERT INTO catalog_imports VALUES ('expanded-source-catalog-v1',?)",(now(),))
        # Surface household cleaners and spices as browsable sections, and add
        # loose-weight goods missing from the sourced packaged-goods catalog.
        if not db.execute("SELECT 1 FROM catalog_imports WHERE key='everyday-goods-v1'").fetchone():
            for category,position in [('منظفات',8),('عطارة',9)]:
                db.execute('INSERT OR IGNORE INTO categories(name,sort_order) VALUES (?,?)',(category,position))
            cleaner_terms=('منظف','مطهر','كلور','سائل غسيل','مسحوق غسيل','جل غسيل','منعم ملابس','صابون أطباق','سائل أطباق','ملمع','معطر جو','أكياس قمامة','إسفنجة أطباق')
            spice_terms=('كمون','كزبرة جافة','كركم','قرفة','شطة','بابريكا','فلفل أسود','زعتر','يانسون','كركديه','حبهان','حبه البركة','حبة البركة','قرنفل','زنجبيل مطحون','بهارات','سماق')
            for product in db.execute("SELECT id,name FROM products WHERE category='سوبر ماركت' AND catalog_preview=1 AND price_pending=1 AND active=0 AND stock=0 AND merchant_id IS NULL AND image<>''").fetchall():
                name=product['name']
                if any(term in name for term in cleaner_terms):
                    db.execute("UPDATE products SET category='منظفات' WHERE id=?",(product['id'],))
                elif any(term in name for term in spice_terms) and not any(term in name for term in ('جبنة','بسكويت','شاي','شوربة','شيبسي','شوكولاتة','عسل','صوص','كاتشب','فول مدمس')):
                    db.execute("UPDATE products SET category='عطارة' WHERE id=?",(product['id'],))
            loose_goods=[('خضار','ثوم بلدي طازج'),('خضار','ثوم صيني طازج'),('سوبر ماركت','لانشون سادة'),('سوبر ماركت','لانشون فراخ'),('سوبر ماركت','لانشون لحم'),('سوبر ماركت','بسطرمة شرائح بالوزن'),('سوبر ماركت','سلامي شرائح بالوزن'),('سوبر ماركت','جبنة رومي بالوزن'),('سوبر ماركت','جبنة شيدر بالوزن'),('منظفات','سائل غسيل أطباق — 750 مل'),('منظفات','مسحوق غسيل ملابس — 1 كجم'),('منظفات','مطهر أرضيات — 1 لتر'),('منظفات','كلور أبيض — 1 لتر'),('منظفات','منظف زجاج — 500 مل'),('عطارة','كمون مطحون — 50 جم'),('عطارة','فلفل أسود مطحون — 50 جم'),('عطارة','كزبرة ناشفة — 50 جم'),('عطارة','كركم مطحون — 50 جم'),('عطارة','يانسون — 100 جم'),('عطارة','كركديه — 100 جم'),('عطارة','حبة البركة — 100 جم'),('عطارة','زعتر — 100 جم')]
            for category,name in loose_goods:
                db.execute('INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview) SELECT ?,?,0,0,0,1,1 WHERE NOT EXISTS (SELECT 1 FROM products WHERE name=? AND category=?)',(name,category,name,category))
            db.execute("INSERT INTO catalog_imports VALUES ('everyday-goods-v1',?)",(now(),))
        if not db.execute("SELECT 1 FROM catalog_imports WHERE key='branded-deli-photos-v1'").fetchone():
            branded_deli=[
                ('أطياب لانشون بقري بالزيتون — بالوزن','atyab-beef-with-olive-luncheon-by-weight','o/l/olives_1_1.jpg'),
                ('أطياب لانشون بقري بالتوابل — بالوزن','atyab-beef-with-bohar-luncheon-by-weight','b/l/black_pepper_1.jpg'),
                ('الإلهامي لانشون سادة — بالوزن','elleheimy-plain-luncheon-by-weight','2/7/273634_yqnhvzidgal4ylqj.jpg'),
                ('ريتش فوود سلامي — بالوزن','rich-food-salami-by-weight','s/a/salami_2.jpg'),
            ]
            base='https://mcprod.spinneys-egypt.com/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/'
            db.execute('CREATE TABLE IF NOT EXISTS catalog_sources (product_id INTEGER PRIMARY KEY REFERENCES products(id), source_url TEXT NOT NULL, image_source_url TEXT NOT NULL)')
            for name,slug,photo in branded_deli:
                image=base+photo+'?width=250&format=webp'
                existing=db.execute("SELECT id FROM products WHERE name=? AND category='سوبر ماركت'",(name,)).fetchone()
                pid=existing['id'] if existing else db.execute("INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview,image) VALUES (?,'سوبر ماركت',0,0,0,1,1,?)",(name,image)).lastrowid
                db.execute('INSERT OR IGNORE INTO catalog_sources(product_id,source_url,image_source_url) VALUES (?,?,?)',(pid,'https://spinneys-egypt.com/ar/'+slug,image))
            db.execute("INSERT INTO catalog_imports VALUES ('branded-deli-photos-v1',?)",(now(),))
        if not db.execute("SELECT 1 FROM catalog_imports WHERE key='chicken-luncheon-photos-v1'").fetchone():
            spinneys='https://mcprod.spinneys-egypt.com/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/'
            chicken_deli=[
                ('الحسن والحسين لانشون فراخ — بالوزن','https://elhassanwelhussain.com/product/لانشون-فراخ/','https://elhassanwelhussain.com/wp-content/uploads/2020/12/4-scaled.jpg'),
                ('الحسن والحسين لانشون فراخ فاخر — بالوزن','https://elhassanwelhussain.com/product/لانشون-فراخ-فاخر/','https://elhassanwelhussain.com/wp-content/uploads/2020/11/h1414.jpg'),
                ('الحسن والحسين لانشون بقري فاخر — بالوزن','https://elhassanwelhussain.com/product/لانشون-بقري-بيف-فاخر/','https://elhassanwelhussain.com/wp-content/uploads/2020/11/h1212-scaled.jpg'),
                ('الوطنية لانشون دجاج سادة — بالوزن','https://spinneys-egypt.com/ar/306963',spinneys+'3/0/306963.jpg?format=webp'),
                ('الإلهامي لانشون دجاج — بالوزن','https://spinneys-egypt.com/ar/elleheimy-chicken-luncheon-by-weight',spinneys+'c/h/chicken_luncheon_5.jpg?format=webp'),
                ('حلواني لانشون دجاج — بالوزن','https://spinneys-egypt.com/ar/halwani-bros-chicken-luncheon-1-kg',spinneys+'c/h/chicken_luncheon_4.jpg?format=webp'),
            ]
            db.execute('CREATE TABLE IF NOT EXISTS catalog_sources (product_id INTEGER PRIMARY KEY REFERENCES products(id), source_url TEXT NOT NULL, image_source_url TEXT NOT NULL)')
            for name,source,image in chicken_deli:
                existing=db.execute("SELECT id FROM products WHERE name=? AND category='سوبر ماركت'",(name,)).fetchone()
                pid=existing['id'] if existing else db.execute("INSERT INTO products(name,category,price,stock,active,price_pending,catalog_preview,image) VALUES (?,'سوبر ماركت',0,0,0,1,1,?)",(name,image)).lastrowid
                db.execute('INSERT OR IGNORE INTO catalog_sources(product_id,source_url,image_source_url) VALUES (?,?,?)',(pid,source,image))
            db.execute("UPDATE products SET catalog_preview=0 WHERE name IN ('لانشون سادة','لانشون فراخ','لانشون لحم') AND category='سوبر ماركت' AND catalog_preview=1 AND price_pending=1 AND active=0 AND stock=0 AND price=0 AND image='' AND merchant_id IS NULL")
            db.execute("INSERT INTO catalog_imports VALUES ('chicken-luncheon-photos-v1',?)",(now(),))
        if not db.execute("SELECT 1 FROM products").fetchone():
            db.executemany("INSERT INTO products(name,category,price,stock) VALUES (?,?,?,?)", [("منتج تجريبي: أرز 1 كجم", "سوبر ماركت", 40, 20), ("منتج تجريبي: خضار مشكل", "خضار", 35, 15), ("منتج تجريبي: وجبة", "مطاعم", 85, 10)])
        if not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            password = os.environ.get('WALLAHA_ADMIN_PASSWORD', '')
            if not password or len(password) < 10:
                raise RuntimeError('Set WALLAHA_ADMIN_PASSWORD to at least 10 characters before first run')
            create_user(db, 'المسؤول', os.environ.get('WALLAHA_ADMIN_PHONE', 'admin-account'), 'admin', password, os.environ.get('WALLAHA_ADMIN_USERNAME','owner'))

        # A deliberate change to the deployment's admin secret recovers the owner account.
        # Store the baseline privately; ordinary restarts never overwrite an in-app password change.
        db.execute('CREATE TABLE IF NOT EXISTS admin_recovery (id INTEGER PRIMARY KEY CHECK(id=1), config_hash TEXT NOT NULL)')
        configured=os.environ.get('WALLAHA_ADMIN_PASSWORD','')
        if len(configured)>=10:
            fingerprint=hashlib.sha256(configured.encode()).hexdigest()
            previous=db.execute('SELECT config_hash FROM admin_recovery WHERE id=1').fetchone()
            if previous and not hmac.compare_digest(previous['config_hash'],fingerprint):
                owner=db.execute("SELECT id,phone,username FROM users WHERE role='admin' AND username=?",(os.environ.get('WALLAHA_ADMIN_USERNAME','owner'),)).fetchone()
                if owner:
                    salt=secrets.token_hex(16)
                    digest=hashlib.scrypt(configured.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
                    db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,digest,owner['id']))
                    db.execute('DELETE FROM sessions WHERE user_id=?',(owner['id'],))
                    db.execute('DELETE FROM login_attempts WHERE phone IN (?,?)',(owner['phone'],owner['username']))
            db.execute('INSERT INTO admin_recovery VALUES (1,?) ON CONFLICT(id) DO UPDATE SET config_hash=excluded.config_hash',(fingerprint,))


def create_user(db, name, phone, role, password, username=None):
    if not name.strip() or not phone.strip() or len(password) < 10:
        raise ValueError('الاسم والهاتف وكلمة مرور من 10 أحرف على الأقل مطلوبة')
    if username is not None:
        username=str(username).strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,29}', username):
            raise ValueError('اسم المستخدم يبدأ بحرف إنجليزي ويحتوي 3 إلى 30 حرفًا أو رقمًا أو _')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return db.execute('INSERT INTO users(name,phone,role,salt,password_hash,username) VALUES (?,?,?,?,?,?)', (name.strip(), phone.strip(), role, salt, digest, username)).lastrowid


def authenticate(db, phone, password):
    u = db.execute('SELECT * FROM users WHERE phone=? OR username=?', (str(phone).strip(),str(phone).strip().lower())).fetchone()
    if not u: return None
    digest = hashlib.scrypt(str(password).encode(), salt=bytes.fromhex(u['salt']), n=2**14, r=8, p=1).hex()
    return u if hmac.compare_digest(digest, u['password_hash']) else None


def login_allowed(db, phone, remote):
    record = db.execute('SELECT attempts,blocked_until FROM login_attempts WHERE phone=? AND remote=?', (phone,remote)).fetchone()
    return not record or record['attempts'] < 5 or record['blocked_until'] <= int(time.time())


def record_failed_login(db, phone, remote):
    record = db.execute('SELECT attempts,blocked_until FROM login_attempts WHERE phone=? AND remote=?', (phone,remote)).fetchone()
    attempts = (record['attempts'] if record and record['blocked_until'] > int(time.time()) else 0) + 1
    db.execute('INSERT INTO login_attempts(phone,remote,attempts,blocked_until) VALUES (?,?,?,?) ON CONFLICT(phone,remote) DO UPDATE SET attempts=excluded.attempts,blocked_until=excluded.blocked_until', (phone,remote,attempts,int(time.time())+(900 if attempts >= 5 else 60)))


def valid_image(value, max_chars):
    if not isinstance(value,str) or len(value)>max_chars: return False
    try:
        header,payload=value.split(',',1)
        if header not in ('data:image/png;base64','data:image/jpeg;base64','data:image/webp;base64'): return False
        raw=base64.b64decode(payload,validate=True)
        return raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'
    except (ValueError,base64.binascii.Error): return False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def admin_daily_stats(db):
    today = datetime.now(CAIRO).date()
    start = datetime.combine(today, datetime.min.time(), CAIRO).astimezone(timezone.utc).isoformat(timespec='seconds')
    end = datetime.combine(today + timedelta(days=1), datetime.min.time(), CAIRO).astimezone(timezone.utc).isoformat(timespec='seconds')
    customers = rows(db, """SELECT u.id,u.name,u.phone,COUNT(o.id) AS orders_today,
        COALESCE(SUM(o.total),0) AS amount_today
        FROM users u LEFT JOIN orders o ON o.user_id=u.id AND o.created_at>=? AND o.created_at<?
        WHERE u.role='customer' GROUP BY u.id ORDER BY orders_today DESC,u.id""", (start,end))
    drivers = rows(db, """SELECT d.id,d.name,d.area,d.vehicle_type,
        COUNT(DISTINCT CASE WHEN e.id IS NOT NULL THEN o.id END) AS delivered_today,
        COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN o.total ELSE 0 END),0) AS amount_today,
        COALESCE(k.meters,0) AS meters_today
        FROM drivers d LEFT JOIN orders o ON o.driver_id=d.id
        LEFT JOIN events e ON e.order_id=o.id AND e.action='تم التسليم' AND e.at>=? AND e.at<?
        LEFT JOIN driver_distance_daily k ON k.driver_id=d.id AND k.day=?
        GROUP BY d.id ORDER BY delivered_today DESC,d.id""", (start,end,str(today)))
    return {'day':str(today),'customers':customers,'drivers':drivers}


def record_driver_distance(db, driver_id, lat, lon):
    active = db.execute("""SELECT id FROM orders WHERE driver_id=? AND status IN ('assigned','ready','picked_up','on_way') ORDER BY id DESC LIMIT 1""",(driver_id,)).fetchone()
    if not active: return
    oid = active['id']
    stamp = datetime.now(timezone.utc)
    previous = db.execute('SELECT lat,lon,at FROM driver_trip_points WHERE driver_id=? AND order_id=?',(driver_id,oid)).fetchone()
    db.execute('INSERT OR REPLACE INTO driver_trip_points VALUES (?,?,?,?,?)',(driver_id,oid,lat,lon,stamp.isoformat(timespec='seconds')))
    if not previous: return
    elapsed = (stamp-datetime.fromisoformat(previous['at'])).total_seconds()
    a,b = math.radians(previous['lat']),math.radians(lat)
    da,dl=math.radians(lat-previous['lat']),math.radians(lon-previous['lon'])
    meters = 12742000*math.asin(min(1,math.sqrt(math.sin(da/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2)))
    if 5 <= elapsed <= 300 and 20 <= meters <= 5000 and meters/elapsed <= 40:
        day=stamp.astimezone(CAIRO).date().isoformat()
        db.execute('INSERT INTO driver_distance_daily(driver_id,day,meters) VALUES (?,?,?) ON CONFLICT(driver_id,day) DO UPDATE SET meters=meters+excluded.meters',(driver_id,day,meters))


def log(db, oid, action):
    db.execute("INSERT INTO events(order_id,action,at) VALUES (?,?,?)", (oid, action, now()))


def delivery_points(db,data):
    kind=data.get('kind')
    if kind not in ('products','delivery'): raise ValueError('تسعير الكيلومتر متاح للمنتجات وتوصيل الشحنات')
    if kind=='products':
        m=db.execute('SELECT lat,lon FROM merchants WHERE id=? AND active=1',(int(data.get('merchant_id') or 0),)).fetchone()
        if not m: raise ValueError('اختر محلًا متاحًا')
        origin=[m['lat'],m['lon']]
    else: origin=[data.get('pickup_lat'),data.get('pickup_lon')]
    dest=[data.get('latitude'),data.get('longitude')]
    try: points=[float(x) for x in origin+dest]
    except (TypeError,ValueError): raise ValueError('حدد نقطتي الاستلام والتسليم على الخريطة لحساب الكيلومترات')
    if not all(math.isfinite(x) for x in points) or not (-90<=points[0]<=90 and -180<=points[1]<=180 and -90<=points[2]<=90 and -180<=points[3]<=180):
        raise ValueError('إحداثيات غير صحيحة')
    return points


def road_km(points):
    lat,lon,dlat,dlon=points
    url=f'https://router.project-osrm.org/route/v1/driving/{lon},{lat};{dlon},{dlat}?overview=false'
    try:
        with urlopen(url,timeout=12) as response: result=json.loads(response.read(1_000_000))
        meters=float(result['routes'][0]['distance'])
        if result.get('code')!='Ok' or not math.isfinite(meters) or not 0<=meters<=500_000: raise ValueError()
        return round(meters/1000,3)
    except Exception:
        raise ValueError('تعذر حساب طريق الشوارع. حاول مرة أخرى؛ لم تُحسب رسوم بديلة')


def quote_delivery(db,user_id,data):
    rate=db.execute("SELECT value FROM settings WHERE key='per_km_rate'").fetchone()[0]
    if not rate: raise ValueError('لم تُفعّل الإدارة تسعير الكيلومتر بعد')
    points=delivery_points(db,data);km=road_km(points)
    fee=float((Decimal(str(km))*Decimal(rate)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    payload={'user_id':user_id,'kind':data['kind'],'points':points,'km':km,'rate':float(rate),'fee':fee,'expires':int(time.time())+600}
    body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode()
    secret=db.execute("SELECT value FROM settings WHERE key='quote_secret'").fetchone()[0]
    signature=hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()
    return {**payload,'quote_token':body+'.'+signature}


def verify_delivery_quote(db,user_id,data):
    try:
        body,signature=str(data.get('delivery_quote','')).split('.')
        secret=db.execute("SELECT value FROM settings WHERE key='quote_secret'").fetchone()[0]
        if not hmac.compare_digest(signature,hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()): raise ValueError()
        q=json.loads(base64.urlsafe_b64decode(body))
        if q['user_id']!=user_id or q['kind']!=data['kind'] or q['expires']<int(time.time()) or q['points']!=delivery_points(db,data): raise ValueError()
        return q
    except (ValueError,KeyError,TypeError):
        raise ValueError('احسب رسوم التوصيل مجددًا بعد تحديد الموقع؛ عرض السعر غير صالح أو انتهت صلاحيته')


def assign(db, oid):
    # Serialize selection and reservation so two concurrent orders cannot claim one driver.
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o or o['status'] not in ('new','awaiting_driver','offered') or o["payment_status"] != "confirmed" or (o['kind']!='products' and not o['quote_accepted']) or o['medicine_review']:
        return
    merchant = db.execute('SELECT area FROM merchants WHERE id=?',(o['merchant_id'],)).fetchone() if o['merchant_id'] else None
    pickup_area = merchant['area'] if merchant else o['area']
    # Last known location must be recent. Busy and declined drivers are excluded.
    candidates = db.execute("""SELECT d.id,d.lat,d.lon,d.vehicle_type FROM drivers d WHERE d.available=1 AND d.area=?
        AND d.lat IS NOT NULL AND d.lon IS NOT NULL AND d.location_at>=?
        AND NOT EXISTS (SELECT 1 FROM orders x WHERE x.driver_id=d.id AND x.id<>?
            AND x.status IN ('offered','assigned','ready','picked_up','on_way'))
        AND NOT EXISTS (SELECT 1 FROM order_declines x WHERE x.order_id=? AND x.driver_id=d.id)""",
        (pickup_area,datetime.fromtimestamp(time.time()-300,timezone.utc).isoformat(timespec='seconds'),oid,oid)).fetchall()
    if o['kind']=='ride':
        candidates=[d for d in candidates if d['vehicle_type']==o['vehicle']]
    origin=(o['pickup_lat'],o['pickup_lon']) if o['pickup_lat'] is not None else (o['latitude'],o['longitude'])
    if origin[0] is None or origin[1] is None:
        db.execute("UPDATE orders SET driver_id=NULL,status='awaiting_driver',offer_until=NULL WHERE id=?",(oid,))
        if o['status']!='awaiting_driver': log(db,oid,'بانتظار تحديد موقع الاستلام لتوزيع الطلب')
        return
    def distance(d):
        a,b=map(math.radians,(origin[0],d['lat']))
        da=math.radians(d['lat']-origin[0]);dl=math.radians(d['lon']-origin[1])
        return 6371*2*math.asin(min(1,math.sqrt(math.sin(da/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2)))
    d=min(candidates,key=lambda x:(distance(x),x['id'])) if candidates else None
    if d:
        db.execute("UPDATE orders SET driver_id=?,status='offered',offer_until=? WHERE id=?", (d['id'],int(time.time())+90,oid))
        log(db, oid, "عُرض الطلب تلقائيًا على أقرب مندوب متاح في منطقة الاستلام")
    else:
        db.execute("UPDATE orders SET driver_id=NULL,status='awaiting_driver',offer_until=NULL WHERE id=?", (oid,))
        if o['status']!='awaiting_driver': log(db, oid, "بانتظار مندوب متاح في منطقة الاستلام يشارك موقعًا حديثًا")


def refresh_offers(db):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    for o in db.execute("SELECT id,driver_id FROM orders WHERE status='offered' AND offer_until<?",(int(time.time()),)).fetchall():
        db.execute('INSERT OR IGNORE INTO order_declines VALUES (?,?)',(o['id'],o['driver_id']))
        log(db,o['id'],'انتهت مهلة قبول المندوب؛ يجري البحث عن التالي')
        assign(db,o['id'])
    for o in db.execute("SELECT id FROM orders WHERE status='awaiting_driver'").fetchall():
        assign(db,o['id'])


def product_illustration(name,category):
    title=xml_escape(name);label=xml_escape(name.split('—')[0].strip());size=xml_escape(name.split('—')[-1].strip() if '—' in name else '')
    tone=['#fff0df','#eef6e8','#fcebea','#eaf2fc'][int(hashlib.sha256(name.encode()).hexdigest()[:4],16)%4]
    def has(*terms): return any(t in name for t in terms)
    art=''
    if category=='خضار':
        if has('بقدونس','جرجير','كزبرة','شبت','نعناع','خس','سبانخ','ملوخية','بصل أخضر'):
            art='<path d="M143 155L162 85M157 156L185 100M168 155L132 90" stroke="#50984a" stroke-width="7"/>' + ''.join(f'<ellipse cx="{x}" cy="{y}" rx="22" ry="13" transform="rotate({r} {x} {y})" fill="{c}"/>' for x,y,r,c in [(140,84,-35,'#539b45'),(172,76,30,'#3b813c'),(181,108,-35,'#64aa4d'),(129,110,40,'#70b151'),(155,120,-30,'#448b40')])
        elif has('جزر','بطاطا'):
            art='<path d="M140 75Q175 70 184 92L132 165Q126 170 129 158Z" fill="#eb8a32"/><path d="M147 77L141 49M156 75L165 45M166 77L185 52" stroke="#4a944c" stroke-width="7" stroke-linecap="round"/><path d="M142 111L154 113M133 137L145 139" stroke="#cf6d22" stroke-width="3"/>'
        elif has('خيار','كوسة','باذنجان','موز'):
            color='#683c80' if has('باذنجان') else '#efca4a' if has('موز') else '#55924e'
            art=f'<path d="M130 70C104 90 117 160 154 165Q174 172 184 145C163 145 150 90 158 72Z" fill="{color}"/><path d="M143 76L158 61" stroke="#4b7d3c" stroke-width="8"/>'
        elif has('عنب'):
            art=''.join(f'<circle cx="{x}" cy="{y}" r="16" fill="#825eaa" stroke="#725096" stroke-width="2"/>' for x,y in [(140,87),(167,85),(127,112),(153,111),(179,110),(141,138),(167,137),(154,159)])+'<path d="M158 71Q151 47 179 45" fill="none" stroke="#548149" stroke-width="5"/>'
        else:
            color='#e85c4f' if has('طماطم','تفاح','فراولة') else '#e9aa46' if has('برتقال','يوسفي','مانجو') else '#e3cc64' if has('ليمون','جوافة') else '#d6bda0' if has('بطاطس','بصل','ثوم') else '#5e9b52'
            art=f'<ellipse cx="155" cy="119" rx="51" ry="43" fill="{color}"/><ellipse cx="136" cy="103" rx="10" ry="17" fill="#ffffff" opacity=".18"/><path d="M155 77L160 55" stroke="#55784b" stroke-width="6"/><path d="M158 69Q175 47 186 61Q177 79 158 69" fill="#659652"/>'
    elif category=='لحوم ودواجن':
        if has('سمك','جمبري'):
            art='<path d="M102 117Q148 56 195 118Q152 172 102 117L77 91L77 145Z" fill="#77a8b7"/><path d="M134 89L151 71L159 84M137 146L157 159L161 145" fill="#5b8b9c"/><circle cx="181" cy="112" r="5" fill="#24424c"/><path d="M163 99Q148 118 162 139" fill="none" stroke="#48788a" stroke-width="3"/>'
        elif has('دجاج','دبابيس','دجاجة','أجنحة','بط','أرانب','قوانص','بانيه','شيش'):
            art='<path d="M138 131L176 160" stroke="#ead4b9" stroke-width="16" stroke-linecap="round"/><circle cx="181" cy="164" r="11" fill="#f6e9d8"/><circle cx="178" cy="152" r="10" fill="#f6e9d8"/><path d="M110 77C158 59 181 113 145 142C112 164 78 98 110 77" fill="#dfab7f" stroke="#c98e68" stroke-width="5"/><path d="M114 83Q137 77 151 101" fill="none" stroke="#f4caaa" stroke-width="8" stroke-linecap="round"/>'
        else:
            art='<path d="M114 73C139 62 183 78 193 111C213 144 171 166 142 158C116 164 87 138 92 111Z" fill="#b95f62" stroke="#f0c4b1" stroke-width="9"/><path d="M137 85L154 115L145 145M111 116L156 119L182 142" fill="none" stroke="#f3c5b6" stroke-width="6"/><ellipse cx="166" cy="94" rx="13" ry="9" fill="#f3d9c6"/>'
    elif category=='أدوية':
        if has('ترمومتر'):
            art='<rect x="126" y="65" width="55" height="105" rx="22" fill="#f7fafc" stroke="#91c4d0" stroke-width="5"/><rect x="139" y="82" width="30" height="32" rx="5" fill="#bce1d6"/><text x="154" y="104" text-anchor="middle" font-size="14" fill="#305955">°C</text><circle cx="154" cy="136" r="6" fill="#64a8b9"/>'
        elif has('كمامات'):
            art='<path d="M115 91C79 81 79 150 115 140M197 91C231 81 231 150 197 140" fill="none" stroke="#87b8ce" stroke-width="5"/><rect x="109" y="85" width="91" height="66" rx="12" fill="#97d0e0"/><path d="M121 100H188M121 116H188M121 132H188" stroke="#d1edf3" stroke-width="4"/>'
        elif has('بلاستر','ضمادات','رباط','شاش','قطن'):
            art='<rect x="95" y="77" width="126" height="74" rx="31" transform="rotate(-25 158 114)" fill="#deb68b"/><rect x="135" y="86" width="46" height="57" rx="8" transform="rotate(-25 158 114)" fill="#f2d3ad"/><g fill="#b28a66"><circle cx="112" cy="120" r="3"/><circle cx="124" cy="114" r="3"/><circle cx="194" cy="98" r="3"/><circle cx="207" cy="91" r="3"/></g>'
        elif has('شامبو','كحول','غسول','جل','كريم','فازلين'):
            art='<rect x="131" y="49" width="47" height="25" rx="5" fill="#64a7b9"/><rect x="117" y="71" width="75" height="94" rx="18" fill="#f9fcfc" stroke="#b7dce2" stroke-width="4"/><rect x="119" y="102" width="71" height="39" fill="#91c7ce"/><path d="M154 110V132M143 121H165" stroke="white" stroke-width="5"/>'
        else:
            art='<rect x="107" y="67" width="98" height="102" rx="13" fill="#f8fcff" stroke="#aec9df" stroke-width="4"/><path d="M108 79H204V109H108Z" fill="#8bc7c3"/><path d="M146 126H165M155 117V136" stroke="#62aaa5" stroke-width="5"/><path d="M123 151H184" stroke="#d3e3ef" stroke-width="5"/>'
    elif has('زيت','خل','مياه','عصير','مشروب','سائل','جل غسيل','منعم','منظف','مبيض'):
        color='#efd164' if has('زيت') else '#dba54f' if has('عصير') else '#9cd2e7' if has('مياه') else '#76b7a7'
        art=f'<rect x="139" y="47" width="32" height="20" rx="5" fill="#687f78"/><path d="M139 65H171V77Q190 85 190 103V161Q156 173 120 161V103Q120 84 139 77Z" fill="{color}" stroke="#ffffff" stroke-width="4"/><rect x="122" y="110" width="66" height="33" rx="5" fill="#fffdf5"/><path d="M135 90V105" stroke="#ffffff" stroke-width="5" opacity=".7"/>'
    elif has('لبن','زبادي','جبنة','زبدة'):
        art='<path d="M121 80L146 55H188L201 80V167H112V83Z" fill="#fafcff" stroke="#b4cfdf" stroke-width="4"/><path d="M121 80H201L188 55H146Z" fill="#75b4cd"/><path d="M145 57V79" stroke="#e5f2fa" stroke-width="4"/><path d="M113 111H200V151H113Z" fill="#9dcdda"/><circle cx="156" cy="132" r="12" fill="#fff"/>'
    elif has('بيض'):
        art='<path d="M89 135L105 164H210L227 135Z" fill="#b69a76"/>'+''.join(f'<ellipse cx="{x}" cy="{y}" rx="17" ry="24" fill="{c}" stroke="#e4d4bd" stroke-width="2"/>' for x,y,c in [(119,112,'#f9eee0'),(155,104,'#f3e4cf'),(191,112,'#fff5e8')])
    elif has('تونة','سردين','معلب','ذرة'):
        art='<rect x="111" y="79" width="90" height="77" rx="8" fill="#dc9163"/><ellipse cx="156" cy="80" rx="45" ry="12" fill="#dce4e4" stroke="#a5b3b5" stroke-width="3"/><ellipse cx="156" cy="154" rx="45" ry="10" fill="#c77a50"/><path d="M115 98H197V137H115Z" fill="#fff5df"/><ellipse cx="157" cy="80" rx="12" ry="4" fill="none" stroke="#98a4a6" stroke-width="3"/>'
    elif has('مربى','عسل','حلاوة','طحينة','قهوة','كاكاو','كاتشب','مايونيز','مستردة','مخلل','زيتون'):
        art='<rect x="121" y="63" width="73" height="20" rx="7" fill="#8f6951"/><rect x="115" y="83" width="85" height="80" rx="18" fill="#d7a460"/><rect x="117" y="105" width="81" height="35" rx="4" fill="#fff4d9"/><path d="M128 89V99" stroke="#f1d4a8" stroke-width="5"/>'
    elif has('شوكولاتة','بسكويت','بطاطس'):
        art='<path d="M104 68H204V164H104Z" fill="#bd6959"/><path d="M104 68H204V94H104Z" fill="#f1d6a5"/><rect x="125" y="103" width="58" height="45" rx="5" fill="#755044"/><path d="M145 105V146M164 105V146M127 125H181" stroke="#ad8070" stroke-width="3"/>'
    else:
        color='#d3ae6c' if has('أرز','دقيق','سكر','ملح') else '#bb965a' if has('مكرونة','شعرية','نودلز') else '#91a46f'
        grains=''.join(f'<ellipse cx="{130+(i%5)*12}" cy="{104+(i//5)*12}" rx="3" ry="5" transform="rotate(25 {130+(i%5)*12} {104+(i//5)*12})" fill="#fff4cf"/>' for i in range(15))
        art=f'<path d="M117 62H195L200 163Q156 176 110 163Z" fill="{color}" stroke="#ffffff" stroke-width="3"/><path d="M118 65H194M115 155H198" stroke="#886944" stroke-width="4" opacity=".3"/><rect x="121" y="92" width="68" height="54" rx="13" fill="#80643f" opacity=".25"/>'+grains
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240" role="img"><title>صورة توضيحية: {title}</title><rect width="320" height="240" rx="22" fill="{tone}"/><circle cx="157" cy="105" r="83" fill="white" opacity=".6"/><ellipse cx="157" cy="174" rx="66" ry="9" fill="#473a2f" opacity=".1"/>{art}<text x="160" y="204" text-anchor="middle" direction="rtl" font-family="Tahoma,Arial,sans-serif" font-size="15" font-weight="bold" fill="#544136">{label}</text><text x="160" y="225" text-anchor="middle" direction="rtl" font-family="Tahoma,Arial,sans-serif" font-size="12" fill="#8d796d">{size} · صورة توضيحية</text></svg>'


def preview_catalog(db):
    return rows(db,"SELECT p.id,p.name,p.category,CASE WHEN p.image<>'' THEN p.image ELSE '/product-illustration/' || p.id || '.svg' END AS image FROM products p JOIN categories c ON c.name=p.category WHERE p.catalog_preview=1 AND p.price_pending=1 AND c.active=1 ORDER BY CASE WHEN p.image<>'' AND (p.name LIKE '%لانشون%فراخ%بالوزن' OR p.name LIKE '%لانشون%دجاج%بالوزن') THEN 0 WHEN p.image<>'' AND (p.name LIKE '%لانشون%بالوزن' OR p.name LIKE '%سلامي%بالوزن') THEN 1 WHEN p.name IN ('ثوم بلدي طازج','ثوم صيني طازج') THEN 2 ELSE 3 END,(p.image LIKE '/product-photo-%') DESC,c.sort_order,p.category,p.id")


def rows(db, sql, args=()):
    return [dict(x) for x in db.execute(sql, args)]


def driver_wallet(db, driver_id):
    entries=rows(db,"SELECT id,status,total,payment,cash_collected,cash_settled,driver_earning_cents,driver_earning_paid,created_at FROM orders WHERE driver_id=? AND status='delivered' ORDER BY id DESC",(driver_id,))
    return {'balance':sum(o['driver_earning_cents'] for o in entries if not o['driver_earning_paid'])/100,
            'earned':sum(o['driver_earning_cents'] for o in entries)/100,
            'paid':sum(o['driver_earning_cents'] for o in entries if o['driver_earning_paid'])/100,
            'cash_due':round(sum(o['total'] for o in entries if o['payment']=='cash' and o['cash_collected'] and not o['cash_settled']),2),
            'entries':entries}


class Handler(BaseHTTPRequestHandler):
    def user(self, db):
        header = self.headers.get('Authorization', '')
        if not header.startswith('Bearer '): return None
        digest = hashlib.sha256(header[7:].encode()).hexdigest()
        return db.execute('SELECT u.id,u.name,u.phone,u.role,u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?', (digest, int(time.time()))).fetchone()

    def respond(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > 3_000_000:
            raise ValueError("حجم الطلب كبير")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            try:
                with connect() as db:
                    db.execute('SELECT 1').fetchone()
                return self.respond({'ok':True})
            except sqlite3.Error:
                return self.respond({'ok':False},503)
        if path == '/api/geocode':
            query = parse_qs(urlparse(self.path).query).get('q', [''])[0].strip()
            if len(query) < 3 or len(query) > 150: return self.respond({'error':'اكتب عنوانًا واضحًا داخل البدرشين'},400)
            cache_key = query.casefold()
            with GEOCODE_LOCK:
                entry = GEOCODE_STATE['cache'].get(cache_key)
                if entry and time.monotonic()-entry[0] < 3600: return self.respond({'results':entry[1]})
                if time.monotonic()-GEOCODE_STATE['last'] < 1.2: return self.respond({'error':'انتظر لحظة ثم ابحث مرة أخرى'},429)
                GEOCODE_STATE['last'] = time.monotonic()
            url = 'https://photon.komoot.io/api/?' + urlencode({'q':query,'limit':5,'lang':'ar','countrycode':'EG','bbox':'31.10,29.70,31.50,30.02','lat':'29.8513','lon':'31.2744'})
            try:
                request = Request(url,headers={'User-Agent':'Walla3ha/1.0 (+https://walla3ha.com)','Accept':'application/json'})
                with urlopen(request,timeout=8) as response: payload=json.load(response)
                results=[]
                for feature in payload.get('features',[])[:5]:
                    lon,lat=feature.get('geometry',{}).get('coordinates',[None,None])[:2]
                    if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)) or not (29.70<=lat<=30.02 and 31.10<=lon<=31.50): continue
                    props=feature.get('properties',{})
                    name='، '.join(str(props[k]) for k in ('name','street','housenumber','district','city') if props.get(k))
                    results.append({'lat':lat,'lon':lon,'label':name or query})
                with GEOCODE_LOCK:
                    cache=GEOCODE_STATE['cache']
                    if len(cache)>400: cache.clear()
                    cache[cache_key]=(time.monotonic(),results)
                return self.respond({'results':results})
            except Exception:
                return self.respond({'error':'البحث غير متاح الآن؛ حدد المكان بلمسة على الخريطة أو الصق رابط اللوكيشن'},502)
        if path == '/api/resolve-map-link':
            link=parse_qs(urlparse(self.path).query).get('url',[''])[0].strip()
            parsed=urlparse(link)
            if len(link)>512 or parsed.scheme!='https' or parsed.hostname not in MAP_LINK_HOSTS:
                return self.respond({'error':'الصق رابط Google Maps صحيحًا'},400)
            try:
                request=Request(link,headers={'User-Agent':'Walla3ha/1.0 (+https://walla3ha.com)'})
                with build_opener(SafeMapsRedirect()).open(request,timeout=7) as response:
                    final=response.url
                if urlparse(final).hostname not in MAP_LINK_HOSTS: raise ValueError()
                return self.respond({'url':final})
            except Exception:
                return self.respond({'error':'تعذر قراءة الرابط المختصر؛ افتح اللوكيشن وانسخ الرابط الكامل أو حدد النقطة على الخريطة'},502)
        if path == '/api/maps-config':
            # Maps JavaScript browser keys are public; restrict this key to walla3ha.com
            # and to the Maps JavaScript API in Google Cloud Console.
            return self.respond({'google_maps_key': os.environ.get('WALLAHA_GOOGLE_MAPS_API_KEY','')})
        if path == '/api/catalog':
            with connect() as db:
                return self.respond({
                    'draft_catalog': preview_catalog(db),
                    'areas': AREAS,
                    'area_fees': {x['area']:x['fee'] for x in db.execute('SELECT * FROM area_fees')},
                    'categories': rows(db, 'SELECT * FROM categories WHERE active=1 ORDER BY sort_order,name'),
                    'merchants': rows(db, 'SELECT * FROM merchants WHERE active=1 ORDER BY id DESC'),
                    'products': rows(db, 'SELECT p.* FROM products p JOIN merchants m ON m.id=p.merchant_id JOIN categories c ON c.name=p.category WHERE p.active=1 AND p.stock>0 AND m.active=1 AND c.active=1 ORDER BY p.id DESC'),
                    'services': rows(db, 'SELECT * FROM services WHERE active=1 ORDER BY rowid'),
                })
        if path in ('/', '/customer', '/driver', '/admin'):
            data = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith('/manifest/') and path.endswith('.json'):
            role=path.split('/')[-1][:-5]
            if role not in ('customer','driver','admin'): return self.respond({'error':'غير موجود'},404)
            title={'customer':'ولعه للعميل','driver':'ولعه للمندوب','admin':'ولعه الإدارة'}[role]
            data=json.dumps({'name':title,'short_name':title,'id':'/'+role,'start_url':'/'+role,'scope':'/','display':'standalone','background_color':'#f3f7f5','theme_color':'#093d3a','icons':[{'src':'/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any maskable'},{'src':'/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any maskable'}]},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/manifest+json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            return
        if re.fullmatch(r'/product-photo-[0-9]+[.]jpg',path):
            photo=ROOT/path[1:]
            if photo.is_file():
                data=photo.read_bytes()
            else:
                data=None
                for archive_name in ('market-catalog.zip','health-beauty-catalog.zip'):
                    try:
                        with zipfile.ZipFile(ROOT/archive_name) as bundle:
                            data=bundle.read(path[1:])
                        break
                    except (FileNotFoundError,KeyError,zipfile.BadZipFile):
                        continue
                if data is None:
                    return self.respond({'error':'الصورة غير متاحة'},404)
            self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Cache-Control','public, max-age=86400');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            return
        if re.fullmatch(r'/product-illustration/[0-9]+[.]svg',path):
            pid=int(path.split('/')[-1][:-4])
            with connect() as db:
                p=db.execute('SELECT name,category,image FROM products WHERE id=? AND catalog_preview=1',(pid,)).fetchone()
            if not p: return self.respond({'error':'الصورة غير متاحة'},404)
            if re.fullmatch(r'/product-photo-[0-9]+[.]jpg',p['image'] or '') or (p['image'] or '').startswith('https://'):
                self.send_response(302);self.send_header('Location',p['image']);self.end_headers();return
            image=product_illustration(p['name'],p['category']).encode()
            self.send_response(200);self.send_header('Content-Type','image/svg+xml; charset=utf-8');self.send_header('Cache-Control','public, max-age=3600');self.send_header('Content-Length',str(len(image)));self.end_headers();self.wfile.write(image)
            return
        if path == '/icon.svg':
            data=(ROOT/'icon.svg').read_bytes()
            self.send_response(200);self.send_header('Content-Type','image/svg+xml');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            return
        if path in ('/icon-192.png','/icon-512.png','/sw.js','/maps.js'):
            data=(ROOT/path[1:]).read_bytes()
            mime='application/javascript' if path in ('/sw.js','/maps.js') else 'image/png'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Cache-Control','public, max-age=3600');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            return
        if path != "/api/state":
            return self.respond({"error": "غير موجود"}, 404)
        with connect() as db:
            user = self.user(db)
            if not user: return self.respond({'error':'سجل الدخول أولًا'}, 401)
            refresh_offers(db)
            clause, args = ('', ()) if user['role']=='admin' else ((' WHERE o.user_id=?', (user['id'],)) if user['role']=='customer' else (' WHERE d.user_id=?', (user['id'],)))
            orders = rows(db, "SELECT o.*,d.name AS driver_name,d.lat AS driver_lat,d.lon AS driver_lon,d.location_at AS driver_location_at,m.name AS merchant_name,m.address AS merchant_address FROM orders o LEFT JOIN drivers d ON d.id=o.driver_id LEFT JOIN merchants m ON m.id=o.merchant_id"+clause+" ORDER BY o.id DESC", args)
            for o in orders:
                o["items"] = rows(db, "SELECT product_id,name,quantity,unit_price,unit FROM order_items WHERE order_id=?", (o["id"],))
                o["events"] = rows(db, "SELECT action,at FROM events WHERE order_id=? ORDER BY id", (o["id"],))
                if user['role']!='admin':
                    if user['role']=='customer' and o['status'] not in ('assigned','ready','picked_up','on_way'):
                        o['driver_lat']=o['driver_lon']=o['driver_location_at']=None
                    o['has_proof']=bool(o['proof'])
                    o['has_prescription']=bool(o['prescription'])
                    o.pop('proof', None)
                    o.pop('reference', None)
                    o.pop('prescription', None)
            profile=db.execute('SELECT id,name,phone,area,vehicle_type,available FROM drivers WHERE user_id=?',(user['id'],)).fetchone() if user['role']=='driver' else None
            wallets={str(d['id']):driver_wallet(db,d['id']) for d in db.execute('SELECT id FROM drivers')} if user['role']=='admin' else {}
            self.respond({"draft_catalog":preview_catalog(db) if user['role'] in ('customer','admin') else [],"driver_profile":dict(profile) if profile else None,"driver_wallet":driver_wallet(db,profile['id']) if profile else None,"driver_wallets":wallets,"user":dict(user),"areas": AREAS,"area_fees":{x['area']:x['fee'] for x in db.execute('SELECT * FROM area_fees')} if user['role']!='driver' else {}, "categories": rows(db,"SELECT * FROM categories ORDER BY sort_order,name") if user['role']=='admin' else rows(db,"SELECT * FROM categories WHERE active=1 ORDER BY sort_order,name") if user['role']=='customer' else [], "merchants":rows(db,"SELECT * FROM merchants ORDER BY id DESC") if user['role']=='admin' else rows(db,"SELECT * FROM merchants WHERE active=1 ORDER BY id DESC") if user['role']=='customer' else [], "products": rows(db, "SELECT * FROM products ORDER BY id DESC") if user['role']!='driver' else [], "services":rows(db,"SELECT * FROM services ORDER BY rowid") if user['role']!='driver' else [], "drivers": rows(db, "SELECT d.*,u.username FROM drivers d JOIN users u ON u.id=d.user_id ORDER BY d.id") if user['role']=='admin' else [], "orders": orders, "daily_stats": admin_daily_stats(db) if user["role"]=="admin" else None, "settings": {x["key"]: x["value"] for x in db.execute("SELECT * FROM settings WHERE key<>'quote_secret'")} if user['role']!='driver' else {}})

    def do_POST(self):
        try:
            data = self.body()
            with connect() as db:
                path = urlparse(self.path).path
                if path == '/api/register':
                    uid=create_user(db,str(data['name']),str(data['phone']),'customer',str(data['password']))
                    return self.respond({'ok':True,'id':uid})
                if path in ('/api/login','/api/admin/login'):
                    phone=str(data.get('phone','')).strip()[:64]
                    remote=self.client_address[0]
                    if not login_allowed(db,phone,remote): return self.respond({'error':'محاولات دخول كثيرة. حاول لاحقًا'},429)
                    u=authenticate(db,phone,data.get('password',''))
                    if not u:
                        record_failed_login(db,phone,remote)
                        db.commit()
                        return self.respond({'error':'بيانات الدخول غير صحيحة'},401)
                    if path == '/api/admin/login' and u['role'] != 'admin':
                        return self.respond({'error':'لوحة التحكم خاصة بالمسؤول فقط'},403)
                    db.execute('DELETE FROM login_attempts WHERE phone=? AND remote=?',(phone,remote))
                    token=secrets.token_urlsafe(32)
                    db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],int(time.time())+86400*7))
                    return self.respond({'token':token,'role':u['role']})
                user=self.user(db)
                if not user: return self.respond({'error':'سجل الدخول أولًا'},401)
                if path == '/api/order/chat':
                    oid=int(data['order_id'])
                    o=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                    d=db.execute('SELECT user_id FROM drivers WHERE id=?',(o['driver_id'],)).fetchone() if o and o['driver_id'] else None
                    is_customer=bool(o and user['role']=='customer' and o['user_id']==user['id'])
                    is_driver=bool(d and user['role']=='driver' and d['user_id']==user['id'])
                    if not o or not d or not (is_customer or is_driver) or o['status'] not in ('assigned','ready','picked_up','on_way','delivered','cancelled'):
                        return self.respond({'error':'الدردشة متاحة لصاحب الطلب والمندوب الذي قبله فقط'},403)
                    mode=data.get('mode','list')
                    if mode not in ('list','send'): raise ValueError('إجراء دردشة غير معروف')
                    if mode=='send':
                        db.execute('BEGIN IMMEDIATE')
                        # Check assignment again under the write lock before recording a private message.
                        current=db.execute('SELECT driver_id,status FROM orders WHERE id=?',(oid,)).fetchone()
                        if current['driver_id']!=o['driver_id'] or current['status'] not in ('assigned','ready','picked_up','on_way'):
                            raise ValueError('انتهى الطلب أو تغيّر المندوب؛ لا يمكن إرسال رسالة الآن')
                        body=str(data.get('body','')).strip()
                        request_id=str(data.get('request_id',''))
                        if not body or len(body)>1000: raise ValueError('الرسالة من حرف إلى 1000 حرف')
                        if not re.fullmatch(r'[a-zA-Z0-9_-]{10,100}',request_id): raise ValueError('معرف الرسالة غير صالح')
                        previous=db.execute('SELECT order_id,driver_id,body FROM order_messages WHERE sender_id=? AND request_id=?',(user['id'],request_id)).fetchone()
                        if previous:
                            if previous['order_id']!=oid or previous['driver_id']!=o['driver_id'] or previous['body']!=body: raise ValueError('معرف الرسالة مستخدم لرسالة أخرى')
                        else:
                            cutoff=datetime.fromtimestamp(time.time()-60,timezone.utc).isoformat(timespec='seconds')
                            if db.execute('SELECT COUNT(*) FROM order_messages WHERE sender_id=? AND at>=?',(user['id'],cutoff)).fetchone()[0]>=30:
                                return self.respond({'error':'رسائل كثيرة؛ انتظر قليلًا ثم أرسل'},429)
                            db.execute('INSERT INTO order_messages(order_id,driver_id,sender_id,request_id,body,at) VALUES (?,?,?,?,?,?)',(oid,o['driver_id'],user['id'],request_id,body,now()))
                    messages=rows(db,"SELECT id,sender_id,body,at FROM (SELECT id,sender_id,body,at FROM order_messages WHERE order_id=? AND driver_id=? ORDER BY id DESC LIMIT 200) ORDER BY id",(oid,o['driver_id']))
                    return self.respond({'messages':messages,'driver_id':o['driver_id'],'can_send':o['status'] in ('assigned','ready','picked_up','on_way')})
                if path == '/api/delivery-quote':
                    if user['role']!='customer': return self.respond({'error':'غير مصرح'},403)
                    return self.respond(quote_delivery(db,user['id'],data))
                if path == '/api/logout':
                    db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(self.headers['Authorization'][7:].encode()).hexdigest(),))
                    return self.respond({'ok':True})
                if path == '/api/change-password':
                    current=str(data.get('current_password',''))
                    replacement=str(data.get('new_password',''))
                    if len(replacement)<10: raise ValueError('كلمة المرور الجديدة يجب أن تكون 10 أحرف على الأقل')
                    if not authenticate(db,user['phone'],current): return self.respond({'error':'كلمة المرور الحالية غير صحيحة'},403)
                    salt=secrets.token_hex(16)
                    digest=hashlib.scrypt(replacement.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
                    db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,digest,user['id']))
                    db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?',(user['id'],hashlib.sha256(self.headers['Authorization'][7:].encode()).hexdigest()))
                    return self.respond({'ok':True})
                if path == "/api/product":
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    name = str(data["name"]).strip()
                    category = str(data["category"]).strip()
                    price, stock = float(data["price"]), int(data["stock"])
                    if not name or not db.execute('SELECT 1 FROM categories WHERE name=? AND active=1',(category,)).fetchone() or price < 0 or stock < 0: raise ValueError("بيانات المنتج أو القسم غير صحيحة")
                    merchant_id=int(data.get('merchant_id') or 0)
                    if not db.execute('SELECT 1 FROM merchants WHERE id=? AND category=? AND active=1',(merchant_id,category)).fetchone(): raise ValueError('حدد محلًا نشطًا من نفس القسم')
                    image=str(data.get('image',''))
                    if image and not valid_image(image,1_500_000): raise ValueError('صورة المنتج يجب أن تكون PNG أو JPEG أو WebP وحجمها صغير')
                    db.execute("INSERT INTO products(name,category,price,stock,image,requires_prescription,merchant_id) VALUES (?,?,?,?,?,?,?)", (name, category, price, stock,image,1 if data.get('requires_prescription') and category=='أدوية' else 0,merchant_id))
                elif path == '/api/product/delete':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    cur=db.execute('UPDATE products SET active=0,catalog_preview=0 WHERE id=?',(int(data['id']),))
                    if not cur.rowcount: raise ValueError('المنتج غير موجود')
                elif path == '/api/product/update':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    price,stock=float(data['price']),int(data['stock'])
                    if price<0 or stock<0: raise ValueError('السعر والكمية يجب أن يكونا غير سالبين')
                    old=db.execute('SELECT category FROM products WHERE id=?',(int(data['id']),)).fetchone()
                    if not old: raise ValueError('المنتج غير موجود')
                    merchant_id=int(data.get('merchant_id') or 0)
                    if data.get('active') and not db.execute('SELECT 1 FROM merchants WHERE id=? AND category=? AND active=1',(merchant_id,old['category'])).fetchone(): raise ValueError('حدد محلًا نشطًا من نفس القسم')
                    if data.get('active') and not db.execute('SELECT 1 FROM categories WHERE name=? AND active=1',(old['category'],)).fetchone(): raise ValueError('فعّل القسم أولًا')
                    cur=db.execute('UPDATE products SET price_pending=0,price=?,stock=?,active=?,requires_prescription=?,merchant_id=? WHERE id=?',(price,stock,1 if data.get('active') else 0,1 if data.get('requires_prescription') and old['category']=='أدوية' else 0,merchant_id or None,int(data['id'])))
                    if not cur.rowcount: raise ValueError('المنتج غير موجود')
                elif path == '/api/service':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    key=str(data.get('key',''))
                    cur=db.execute('UPDATE services SET active=? WHERE key=?',(1 if data.get('active') else 0,key))
                    if not cur.rowcount: raise ValueError('خدمة غير معروفة')
                elif path == '/api/category/create':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    name=str(data.get('name','')).strip()
                    if not 2<=len(name)<=50: raise ValueError('اسم القسم من 2 إلى 50 حرفًا')
                    db.execute('INSERT INTO categories(name,sort_order) VALUES (?,COALESCE((SELECT MAX(sort_order)+1 FROM categories),0))',(name,))
                elif path == '/api/merchant/create':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    name=str(data.get('name','')).strip();category=str(data.get('category','')).strip()
                    area=str(data.get('area','')).strip();address=str(data.get('address','')).strip()
                    lat,lon=float(data['lat']),float(data['lon'])
                    if not (2<=len(name)<=70 and 3<=len(address)<=200 and area in AREAS and -90<=lat<=90 and -180<=lon<=180): raise ValueError('بيانات المحل أو موقعه غير صحيحة')
                    if not db.execute('SELECT 1 FROM categories WHERE name=? AND active=1',(category,)).fetchone(): raise ValueError('قسم غير نشط')
                    db.execute('INSERT INTO merchants(name,category,area,address,lat,lon) VALUES (?,?,?,?,?,?)',(name,category,area,address,lat,lon))
                elif path == '/api/merchant/update':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if not db.execute('UPDATE merchants SET active=? WHERE id=?',(int(bool(data.get('active'))),int(data['id']))).rowcount: raise ValueError('المحل غير موجود')
                elif path == '/api/category/update':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    name=str(data.get('name','')).strip()
                    active=bool(data.get('active'))
                    if not active and db.execute('SELECT 1 FROM products WHERE category=? AND active=1 LIMIT 1',(name,)).fetchone():
                        raise ValueError('أخفِ المنتجات الظاهرة في القسم أولًا')
                    if not db.execute('UPDATE categories SET active=? WHERE name=?',(int(active),name)).rowcount: raise ValueError('القسم غير موجود')
                elif path == '/api/service/create':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    name=str(data.get('name','')).strip()
                    if not 3<=len(name)<=60: raise ValueError('اسم الخدمة يجب أن يكون بين 3 و60 حرفًا')
                    db.execute('INSERT INTO services(key,name) VALUES (?,?)',('custom_'+secrets.token_hex(6),name))
                elif path == '/api/delivery-rate':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    rate=str(data.get('per_km_rate','')).strip()
                    if rate:
                        number=float(rate)
                        if not math.isfinite(number) or not 0<number<=10000: raise ValueError('سعر الكيلومتر يجب أن يكون أكبر من صفر وحتى 10000 جنيه')
                        rate=str(Decimal(rate).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
                        if Decimal(rate)<=0: raise ValueError('سعر الكيلومتر صغير جدًا')
                    db.execute("UPDATE settings SET value=? WHERE key='per_km_rate'",(rate,))
                elif path == '/api/products/import-names':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    merchant=db.execute("SELECT id FROM merchants WHERE id=? AND category='سوبر ماركت' AND active=1",(int(data.get('merchant_id') or 0),)).fetchone()
                    if not merchant: raise ValueError('اختر سوبر ماركت نشطًا')
                    names=list(dict.fromkeys(str(data.get('names','')).splitlines()))
                    names=[n.strip() for n in names if n.strip()]
                    if not names or len(names)>1000 or any(len(n)>160 for n in names): raise ValueError('أدخل حتى 1000 اسم، كل منتج في سطر وبحد أقصى 160 حرفًا')
                    count=0
                    for name in names:
                        if db.execute('SELECT 1 FROM products WHERE merchant_id=? AND name=?',(merchant['id'],name)).fetchone(): continue
                        db.execute("INSERT INTO products(name,category,price,stock,active,merchant_id,price_pending) VALUES (?,'سوبر ماركت',0,0,0,?,1)",(name,merchant['id']))
                        count+=1
                    return self.respond({'ok':True,'imported':count})
                elif path == '/api/settings':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    fee=float(data['delivery_fee'])
                    if fee<0: raise ValueError('رسوم التوصيل غير صحيحة')
                    for k,v in [('wallet',str(data['wallet']).strip()),('whatsapp',str(data['whatsapp']).strip()),('delivery_fee',str(fee))]:
                        if not v: raise ValueError('الإعدادات مطلوبة')
                        db.execute('UPDATE settings SET value=? WHERE key=?',(v,k))
                elif path == '/api/area-fee':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    area,fee=data['area'],float(data['fee'])
                    if area not in AREAS or fee<0: raise ValueError('المنطقة أو الرسوم غير صحيحة')
                    db.execute('UPDATE area_fees SET fee=? WHERE area=?',(fee,area))
                elif path == '/api/driver/earning':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    o=db.execute("SELECT * FROM orders WHERE id=? AND status='delivered' AND driver_id IS NOT NULL",(int(data['id']),)).fetchone()
                    if not o: raise ValueError('حدد مشوارًا تم تسليمه')
                    if o['driver_earning_paid']: raise ValueError('تم صرف المستحق بالفعل')
                    if data.get('paid') is True:
                        if o['driver_earning_cents']<=0: raise ValueError('حدد أجر المشوار أولًا')
                        db.execute('UPDATE orders SET driver_earning_paid=1 WHERE id=?',(o['id'],))
                        log(db,o['id'],'سجل المسؤول صرف مستحق الطيار')
                    else:
                        try: amount=Decimal(str(data.get('amount','')))
                        except InvalidOperation: raise ValueError('أجر غير صالح')
                        if not amount.is_finite() or amount<0 or amount>100000 or amount!=amount.quantize(Decimal('0.01')): raise ValueError('اكتب مبلغًا صحيحًا بحد أقصى منزلتين عشريتين')
                        db.execute('UPDATE orders SET driver_earning_cents=? WHERE id=?',(int(amount*100),o['id']))
                        log(db,o['id'],'حدد المسؤول أجر الطيار: '+str(amount)+' جنيه')
                elif path == '/api/driver/availability':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    did=int(data['id'])
                    cur=db.execute('UPDATE drivers SET available=? WHERE id=?',(1 if data.get('available') else 0,did))
                    if not cur.rowcount: raise ValueError('المندوب غير موجود')
                    if not data.get('available'):
                        for offer in db.execute("SELECT id FROM orders WHERE driver_id=? AND status='offered'",(did,)).fetchall():
                            db.execute('INSERT OR IGNORE INTO order_declines VALUES (?,?)',(offer['id'],did))
                            assign(db,offer['id'])
                elif path == '/api/order/reassign':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    oid,did=int(data['id']),int(data['driver_id'])
                    o=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                    d=db.execute('SELECT * FROM drivers WHERE id=?',(did,)).fetchone()
                    if not o or not d or (o['kind']=='ride' and d['vehicle_type']!=o['vehicle']) or o['area']!=d['area'] or not d['available'] or o['payment_status']!='confirmed' or o['status'] not in ('assigned','awaiting_driver','ready') or (o['kind']!='products' and not o['quote_accepted']):
                        raise ValueError('تعذر إسناد الطلب لهذا المندوب')
                    db.execute('UPDATE orders SET driver_id=?,status=? WHERE id=?',(did,'ready' if o['status']=='ready' else 'assigned',oid))
                    log(db,oid,'أعاد المسؤول إسناد الطلب إلى مندوب آخر')
                elif path == "/api/driver":
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if data["area"] not in AREAS: raise ValueError("منطقة غير معروفة")
                    vehicle=str(data.get('vehicle_type','موتوسيكل'))
                    if vehicle not in ('موتوسيكل','عجلة','توك توك','سيارة','ميكروباص'): raise ValueError('نوع المركبة غير معروف')
                    uid=create_user(db,str(data['name']),str(data['phone']),'driver',str(data['password']),data.get('username'))
                    db.execute("INSERT INTO drivers(user_id,name,phone,area,vehicle_type) VALUES (?,?,?,?,?)", (uid,str(data["name"]).strip(), str(data["phone"]).strip(), data["area"],vehicle))
                elif path == "/api/order":
                    if user['role']!='customer': return self.respond({'error':'غير مصرح'},403)
                    request_id=str(data.get('client_request_id','')).strip()
                    if not request_id or len(request_id)>100: raise ValueError('معرف الطلب غير صالح')
                    previous=db.execute('SELECT id FROM orders WHERE user_id=? AND client_request_id=?',(user['id'],request_id)).fetchone()
                    if previous: return self.respond({'ok':True,'id':previous['id'],'duplicate':True})
                    kind = data["kind"]
                    if kind not in ("products", "delivery", "ride", "custom"): raise ValueError("نوع خدمة غير معروف")
                    parcel={}
                    if kind=='delivery':
                        parcel={key:str(data.get(key,'')).strip() for key in ('shipment_type','shipment_other','sender_name','sender_phone','recipient_name','recipient_phone')}
                        if parcel['shipment_type'] not in ('ملابس','كوزماتيكس','بيرفيوم','مواد غذائية وأطعمة','أخرى'): raise ValueError('اختر نوع الشحنة أولًا')
                        if parcel['shipment_type']=='أخرى' and not parcel['shipment_other']: raise ValueError('اكتب وصف نوع الشحنة')
                        if any(not parcel[key] or len(parcel[key])>120 for key in ('sender_name','recipient_name')): raise ValueError('اكتب اسم المرسل والمستلم')
                        if any(not re.fullmatch(r'[+0-9٠-٩ ()-]{7,25}',parcel[key]) for key in ('sender_phone','recipient_phone')): raise ValueError('اكتب رقم تليفون صحيح للمرسل والمستلم')
                    service_key='products' if kind=='products' else 'delivery' if kind=='delivery' else {'توك توك':'ride_tuktuk','موتوسيكل':'ride_motorbike','سيارة':'ride_car','ميكروباص':'ride_microbus','عجلة':'ride_bicycle'}.get(data.get('vehicle'),'') if kind=='ride' else str(data.get('service_key',''))
                    if kind=='custom' and not service_key.startswith('custom_'): raise ValueError('الخدمة غير معروفة')
                    if not db.execute('SELECT 1 FROM services WHERE key=? AND active=1',(service_key,)).fetchone(): raise ValueError('الخدمة غير متاحة حاليًا')
                    if data["area"] not in AREAS: raise ValueError("اختر منطقة الخدمة")
                    payment = data["payment"]
                    if payment not in ("cash", "wallet"): raise ValueError("طريقة دفع غير معروفة")
                    proof = str(data.get("proof", ""))
                    if len(proof) > 2_500_000: raise ValueError("صورة الإثبات كبيرة")
                    customer,phone=user['name'],user['phone']
                    address=str(data.get('address','')).strip()
                    if not address: raise ValueError("العنوان مطلوب")
                    lat,lon=data.get('latitude'),data.get('longitude')
                    if lat is None and lon is None and data.get('address_mode')=='manual':
                        lat=lon=None
                    else:
                        if lat is None or lon is None: raise ValueError('حدد موقع العنوان على الخريطة')
                        lat,lon=float(lat),float(lon)
                        if not (-90<=lat<=90 and -180<=lon<=180): raise ValueError('إحداثيات العنوان غير صحيحة')
                    fee = float(db.execute('SELECT fee FROM area_fees WHERE area=?',(data['area'],)).fetchone()[0])
                    subtotal = 0
                    items = []
                    medicine_review = False
                    requires_prescription = False
                    merchant=None
                    if kind == "products":
                        merchant=db.execute('SELECT * FROM merchants WHERE id=? AND active=1',(int(data.get('merchant_id') or 0),)).fetchone()
                        if not merchant: raise ValueError('اختر محلًا أو صيدلية متاحة')
                        for it in data.get("items", []):
                            qty = float(it["quantity"])
                            p = db.execute("SELECT * FROM products WHERE id=? AND active=1", (it["product_id"],)).fetchone()
                            if not p: raise ValueError('المنتج غير متاح')
                            qty = order_quantity(p, qty)
                            stock_qty = qty / weight_basis(p) if sold_by_weight(p) else qty
                            if any(existing['id'] == p['id'] for existing, _ in items): raise ValueError('منتج مكرر في السلة')
                            if p['merchant_id']!=merchant['id'] or p["stock"] < stock_qty: raise ValueError("اختر منتجات من نفس المحل وبكمية متاحة")
                            items.append((p, qty))
                            medicine_review |= p['category']=='أدوية'
                            requires_prescription |= bool(p['requires_prescription'])
                            subtotal += (p["price"] / weight_basis(p) if sold_by_weight(p) else p["price"]) * qty
                        if not items: raise ValueError("السلة فارغة")
                    prescription=str(data.get('prescription',''))
                    if requires_prescription and not valid_image(prescription,2_500_000): raise ValueError('صورة الوصفة مطلوبة لهذا المنتج')
                    if prescription and not valid_image(prescription,2_500_000): raise ValueError('صورة الوصفة غير صالحة')
                    if payment=='wallet' and kind=='products' and not medicine_review and not valid_image(proof,2_500_000): raise ValueError('صورة إثبات التحويل مطلوبة')
                    if medicine_review and proof: raise ValueError('انتظر مراجعة طلب الأدوية قبل التحويل')
                    if kind == "ride" and data.get("vehicle") not in ("توك توك", "موتوسيكل", "سيارة", "ميكروباص", "عجلة"): raise ValueError("اختر نوع المركبة")
                    if kind in ("ride", "delivery") and not data.get("pickup"): raise ValueError("اكتب مكان الاستلام")
                    if kind in ("ride", "delivery") and not data.get("destination"): raise ValueError("اكتب الوجهة")
                    pickup_lat,pickup_lon=None,None
                    if kind!='products':
                        if data.get('pickup_lat') is None or data.get('pickup_lon') is None: raise ValueError('حدد مكان الاستلام على الخريطة')
                        pickup_lat,pickup_lon=float(data['pickup_lat']),float(data['pickup_lon'])
                        if not (-90<=pickup_lat<=90 and -180<=pickup_lon<=180): raise ValueError('موقع الاستلام غير صحيح')
                    # Service prices require admin review; fee is shown only for catalog orders.
                    if kind != "products": fee = 0
                    km_quote=None
                    rate=db.execute("SELECT value FROM settings WHERE key='per_km_rate'").fetchone()[0]
                    if rate and kind in ('products','delivery'):
                        km_quote=verify_delivery_quote(db,user['id'],data)
                        fee=km_quote['fee']
                    ps = "confirmed" if payment == "cash" else "pending"
                    status = "awaiting_quote" if kind != "products" else ("medicine_review" if medicine_review else ("new" if payment == "cash" else "payment_review"))
                    cur = db.execute("INSERT INTO orders(user_id,client_request_id,kind,customer,phone,area,address,details,vehicle,pickup,destination,payment,proof,reference,prescription,medicine_review,payment_status,status,total,delivery_fee,quote_accepted,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (user['id'],request_id,kind, customer, phone, data["area"], address, str(data.get("details", "")), str(data.get("vehicle", "")), str(data.get("pickup", "")), str(data.get("destination", "")), payment, proof, str(data.get("reference", "")), prescription,1 if medicine_review else 0, ps, status, subtotal+fee, fee,1 if kind=='products' else 0, now()))
                    oid = cur.lastrowid
                    if km_quote:
                        db.execute('UPDATE orders SET route_km=?,km_rate=? WHERE id=?',(km_quote['km'],km_quote['rate'],oid))
                        if kind=='delivery': db.execute("UPDATE orders SET status=?,quote_accepted=1 WHERE id=?",('new' if payment=='cash' else 'payment_review',oid))
                        log(db,oid,f"رسوم الطريق: {km_quote['km']} كم × {km_quote['rate']} ج = {fee} ج")
                    if parcel:
                        db.execute('UPDATE orders SET shipment_type=?,shipment_other=?,sender_name=?,sender_phone=?,recipient_name=?,recipient_phone=? WHERE id=?',tuple(parcel[key] for key in ('shipment_type','shipment_other','sender_name','sender_phone','recipient_name','recipient_phone'))+(oid,))
                    db.execute('UPDATE orders SET service_key=?,latitude=?,longitude=?,merchant_id=?,pickup_lat=?,pickup_lon=? WHERE id=?',(service_key,lat,lon,merchant['id'] if merchant else None,merchant['lat'] if merchant else pickup_lat,merchant['lon'] if merchant else pickup_lon,oid))
                    for p, qty in items:
                        db.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty / weight_basis(p) if sold_by_weight(p) else qty, p["id"]))
                        db.execute("INSERT INTO order_items(order_id,product_id,name,quantity,unit_price,unit,stock_quantity) VALUES (?,?,?,?,?,?,?)", (oid, p["id"], p["name"], qty, p["price"] / weight_basis(p) if sold_by_weight(p) else p["price"], "كجم" if sold_by_weight(p) else "قطعة", qty / weight_basis(p) if sold_by_weight(p) else qty))
                    log(db, oid, "أنشأ العميل الطلب")
                    if status=='new' or (km_quote and kind=='delivery' and payment=='cash'): assign(db,oid)
                    return self.respond({"ok": True, "id": oid})
                elif path == "/api/order/action":
                    oid, action = int(data["id"]), data["action"]
                    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
                    if not o: raise ValueError("الطلب غير موجود")
                    if action=='submit_proof':
                        if user['role']!='customer' or o['user_id']!=user['id']: return self.respond({'error':'غير مصرح'},403)
                    elif action in ('accept_quote','decline_quote'):
                        if user['role']!='customer' or o['user_id']!=user['id']: return self.respond({'error':'غير مصرح'},403)
                    elif action=='customer_cancel':
                        if user['role']!='customer' or o['user_id']!=user['id']: return self.respond({'error':'غير مصرح'},403)
                    elif action in ('accept_offer','decline_offer','picked_up','on_way','delivered'):
                        d=db.execute('SELECT id FROM drivers WHERE user_id=?',(user['id'],)).fetchone()
                        if user['role']!='driver' or not d or o['driver_id']!=d['id']: return self.respond({'error':'غير مصرح'},403)
                    elif user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if action == "confirm_payment" and o["payment_status"] == "pending" and o["proof"] and o["status"] == "payment_review":
                        db.execute("UPDATE orders SET payment_status='confirmed',status='new' WHERE id=?", (oid,))
                        log(db, oid, "أكد المسؤول وصول التحويل")
                        assign(db,oid)
                    elif action == 'approve_order' and o['status']=='new' and o['payment_status']=='confirmed':
                        log(db,oid,'راجع المسؤول الطلب ووافق على توزيعه')
                        assign(db,oid)
                    elif action == 'approve_medicine' and o['status']=='medicine_review' and o['medicine_review']:
                        db.execute("UPDATE orders SET medicine_review=0,status=? WHERE id=?",('payment_review' if o['payment']=='wallet' else 'new',oid))
                        log(db,oid,'راجع المسؤول طلب الأدوية')
                        if o['payment']=='cash': assign(db,oid)
                    elif action == "price" and o["kind"] != "products" and o["status"] in ('awaiting_quote','quote_pending'):
                        amount = float(data["amount"])
                        if amount < 0: raise ValueError("السعر غير صحيح")
                        db.execute("UPDATE orders SET total=?,status='quote_pending',quote_accepted=0 WHERE id=?", (amount, oid))
                        log(db, oid, "حدد المسؤول سعر الخدمة وينتظر موافقة العميل")
                    elif action == 'accept_quote' and o['status']=='quote_pending':
                        db.execute("UPDATE orders SET quote_accepted=1,status=? WHERE id=?",('payment_review' if o['payment']=='wallet' else 'new',oid))
                        log(db,oid,'وافق العميل على السعر')
                        if o['payment']=='cash':
                            db.execute('UPDATE orders SET quote_accepted=1 WHERE id=?',(oid,))
                            assign(db,oid)
                    elif action == 'decline_quote' and o['status']=='quote_pending':
                        db.execute("UPDATE orders SET status='cancelled' WHERE id=?",(oid,))
                        log(db,oid,'رفض العميل السعر')
                    elif action == "submit_proof" and o["status"] == "payment_review" and o["payment"] == "wallet":
                        proof = str(data.get("proof", ""))
                        if not valid_image(proof,2_500_000): raise ValueError("صورة إثبات صالحة مطلوبة")
                        db.execute("UPDATE orders SET proof=?,reference=? WHERE id=?", (proof, str(data.get("reference", "")), oid))
                        log(db, oid, "رفع العميل إثبات التحويل")
                    elif action == "ready" and o["kind"] == "products" and o["status"] == "assigned":
                        db.execute("UPDATE orders SET status='ready' WHERE id=?", (oid,))
                        log(db, oid, "الطلب جاهز للاستلام من مكان المسؤول")
                    elif action=='accept_offer' and o['status']=='offered' and o['offer_until']>=int(time.time()):
                        db.execute("UPDATE orders SET status='assigned',offer_until=NULL WHERE id=?",(oid,))
                        log(db,oid,'قبل المندوب الطلب ويتجه للمحل')
                    elif action=='decline_offer' and o['status']=='offered':
                        db.execute('INSERT OR IGNORE INTO order_declines VALUES (?,?)',(oid,o['driver_id']))
                        log(db,oid,'اعتذر المندوب عن الطلب')
                        assign(db,oid)
                    elif action in ("picked_up", "on_way", "delivered") and o["driver_id"] and o["status"] in ({'picked_up': ('assigned','ready'),'on_way':('picked_up',),'delivered':('on_way',)}[action]):
                        if action=='delivered' and o['payment']=='cash' and data.get('cash_collected') is not True: raise ValueError('أكد تحصيل المبلغ النقدي أولًا')
                        db.execute("UPDATE orders SET status=? WHERE id=?", (action, oid))
                        if action=='delivered' and o['payment']=='cash': db.execute('UPDATE orders SET cash_collected=1 WHERE id=?',(oid,))
                        log(db, oid, {"picked_up": "استلم المندوب الطلب", "on_way": "المندوب في الطريق", "delivered": "تم التسليم"}[action])
                    elif action=='settle_cash' and o['payment']=='cash' and o['status']=='delivered' and o['cash_collected'] and not o['cash_settled']:
                        db.execute('UPDATE orders SET cash_settled=1 WHERE id=?',(oid,))
                        log(db,oid,'أكد المسؤول استلام الكاش من المندوب')
                    elif action in ('cancel','reject_medicine','customer_cancel') and o["status"] not in ("delivered", "cancelled") and (action!='reject_medicine' or o['status']=='medicine_review') and (action!='customer_cancel' or (o['status'] in ('awaiting_quote','quote_pending','medicine_review','payment_review','new','awaiting_driver','offered','assigned') and not (o['payment']=='wallet' and (o['payment_status']=='confirmed' or bool(o['proof']))))):
                        db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
                        for it in db.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)):
                            db.execute("UPDATE products SET stock=stock+? WHERE id=?", (it["stock_quantity"] if it["stock_quantity"] is not None else it["quantity"], it["product_id"]))
                        log(db, oid, "ألغي الطلب وأعيدت المنتجات للكمية المتاحة")
                    else: raise ValueError("الإجراء غير متاح في حالة الطلب الحالية")
                elif path == '/api/location':
                    if user['role']!='driver': return self.respond({'error':'غير مصرح'},403)
                    lat,lon=float(data['lat']),float(data['lon'])
                    if not (-90<=lat<=90 and -180<=lon<=180): raise ValueError('الموقع غير صالح')
                    d=db.execute('SELECT id FROM drivers WHERE user_id=?',(user['id'],)).fetchone()
                    available=db.execute('SELECT available FROM drivers WHERE id=?',(d['id'],)).fetchone() if d else None
                    if not available or not available['available']: return self.respond({'error':'المندوب غير متاح'},403)
                    record_driver_distance(db,d['id'],lat,lon)
                    db.execute('UPDATE drivers SET lat=?,lon=?,location_at=? WHERE id=?',(lat,lon,now(),d['id']))
                    refresh_offers(db)
                else: return self.respond({"error": "غير موجود"}, 404)
            self.respond({"ok": True})
        except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
            self.respond({"error": str(e)}, 400)


if __name__ == "__main__":
    init()
    host=os.environ.get('WALLAHA_BIND','127.0.0.1')
    port=int(os.environ.get('WALLAHA_PORT',os.environ.get('PORT','8080')))
    print(f"Wallaha development server: http://{host}:{port}")
    ThreadingHTTPServer((host,port), Handler).serve_forever()
