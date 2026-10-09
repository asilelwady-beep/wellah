#!/usr/bin/env python3
"""Local, dependency-free prototype for Wallaha. Not a production service."""
import json
import gzip
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
from wallet_controls import init_wallet_controls, wallet_post, unlocked, verify_owner, redact_wallet
from account_support import init_features, feature_post, feature_state, normalized_username, complete_registration

ROOT = Path(__file__).parent
DB = Path(os.environ.get('WALLAHA_DB_PATH', str(ROOT / 'wallaha.sqlite3')))
AREAS = ["مدينة البدرشين", "أبو رجوان البحري", "أبو رجوان القبلي", "أبو صير", "ميت رهينة", "سقارة", "دهشور", "زاوية دهشور", "الشوبك الغربي", "الطرفاية", "المرازيق", "الشنباب", "العزيزية"]
CAIRO = ZoneInfo('Africa/Cairo')
GEOCODE_LOCK = Lock()
GEOCODE_STATE = {'last': 0.0, 'cache': {}}
ROAD_LOCK = Lock()
ROAD_STATE = {'last': 0.0, 'cache': {}}
OSM_SEARCH_LOCK = Lock()
OSM_SEARCH_STATE = {'last': 0.0}
def osm_named_places(query):
    """Fallback for named map features missing from Photon; one bounded user search."""
    words=re.findall(r'[\\w\\u0600-\\u06ff]+',query.casefold())
    stop={'شارع','طريق','مركز','قسم','جامع','مسجد','قرية','مدينه','مدينة','البدرشين','البدراشين','الجيزة','صيدلية','سوبر','ماركت','محل','مدرسة'}
    terms=[w for w in words if len(w)>=3 and w not in stop]
    if not terms: return []
    token=max(terms,key=len)
    with OSM_SEARCH_LOCK:
        if time.monotonic()-OSM_SEARCH_STATE['last']<5: return []
        OSM_SEARCH_STATE['last']=time.monotonic()
    statement='[out:json][timeout:12];nwr[~"^name(:ar)?$"~"%s",i](29.70,31.10,30.02,31.50);out center 60;' % token
    request=Request('https://overpass-api.de/api/interpreter',
                    data=urlencode({'data':statement}).encode(),
                    headers={'User-Agent':'Walla3ha/1.0 (+https://walla3ha.com)',
                             'Content-Type':'application/x-www-form-urlencoded',
                             'Accept':'application/json'})
    with urlopen(request,timeout=16) as response: payload=json.load(response)
    normalized=lambda value: re.sub(r'[\\s\\W_]+','',str(value).casefold().replace('أ','ا').replace('إ','ا').replace('آ','ا').replace('ة','ه').replace('ى','ي'))
    required=[normalized(w) for w in terms]
    results=[]
    seen=set()
    for item in payload.get('elements',[])[:60]:
        tags=item.get('tags') or {}
        names=[str(tags.get(k) or '') for k in ('name:ar','name')]
        if not any(all(term in normalized(name) for term in required) for name in names): continue
        location=item.get('center') or item
        lat,lon=location.get('lat'),location.get('lon')
        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)) or not (29.70<=lat<=30.02 and 31.10<=lon<=31.50): continue
        name=names[0] or names[1]
        key=(round(lat,5),round(lon,5),normalized(name))
        if key in seen: continue
        seen.add(key)
        locality=tags.get('addr:city') or tags.get('addr:suburb') or tags.get('addr:place') or 'البدرشين'
        results.append({'lat':lat,'lon':lon,'label':name+'، '+str(locality)+' — راجع الدبوس عند المدخل'})
        if len(results)>=5: break
    return results

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
        CREATE TABLE IF NOT EXISTS order_offer_timeouts (order_id INTEGER NOT NULL REFERENCES orders(id), driver_id INTEGER NOT NULL REFERENCES drivers(id), retry_after INTEGER NOT NULL, PRIMARY KEY(order_id,driver_id));
        CREATE TABLE IF NOT EXISTS drivers (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE REFERENCES users(id), name TEXT NOT NULL, phone TEXT DEFAULT '', area TEXT NOT NULL, available INTEGER DEFAULT 1, lat REAL, lon REAL, location_at TEXT);
        CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), client_request_id TEXT, kind TEXT NOT NULL, customer TEXT NOT NULL, phone TEXT NOT NULL, area TEXT NOT NULL, address TEXT NOT NULL, details TEXT DEFAULT '', vehicle TEXT DEFAULT '', pickup TEXT DEFAULT '', destination TEXT DEFAULT '', payment TEXT NOT NULL, proof TEXT DEFAULT '', reference TEXT DEFAULT '', prescription TEXT DEFAULT '', medicine_review INTEGER DEFAULT 0, cash_collected INTEGER DEFAULT 0, cash_settled INTEGER DEFAULT 0, payment_status TEXT NOT NULL, status TEXT NOT NULL, total REAL NOT NULL DEFAULT 0, delivery_fee REAL NOT NULL DEFAULT 0, quote_accepted INTEGER DEFAULT 0, driver_id INTEGER REFERENCES drivers(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_items (order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id), name TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS order_messages (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), driver_id INTEGER NOT NULL REFERENCES drivers(id), sender_id INTEGER NOT NULL REFERENCES users(id), request_id TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(sender_id,request_id));
        CREATE INDEX IF NOT EXISTS order_messages_thread ON order_messages(order_id,driver_id,id);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), action TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS driver_trip_points (driver_id INTEGER NOT NULL, order_id INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, at TEXT NOT NULL, PRIMARY KEY(driver_id,order_id));
        CREATE TABLE IF NOT EXISTS driver_distance_daily (driver_id INTEGER NOT NULL, day TEXT NOT NULL, meters REAL NOT NULL DEFAULT 0, PRIMARY KEY(driver_id,day));
        """)
        init_features(db)
        init_wallet_controls(db)
        db.execute('CREATE TABLE IF NOT EXISTS staff_members (user_id INTEGER PRIMARY KEY REFERENCES users(id), permissions TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id))')
        if not db.execute("SELECT 1 FROM settings WHERE key='wallet'").fetchone():
            db.executemany("INSERT INTO settings VALUES (?,?)", [("wallet", os.environ.get('WALLAHA_WALLET','')), ("whatsapp", os.environ.get('WALLAHA_WHATSAPP','')), ("delivery_fee", "20")])
        db.execute("INSERT OR IGNORE INTO settings VALUES ('per_km_rate','')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('quote_secret',?)",(secrets.token_hex(32),))
        for commission_key in ('products','delivery','custom',*RIDE_RATE_KEYS.values()):
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",('commission_'+commission_key,'0'))
        for ride_key in RIDE_RATE_KEYS.values():
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",(ride_key+'_base',''))
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",(ride_key+'_extra',''))
        for table,column,definition in [('orders','route_km','REAL'),('orders','km_rate','REAL'),('products','price_pending','INTEGER NOT NULL DEFAULT 0'),('order_items','unit',"TEXT NOT NULL DEFAULT 'قطعة'"),('order_items','stock_quantity','REAL')]:
            if column not in {x['name'] for x in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        for column,definition in [('driver_earning_cents' ,'INTEGER NOT NULL DEFAULT 0'),('driver_earning_paid','INTEGER NOT NULL DEFAULT 0'),('commission_percent','REAL NOT NULL DEFAULT 0'),('commission_cents','INTEGER NOT NULL DEFAULT 0'),('commission_locked','INTEGER NOT NULL DEFAULT 0')]:
            if column not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
                db.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
        if 'username' not in {x['name'] for x in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN username TEXT')
        db.execute('CREATE TABLE IF NOT EXISTS driver_shifts (id INTEGER PRIMARY KEY,driver_id INTEGER NOT NULL REFERENCES drivers(id),selfie TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT)')
        for table,column,definition in [('drivers','identity_blocked_shift','INTEGER'),('driver_shifts','review_status',"TEXT NOT NULL DEFAULT 'pending'"),('driver_shifts','reviewed_at','TEXT'),('driver_shifts','reviewed_by','INTEGER'),('driver_shifts','restored_at','TEXT')]:
            if column not in {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}: db.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+definition)
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS driver_one_open_shift ON driver_shifts(driver_id) WHERE ended_at IS NULL')
        for column,definition in [('second_selfie','TEXT'),('second_photo_at','TEXT'),('second_review_status',"TEXT NOT NULL DEFAULT 'pending'"),('second_reviewed_at','TEXT')]:
            if column not in {r['name'] for r in db.execute('PRAGMA table_info(driver_shifts)')}:
                db.execute('ALTER TABLE driver_shifts ADD COLUMN '+column+' '+definition)
        db.execute('CREATE TABLE IF NOT EXISTS featured_rewards (id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),kind TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),created_at TEXT NOT NULL,used_order_id INTEGER REFERENCES orders(id))')
        db.execute('CREATE INDEX IF NOT EXISTS featured_rewards_available ON featured_rewards(user_id,kind,used_order_id)')
        db.execute('CREATE INDEX IF NOT EXISTS order_items_order_lookup ON order_items(order_id)')
        db.execute('CREATE INDEX IF NOT EXISTS events_order_lookup ON events(order_id,id)')
        if 'featured' not in {r['name'] for r in db.execute('PRAGMA table_info(drivers)')}:
            db.execute('ALTER TABLE drivers ADD COLUMN featured INTEGER NOT NULL DEFAULT 0')
        if 'featured' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN featured INTEGER NOT NULL DEFAULT 0')
        if 'discount_cents' not in {r['name'] for r in db.execute('PRAGMA table_info(orders)')}:
            db.execute('ALTER TABLE orders ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0')
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('featured_min_order','35')")
        if 'break_until' not in {x['name'] for x in db.execute('PRAGMA table_info(drivers)')}:
            db.execute('ALTER TABLE drivers ADD COLUMN break_until INTEGER NOT NULL DEFAULT 0')
        if 'photo' not in {x['name'] for x in db.execute('PRAGMA table_info(drivers)')}:
            db.execute("ALTER TABLE drivers ADD COLUMN photo TEXT NOT NULL DEFAULT ''")
        if 'vehicle_type' not in {x['name'] for x in db.execute('PRAGMA table_info(drivers)')}:
            db.execute("ALTER TABLE drivers ADD COLUMN vehicle_type TEXT NOT NULL DEFAULT 'موتوسيكل'")
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username) WHERE username IS NOT NULL')
        if not db.execute("SELECT 1 FROM users WHERE username='owner'").fetchone():
            db.execute("UPDATE users SET username='owner' WHERE role='admin' AND username IS NULL")
        db.execute("INSERT OR IGNORE INTO services(key,name) VALUES ('products','المنتجات'),('delivery','توصيل أوردر'),('ride_tuktuk','مشوار توك توك'),('ride_motorbike','مشوار موتوسيكل'),('ride_car','مشوار سيارة'),('ride_microbus','مشوار ميكروباص'),('ride_bicycle','مشوار عجلة'),('ride_scooter','مشوار سكوتر')")
        db.executemany('INSERT OR IGNORE INTO categories(name,sort_order) VALUES (?,?)', [(name,i) for i,name in enumerate(('سوبر ماركت','مطاعم','خضار','أدوية','مخبوزات وعيش','أخرى'))])
        db.execute('INSERT OR IGNORE INTO categories(name,sort_order) SELECT DISTINCT category,100 FROM products')
        if 'quote_accepted' not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
            db.execute('ALTER TABLE orders ADD COLUMN quote_accepted INTEGER DEFAULT 0')
            db.execute("UPDATE orders SET quote_accepted=1 WHERE kind='products' OR status NOT IN ('awaiting_quote','payment_review')")
        for table,column,definition in [('products','requires_prescription','INTEGER DEFAULT 0'),('orders','client_request_id','TEXT'),('orders','prescription',"TEXT DEFAULT ''"),('orders','prescription_only','INTEGER NOT NULL DEFAULT 0'),('orders','medicine_review','INTEGER DEFAULT 0'),('orders','cash_collected','INTEGER DEFAULT 0'),('orders','cash_settled','INTEGER DEFAULT 0')]:
            if column not in {x['name'] for x in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        if 'service_key' not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
            db.execute("ALTER TABLE orders ADD COLUMN service_key TEXT DEFAULT ''")
        for column,definition in [('shop_anywhere','INTEGER NOT NULL DEFAULT 0')]:
            if column not in {x['name'] for x in db.execute('PRAGMA table_info(orders)')}:
                db.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
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
        db.execute('CREATE INDEX IF NOT EXISTS orders_dispatch_lookup ON orders(status,offer_until)')
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
        username=normalized_username(username)
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return db.execute('INSERT INTO users(name,phone,role,salt,password_hash,username) VALUES (?,?,?,?,?,?)', (name.strip(), phone.strip(), role, salt, digest, username)).lastrowid


def authenticate(db, phone, password):
    u = db.execute('SELECT * FROM users WHERE disabled=0 AND (phone=? OR username=? OR email=?)', (str(phone).strip(),str(phone).strip().lower(),str(phone).strip().lower())).fetchone()
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


RIDE_RATE_KEYS = {'عجلة':'ride_bicycle', 'سكوتر':'ride_scooter', 'موتوسيكل':'ride_motorbike', 'توك توك':'ride_tuktuk', 'سيارة':'ride_car', 'ميكروباص':'ride_microbus'}

def delivery_points(db,data):
    kind=data.get('kind')
    if kind not in ('products','delivery','ride'): raise ValueError('نوع الخدمة لا يدعم حساب الطريق')
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


def google_maps_request(url, body, field_mask):
    key = os.environ.get('WALLAHA_GOOGLE_MAPS_SERVER_KEY', '').strip()
    request = Request(url, data=json.dumps(body).encode('utf-8'), headers={
        'Content-Type': 'application/json', 'X-Goog-Api-Key': key,
        'X-Goog-FieldMask': field_mask,
    }, method='POST')
    with urlopen(request, timeout=12) as response:
        return json.loads(response.read(1_000_000))


def road_km(points):
    lat,lon,dlat,dlon=points
    if os.environ.get('WALLAHA_GOOGLE_MAPS_SERVER_KEY', '').strip():
        try:
            result=google_maps_request('https://routes.googleapis.com/directions/v2:computeRoutes', {
                'origin': {'location': {'latLng': {'latitude': lat, 'longitude': lon}}},
                'destination': {'location': {'latLng': {'latitude': dlat, 'longitude': dlon}}},
                'travelMode': 'DRIVE', 'routingPreference': 'TRAFFIC_UNAWARE',
            }, 'routes.distanceMeters')
            meters=float(result['routes'][0]['distanceMeters'])
            if not math.isfinite(meters) or not 0<=meters<=500_000: raise ValueError()
            return round(meters/1000,3)
        except Exception:
            raise ValueError('تعذر حساب الطريق عبر خرائط جوجل. راجع النقطتين وحاول مرة أخرى')
    url=f'https://router.project-osrm.org/route/v1/driving/{lon},{lat};{dlon},{dlat}?overview=false'
    try:
        with urlopen(url,timeout=12) as response: result=json.loads(response.read(1_000_000))
        meters=float(result['routes'][0]['distance'])
        if result.get('code')!='Ok' or not math.isfinite(meters) or not 0<=meters<=500_000: raise ValueError()
        return round(meters/1000,3)
    except Exception:
        raise ValueError('تعذر حساب طريق الشوارع. حاول مرة أخرى؛ لم تُحسب رسوم بديلة')


def quote_delivery(db,user_id,data):
    kind=data.get('kind')
    vehicle=str(data.get('vehicle',''))
    if kind=='ride':
        key=RIDE_RATE_KEYS.get(vehicle)
        if not key or not db.execute('SELECT 1 FROM services WHERE key=? AND active=1',(key,)).fetchone():
            raise ValueError('المركبة غير متاحة')
        prices={r['key']:r['value'] for r in db.execute("SELECT key,value FROM settings WHERE key IN (?,?)",(key+'_base',key+'_extra'))}
        if not prices.get(key+'_base') or not prices.get(key+'_extra'):
            raise ValueError('لم تحدد الإدارة تسعيرة هذه المركبة بعد')
        base=Decimal(prices[key+'_base'])
        extra=Decimal(prices[key+'_extra'])
        rate=extra
    else:
        saved=db.execute("SELECT value FROM settings WHERE key='per_km_rate'").fetchone()[0]
        if not saved: raise ValueError('لم تُفعّل الإدارة تسعير الكيلومتر بعد')
        rate=Decimal(saved)
    points=delivery_points(db,data)
    km=road_km(points)
    if kind=='ride':
        additional=max(0,math.ceil(Decimal(str(km))-Decimal('3')))
        fee=float((base+extra*additional).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    else:
        fee=float((Decimal(str(km))*rate).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    payload={'user_id':user_id,'kind':kind,'points':points,'km':km,'rate':float(rate),'fee':fee,'expires':int(time.time())+600}
    if kind=='ride': payload.update({'vehicle':vehicle,'base':float(base),'extra':float(extra)})
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
        if q['user_id']!=user_id or q['kind']!=data['kind'] or q['expires']<int(time.time()) or q['points']!=delivery_points(db,data) or (q['kind']=='ride' and q.get('vehicle')!=data.get('vehicle')): raise ValueError()
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
    pickup_area = merchant['area'] if merchant and not o['shop_anywhere'] else o['area']
    # Prefer fresh GPS, but keep on-shift drivers eligible by area when location is stale.
    candidates = db.execute("""SELECT d.id,d.lat,d.lon,d.location_at,d.area,d.vehicle_type,d.featured FROM drivers d JOIN users du ON du.id=d.user_id WHERE du.disabled=0 AND d.identity_blocked_shift IS NULL AND d.available=1 AND d.break_until<=? AND (?=1 OR d.area=?)
        AND NOT EXISTS (SELECT 1 FROM orders x WHERE x.driver_id=d.id AND x.id<>?
            AND x.status IN ('offered','assigned','ready','picked_up','on_way'))
        AND NOT EXISTS (SELECT 1 FROM order_declines x WHERE x.order_id=? AND x.driver_id=d.id)
        AND NOT EXISTS (SELECT 1 FROM order_offer_timeouts t WHERE t.order_id=? AND t.driver_id=d.id AND t.retry_after>?)
        AND EXISTS (SELECT 1 FROM driver_shifts s WHERE s.driver_id=d.id AND s.ended_at IS NULL AND s.started_at>=? AND (s.started_at>? OR s.second_selfie IS NOT NULL))""",
        (int(time.time()),1 if o['kind']!='products' else 0,pickup_area,oid,oid,oid,int(time.time()),datetime.fromtimestamp(time.time()-86400,timezone.utc).isoformat(timespec='seconds'),datetime.fromtimestamp(time.time()-21600,timezone.utc).isoformat(timespec='seconds'))).fetchall()
    if o['kind']=='ride':
        candidates=[d for d in candidates if d['vehicle_type']==o['vehicle']]
    origin=(o['pickup_lat'],o['pickup_lon']) if o['pickup_lat'] is not None else (o['latitude'],o['longitude'])
    if origin[0] is None or origin[1] is None:
        db.execute("UPDATE orders SET driver_id=NULL,status='awaiting_driver',offer_until=NULL WHERE id=?",(oid,))
        if o['status']!='awaiting_driver': log(db,oid,'بانتظار تحديد موقع الاستلام لتوزيع الطلب')
        return
    def distance(d):
        if d['lat'] is None or d['lon'] is None: return float('inf')
        a,b=map(math.radians,(origin[0],d['lat']))
        da=math.radians(d['lat']-origin[0]);dl=math.radians(d['lon']-origin[1])
        return 6371*2*math.asin(min(1,math.sqrt(math.sin(da/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2)))
    minimum=float(db.execute("SELECT value FROM settings WHERE key='featured_min_order'").fetchone()[0])
    prioritize=float(o['delivery_fee'] or o['total'])>=minimum
    recent=datetime.fromtimestamp(time.time()-300,timezone.utc).isoformat(timespec='seconds')
    d=min(candidates,key=lambda x:(0 if prioritize and x['featured'] else 1,0 if x['location_at'] and x['location_at']>=recent and x['lat'] is not None and x['lon'] is not None else 1,0 if x['area']==pickup_area else 1,distance(x),x['id'])) if candidates else None
    if d:
        if not o['commission_locked']:
            percent,share,net=commission_split(db,o['kind'],d['vehicle_type'],o['delivery_fee'])
            db.execute('UPDATE orders SET commission_percent=?,commission_cents=?,driver_earning_cents=?,commission_locked=1 WHERE id=?',(percent,share,net,oid))
        db.execute("UPDATE orders SET driver_id=?,status='offered',offer_until=? WHERE id=?", (d['id'],int(time.time())+90,oid))
        log(db, oid, "أولوية الطيار المميز للطلب الأعلى قيمة" if prioritize and d['featured'] else "عُرض الطلب تلقائيًا على أقرب مندوب متاح من نقطة الاستلام")
    else:
        db.execute("UPDATE orders SET driver_id=NULL,status='awaiting_driver',offer_until=NULL WHERE id=?", (oid,))
        if o['status']!='awaiting_driver': log(db, oid, "بانتظار طيار متاح بدأ الشيفت وأكمل صورة التحقق")


def refresh_offers(db):
    if not db.execute("SELECT 1 FROM orders WHERE status='awaiting_driver' OR (status='offered' AND offer_until<?) LIMIT 1", (int(time.time()),)).fetchone():
        return
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    for o in db.execute("SELECT id,driver_id FROM orders WHERE status='offered' AND offer_until<?",(int(time.time()),)).fetchall():
        db.execute('INSERT INTO order_offer_timeouts(order_id,driver_id,retry_after) VALUES (?,?,?) ON CONFLICT(order_id,driver_id) DO UPDATE SET retry_after=excluded.retry_after',(o['id'],o['driver_id'],int(time.time())+30))
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


def warehouse_for_category(db, category):
    """Use the owner's saved pickup point for products from their own stock."""
    existing=db.execute("SELECT id FROM merchants WHERE name='مخزن ولعه' AND category=? AND active=1 ORDER BY id LIMIT 1",(category,)).fetchone()
    if existing: return existing['id']
    settings={r['key']:r['value'] for r in db.execute("SELECT key,value FROM settings WHERE key IN ('warehouse_area','warehouse_address','warehouse_lat','warehouse_lon')")}
    if len(settings)!=4: return None
    cur=db.execute('INSERT INTO merchants(name,category,area,address,lat,lon) VALUES (?,?,?,?,?,?)',('مخزن ولعه',category,settings['warehouse_area'],settings['warehouse_address'],float(settings['warehouse_lat']),float(settings['warehouse_lon'])))
    return cur.lastrowid


def preview_catalog(db):
    return rows(db,"SELECT p.id,p.name,p.category,CASE WHEN p.image<>'' THEN p.image ELSE '/product-illustration/' || p.id || '.svg' END AS image FROM products p JOIN categories c ON c.name=p.category WHERE p.catalog_preview=1 AND p.price_pending=1 AND c.active=1 ORDER BY CASE WHEN p.image<>'' AND (p.name LIKE '%لانشون%فراخ%بالوزن' OR p.name LIKE '%لانشون%دجاج%بالوزن') THEN 0 WHEN p.image<>'' AND (p.name LIKE '%لانشون%بالوزن' OR p.name LIKE '%سلامي%بالوزن') THEN 1 WHEN p.name IN ('ثوم بلدي طازج','ثوم صيني طازج') THEN 2 ELSE 3 END,(p.image LIKE '/product-photo-%') DESC,c.sort_order,p.category,p.id")


def rows(db, sql, args=()):
    return [dict(x) for x in db.execute(sql, args)]


def commission_key(kind, vehicle=''):
    return RIDE_RATE_KEYS.get(vehicle,kind)


def commission_split(db, kind, vehicle, amount):
    key='commission_'+commission_key(kind,vehicle)
    row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    percent=Decimal(row['value'] if row else '0')
    cents=int((Decimal(str(amount))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    share=int((Decimal(cents)*percent/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return float(percent),share,cents-share


def driver_wallet(db, driver_id):
    entries=rows(db,"SELECT id,status,total,payment,cash_collected,cash_settled,driver_earning_cents,driver_earning_paid,commission_percent,commission_cents,commission_settled,created_at FROM orders WHERE driver_id=? AND status='delivered' ORDER BY id DESC",(driver_id,))
    adjustments=rows(db,'SELECT id,amount_cents,reason,at,settled FROM wallet_adjustments WHERE driver_id=? ORDER BY id DESC',(driver_id,))
    audit=rows(db,'SELECT action,details,at FROM wallet_audit WHERE driver_id=? ORDER BY id DESC LIMIT 100',(driver_id,))
    return {'balance':(sum(o['driver_earning_cents'] for o in entries if not o['driver_earning_paid'])+sum(a['amount_cents'] for a in adjustments if not a['settled']))/100,
            'earned':(sum(o['driver_earning_cents'] for o in entries)+sum(a['amount_cents'] for a in adjustments))/100,
            'paid':(sum(o['driver_earning_cents'] for o in entries if o['driver_earning_paid'])+sum(a['amount_cents'] for a in adjustments if a['settled']))/100,
            'commission_due':sum(o['commission_cents'] for o in entries if not o['commission_settled'])/100,
            'cash_due':round(sum(o['total'] for o in entries if o['payment']=='cash' and o['cash_collected'] and not o['cash_settled']),2),
            'entries':entries,'adjustments':adjustments,'audit':audit}

STAFF_SECTIONS = {'admin-overview','admin-orders','admin-driver-list','admin-driver-shifts','admin-map-section','admin-daily','admin-activity','admin-products','admin-merchants','admin-categories','admin-delivery-rate','admin-commission','admin-services','admin-ratings','admin-featured','admin-support'}

def staff_access(user, section):
    if user['role'] != 'admin': return False
    return user['staff_permissions'] is None or section in json.loads(user['staff_permissions'])

def staff_state(view, permissions):
    allowed=set(permissions)
    if not allowed.intersection({'admin-orders','admin-driver-list','admin-map-section','admin-daily','admin-overview'}): view['orders']=[]
    if not allowed.intersection({'admin-driver-list','admin-driver-shifts','admin-map-section','admin-daily','admin-overview'}): view['drivers']=[]
    if not allowed.intersection({'admin-products','admin-categories','admin-overview'}): view['products']=[]
    if not allowed.intersection({'admin-products','admin-merchants','admin-categories'}): view['merchants']=[];view['categories']=[]
    if 'admin-driver-shifts' not in allowed: view['driver_shifts']=[]
    if 'admin-daily' not in allowed: view['daily_distances']=[];view['daily_resets']=[]
    if 'admin-ratings' not in allowed: view['ratings']=[];view['driver_complaints']=[]
    if 'admin-featured' not in allowed: view['featured_people']=[];view['featured_rewards']=[]
    if 'admin-support' not in allowed: view['support_tickets']=[]
    if 'admin-overview' not in allowed and 'admin-daily' not in allowed: view['daily_stats']=None
    if 'admin-products' not in allowed: view['draft_catalog']=[]
    if 'admin-services' not in allowed: view['services']=[]
    if 'admin-delivery-rate' not in allowed: view['area_fees']={}
    if 'admin-orders' not in allowed and 'admin-driver-list' not in allowed: view['chat_threads']=[];view['chat_counts']={}
    view['driver_wallets']={};view['wallet_unlocked']=False
    view['settings']={k:v for k,v in view['settings'].items() if (k.startswith('commission_') and 'admin-commission' in allowed) or (k in ('delivery_fee','per_km_rate') and 'admin-delivery-rate' in allowed)}
    view['staff_permissions']=list(allowed)
    return view


class Handler(BaseHTTPRequestHandler):
    def user(self, db):
        header = self.headers.get('Authorization', '')
        if not header.startswith('Bearer '): return None
        digest = hashlib.sha256(header[7:].encode()).hexdigest()
        return db.execute('SELECT u.id,u.name,u.phone,u.role,u.username,u.email,sm.permissions AS staff_permissions FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN staff_members sm ON sm.user_id=u.id WHERE s.token_hash=? AND s.expires>? AND u.disabled=0', (digest, int(time.time()))).fetchone()

    def respond(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        compressed = len(data) > 2048 and 'gzip' in self.headers.get('Accept-Encoding', '').lower()
        if compressed: data = gzip.compress(data, compresslevel=3)
        self.send_response(code)
        if compressed: self.send_header('Content-Encoding', 'gzip')
        self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Cache-Control', 'no-store')
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
        if path == '/api/map-roads':
            params = parse_qs(urlparse(self.path).query)
            try:
                lat = float(params.get('lat', [''])[0])
                lon = float(params.get('lon', [''])[0])
            except (ValueError, TypeError):
                return self.respond({'error':'حدد منطقة على الخريطة أولاً'},400)
            if not (29.68 <= lat <= 30.04 and 31.08 <= lon <= 31.52):
                return self.respond({'error':'المكان خارج نطاق خريطة البدرشين'},400)
            # A small, explicitly requested viewport keeps the public OSM service load bounded.
            key = (round(lat, 3), round(lon, 3))
            with ROAD_LOCK:
                entry = ROAD_STATE['cache'].get(key)
                if entry and time.monotonic()-entry[0] < 86400:
                    return self.respond({'roads':entry[1], 'source':'OpenStreetMap'})
                if time.monotonic()-ROAD_STATE['last'] < 4:
                    return self.respond({'error':'انتظر ثواني ثم جرّب منطقة أخرى'},429)
                ROAD_STATE['last'] = time.monotonic()
            query = ('[out:json][timeout:15];'
                     '(way(around:900,%.5f,%.5f)[highway~"^(residential|living_street|unclassified|service|pedestrian|footway|tertiary|secondary|primary)$"];);'
                     'out geom 400;') % key
            try:
                request = Request('https://overpass-api.de/api/interpreter',
                                  data=urlencode({'data':query}).encode(),
                                  headers={'User-Agent':'Walla3ha/1.0 (+https://walla3ha.com)',
                                           'Content-Type':'application/x-www-form-urlencoded',
                                           'Accept':'application/json'})
                with urlopen(request,timeout=20) as response:
                    payload=json.load(response)
                roads=[]
                for item in payload.get('elements',[])[:400]:
                    geometry=item.get('geometry') or []
                    coords=[[node['lat'],node['lon']] for node in geometry
                            if isinstance(node.get('lat'),(int,float)) and isinstance(node.get('lon'),(int,float))]
                    if len(coords)<2: continue
                    tags=item.get('tags') or {}
                    roads.append({'id':item.get('id'), 'name':tags.get('name:ar') or tags.get('name') or '',
                                  'points':coords})
                with ROAD_LOCK:
                    cache=ROAD_STATE['cache']
                    if len(cache)>120: cache.clear()
                    cache[key]=(time.monotonic(),roads)
                return self.respond({'roads':roads,'source':'OpenStreetMap'})
            except Exception:
                return self.respond({'error':'تعذر تحميل شوارع المنطقة الآن؛ كبّر الخريطة وحدد النقطة يدويًا'},502)
        if path == '/api/geocode':
            query = parse_qs(urlparse(self.path).query).get('q', [''])[0].strip()
            if len(query) < 3 or len(query) > 150: return self.respond({'error':'اكتب عنوانًا واضحًا داخل البدرشين'},400)
            cache_key = query.casefold()
            with GEOCODE_LOCK:
                entry = GEOCODE_STATE['cache'].get(cache_key)
                if entry and time.monotonic()-entry[0] < (3600 if entry[1] else 30): return self.respond({'results':entry[1]})
                if time.monotonic()-GEOCODE_STATE['last'] < 1.2: return self.respond({'error':'انتظر لحظة ثم ابحث مرة أخرى'},429)
                GEOCODE_STATE['last'] = time.monotonic()
            # Verified Google Maps place listing; keep explicit pin review for moved venues.
            normalized_query=re.sub(r'[\s\W_]+','',query.casefold().replace('أ','ا').replace('إ','ا').replace('آ','ا').replace('ة','ه').replace('ى','ي'))
            if normalized_query in ('مركزشرطهالبدرشين','قسمشرطهالبدرشين','مركزشرطهالبدراشين'):
                return self.respond({'results':[{
                    'lat':29.8469292,'lon':31.2742704,
                    'label':'مركز شرطة البدرشين، مدينة البدراشين، الجيزة — راجع الدبوس عند المدخل',
                }],'source':'Google Maps place listing'})
            google_key=os.environ.get('WALLAHA_GOOGLE_MAPS_SERVER_KEY','').strip()
            try:
                if google_key:
                    payload=google_maps_request('https://places.googleapis.com/v1/places:searchText', {
                        'textQuery':query, 'languageCode':'ar', 'regionCode':'EG',
                        'pageSize':8,
                        'locationRestriction': {'rectangle': {
                            'low': {'latitude':29.70,'longitude':31.10},
                            'high': {'latitude':30.02,'longitude':31.50},
                        }},
                    }, 'places.id,places.displayName,places.formattedAddress,places.location,places.types')
                    results=[]
                    for place in payload.get('places',[]):
                        location=place.get('location') or {}
                        lat,lon=location.get('latitude'),location.get('longitude')
                        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)) or not (29.70<=lat<=30.02 and 31.10<=lon<=31.50): continue
                        title=(place.get('displayName') or {}).get('text','')
                        address=place.get('formattedAddress','')
                        results.append({'lat':lat,'lon':lon,'label':(title+'، '+address).strip('، '),'place_id':place.get('id')})
                else:
                    # A street with a similar name is not a trustworthy match for a named place.
                    url = 'https://photon.komoot.io/api/?' + urlencode({'q':query,'limit':8,'lang':'default','countrycode':'EG','bbox':'31.10,29.70,31.50,30.02','lat':'29.8513','lon':'31.2744'})
                    request = Request(url,headers={'User-Agent':'Walla3ha/1.0 (+https://walla3ha.com)','Accept':'application/json'})
                    with urlopen(request,timeout=8) as response: payload=json.load(response)
                    results=[]
                    for feature in payload.get('features',[])[:8]:
                        lon,lat=feature.get('geometry',{}).get('coordinates',[None,None])[:2]
                        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)) or not (29.70<=lat<=30.02 and 31.10<=lon<=31.50): continue
                        props=feature.get('properties',{})
                        name=str(props.get('name') or '')
                        normalized=lambda value: re.sub(r'[\s\W_]+','',str(value).casefold().replace('أ','ا').replace('إ','ا').replace('آ','ا').replace('ة','ه').replace('ى','ي'))
                        terms=[normalized(word) for word in query.split() if len(normalized(word))>2 and normalized(word) not in ('شارع','طريق','مركز','مدينه')]
                        context=' '.join(str(props.get(k) or '') for k in ('name','district','city','county'))
                        if terms and not all(term in normalized(context) for term in terms): continue
                        if ('شرط' in normalized(query) or 'مستشف' in normalized(query)) and (props.get('osm_key')=='highway' or name.strip().startswith('شارع ')): continue
                        label='، '.join(str(props[k]) for k in ('name','street','housenumber','district','city','county','state') if props.get(k))
                        results.append({'lat':lat,'lon':lon,'label':label or query})
                    results=results[:5]
                    if not results:
                        try: results=osm_named_places(query)
                        except Exception: results=[]
                with GEOCODE_LOCK:
                    cache=GEOCODE_STATE['cache']
                    if len(cache)>400: cache.clear()
                    cache[cache_key]=(time.monotonic(),results)
                return self.respond({'results':results,'source':'Google Maps' if google_key else 'OpenStreetMap'})
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
        if path == '/api/auth-config':
            from account_support import mail_ready, sms_ready
            with connect() as db:
                wa=db.execute("SELECT value FROM settings WHERE key='whatsapp'").fetchone()
                return self.respond({'email_otp_ready':mail_ready(),'sms_otp_ready':sms_ready(),'ai_ready':bool(os.environ.get('OPENAI_API_KEY')),'whatsapp':wa['value'] if wa else ''})
        if path == '/api/maps-config':
            # Maps JavaScript browser keys are public; restrict this key to walla3ha.com
            # and to the Maps JavaScript API in Google Cloud Console.
            with connect() as db:
                saved=db.execute("SELECT value FROM settings WHERE key='google_maps_browser_key'").fetchone()
            return self.respond({'google_maps_key': saved['value'] if saved else os.environ.get('WALLAHA_GOOGLE_MAPS_API_KEY','')})
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
        if path in ('/', '/customer', '/driver', '/driver/settings', '/admin'):
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
        if path in ('/icon-192.png','/icon-512.png','/sw.js','/maps.js','/features.js'):
            data=(ROOT/path[1:]).read_bytes()
            mime='application/javascript' if path in ('/sw.js','/maps.js','/features.js') else 'image/png'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Cache-Control','public, max-age=3600');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            return
        if path != "/api/state":
            return self.respond({"error": "غير موجود"}, 404)
        with connect() as db:
            user = self.user(db)
            if not user: return self.respond({'error':'سجل الدخول أولًا'}, 401)
            refresh_offers(db)
            clause, args = ('', ()) if user['role']=='admin' else ((" WHERE o.user_id=? AND o.status NOT IN ('delivered','cancelled')", (user['id'],)) if user['role']=='customer' else (' WHERE d.user_id=?', (user['id'],)))
            if user['staff_permissions'] is not None and not set(json.loads(user['staff_permissions'])).intersection({'admin-orders','admin-driver-list','admin-map-section','admin-daily','admin-overview'}):
                clause, args = ' WHERE 0', ()
            orders = rows(db, "SELECT o.*,d.name AS driver_name,d.phone AS driver_phone,d.photo AS driver_photo,d.lat AS driver_lat,d.lon AS driver_lon,d.location_at AS driver_location_at,m.name AS merchant_name,m.address AS merchant_address FROM orders o LEFT JOIN drivers d ON d.id=o.driver_id LEFT JOIN merchants m ON m.id=o.merchant_id"+clause+" ORDER BY o.id DESC", args)
            item_groups, event_groups = {}, {}
            order_scope = "SELECT o.id FROM orders o LEFT JOIN drivers d ON d.id=o.driver_id" + clause
            if orders:
                for item in rows(db, "SELECT oi.order_id,oi.product_id,oi.name,oi.quantity,oi.unit_price,oi.unit,COALESCE(NULLIF(p.image,''),CASE WHEN p.catalog_preview=1 THEN '/product-illustration/' || p.id || '.svg' ELSE '/icon.svg' END) AS image FROM order_items oi LEFT JOIN products p ON p.id=oi.product_id WHERE oi.order_id IN ("+order_scope+") ORDER BY oi.rowid", args):
                    item_groups.setdefault(item.pop('order_id'), []).append(item)
                for event in rows(db, "SELECT order_id,action,at FROM events WHERE order_id IN ("+order_scope+") ORDER BY id", args):
                    event_groups.setdefault(event.pop('order_id'), []).append(event)
            for o in orders:
                o["items"] = item_groups.get(o['id'], [])
                o["events"] = event_groups.get(o['id'], [])
                if user['role']!='admin':
                    if user['role']=='customer' and o['status'] not in ('assigned','ready','picked_up','on_way','delivered'):
                        o['driver_lat']=o['driver_lon']=o['driver_location_at']=None
                        o['driver_name']=o['driver_phone']=o['driver_photo']=None
                    o['has_proof']=bool(o['proof'])
                    o['has_prescription']=bool(o['prescription'])
                    o.pop('proof', None)
                    o.pop('reference', None)
                    if user['role']!='driver' or o['status'] not in ('assigned','ready','picked_up','on_way'): o.pop('prescription', None)
                    for key in list(o):
                        if key.startswith('commission_'): o.pop(key)
                    if user['role']=='customer':
                        o.pop('driver_earning_cents',None);o.pop('driver_earning_paid',None)
            profile=db.execute('SELECT id,name,phone,photo,area,vehicle_type,available,break_until,lat,lon,location_at,identity_blocked_shift FROM drivers WHERE user_id=?',(user['id'],)).fetchone() if user['role']=='driver' else None
            wallets={str(d['id']):driver_wallet(db,d['id']) for d in db.execute('SELECT id FROM drivers')} if user['role']=='admin' and unlocked(self,db) else {}
            view={**feature_state(db,user),"draft_catalog":preview_catalog(db) if user['role'] in ('customer','admin') else [],"driver_profile":dict(profile) if profile else None,"wallet_unlocked":unlocked(self,db) if user['role']=='admin' else False,"driver_wallet":redact_wallet(driver_wallet(db,profile['id'])) if profile else None,"driver_wallets":wallets,"user":dict(user),"areas": AREAS,"area_fees":{x['area']:x['fee'] for x in db.execute('SELECT * FROM area_fees')} if user['role']!='driver' else {}, "categories": rows(db,"SELECT * FROM categories ORDER BY sort_order,name") if user['role']=='admin' else rows(db,"SELECT * FROM categories WHERE active=1 ORDER BY sort_order,name") if user['role']=='customer' else [], "merchants":rows(db,"SELECT * FROM merchants ORDER BY id DESC") if user['role']=='admin' else rows(db,"SELECT * FROM merchants WHERE active=1 ORDER BY id DESC") if user['role']=='customer' else [], "products": rows(db, "SELECT * FROM products ORDER BY id DESC") if user['role']!='driver' else [], "services":rows(db,"SELECT * FROM services ORDER BY rowid") if user['role']!='driver' else [], "drivers": rows(db, "SELECT d.*,u.username,u.disabled,u.email FROM drivers d JOIN users u ON u.id=d.user_id ORDER BY d.id") if user['role']=='admin' else [], "orders": orders, "daily_stats": admin_daily_stats(db) if user["role"]=="admin" else None, "settings": {x["key"]: x["value"] for x in db.execute("SELECT * FROM settings WHERE key<>'quote_secret'")} if user['role']!='driver' else {}}
            if user['staff_permissions'] is not None: staff_state(view,json.loads(user['staff_permissions']))
            else: view['staff_permissions']=None
            if user['role']=='admin' and user['staff_permissions'] is None: view['staff_members']=[dict(r) for r in db.execute("SELECT u.id,u.name,u.username,u.disabled,s.permissions FROM staff_members s JOIN users u ON u.id=s.user_id ORDER BY u.id DESC")]
            self.respond(view)

    def do_POST(self):
        try:
            data = self.body()
            with connect() as db:
                path = urlparse(self.path).path
                actor=self.user(db)
                if actor and actor['staff_permissions'] is not None:
                    readable=path=='/api/admin/activity' and staff_access(actor,'admin-activity') or path=='/api/admin/chat/archive' and (staff_access(actor,'admin-orders') or staff_access(actor,'admin-driver-list'))
                    if not readable and path not in ('/api/logout','/api/change-password','/api/login','/api/admin/login'):
                        return self.respond({'error':'حساب الفريق للعرض فقط؛ هذا الإجراء يحتاج المسؤول الرئيسي'},403)
                if wallet_post(self,db,path,data,self.user(db),now,driver_wallet): return
                if feature_post(self, db, path, data, self.user(db), create_user, AREAS, now): return
                if path == '/api/register':
                    uid=complete_registration(db,data,create_user)
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
                    expected_role=data.get('expected_role')
                    if expected_role is not None:
                        if expected_role not in ('customer','driver','admin'):
                            return self.respond({'error':'نوع التطبيق غير صحيح'},400)
                        if u['role'] != expected_role:
                            label={'customer':'عميل','driver':'طيار','admin':'مسؤول'}[expected_role]
                            return self.respond({'error':'هذا التطبيق يحتاج حساب '+label+'؛ الحساب الذي أدخلته من نوع مختلف'},403)
                    db.execute('DELETE FROM login_attempts WHERE phone=? AND remote=?',(phone,remote))
                    token=secrets.token_urlsafe(32)
                    db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],int(time.time())+86400*7))
                    return self.respond({'token':token,'role':u['role']})
                user=self.user(db)
                if not user: return self.respond({'error':'سجل الدخول أولًا'},401)
                if path == '/api/admin/team':
                    if user['role']!='admin' or user['staff_permissions'] is not None: return self.respond({'error':'إدارة الفريق للمسؤول الرئيسي فقط'},403)
                    action=str(data.get('action',''))
                    permissions=json.dumps(sorted(set(data.get('permissions',[])) & STAFF_SECTIONS),ensure_ascii=False)
                    if action=='create':
                        username=normalized_username(str(data.get('username','')))
                        if not json.loads(permissions): raise ValueError('اختر قسمًا واحدًا على الأقل')
                        uid=create_user(db,str(data.get('name','')), 'staff:'+username,'admin',str(data.get('password','')),username)
                        db.execute('INSERT INTO staff_members(user_id,permissions,created_by) VALUES (?,?,?)',(uid,permissions,user['id']))
                    elif action in ('update','disable','enable'):
                        uid=int(data.get('id',0))
                        if not db.execute('SELECT 1 FROM staff_members WHERE user_id=?',(uid,)).fetchone(): raise ValueError('عضو الفريق غير موجود')
                        if action=='update':
                            if not json.loads(permissions): raise ValueError('اختر قسمًا واحدًا على الأقل')
                            db.execute('UPDATE staff_members SET permissions=? WHERE user_id=?',(permissions,uid))
                        else: db.execute('UPDATE users SET disabled=? WHERE id=?',(int(action=='disable'),uid))
                        db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                    else: raise ValueError('إجراء غير معروف')
                    return self.respond({'ok':True})
                if user['role']=='driver' and path not in ('/api/logout','/api/support') and db.execute('SELECT 1 FROM drivers WHERE user_id=? AND identity_blocked_shift IS NOT NULL',(user['id'],)).fetchone():
                    return self.respond({'error':'حسابك موقوف بإنذار مراجعة الهوية. تواصل مع الإدارة لإعادة التفعيل.'},403)
                if path == '/api/admin/shift/review':
                    if user['role']!='admin': return self.respond({'error':'خاص بالمسؤول فقط'},403)
                    db.execute('BEGIN IMMEDIATE')
                    shift=db.execute('SELECT * FROM driver_shifts WHERE id=?',(int(data.get('id',0)),)).fetchone()
                    if not shift: raise ValueError('الشيفت غير موجود')
                    action=data.get('action')
                    if action=='restore':
                        blocked=db.execute('SELECT identity_blocked_shift FROM drivers WHERE id=?',(shift['driver_id'],)).fetchone()['identity_blocked_shift']
                        if blocked!=shift['id']: raise ValueError('هذا الإنذار ليس سبب الإيقاف الحالي')
                        db.execute('UPDATE drivers SET identity_blocked_shift=NULL,available=0 WHERE id=?',(shift['driver_id'],))
                        db.execute('UPDATE driver_shifts SET restored_at=? WHERE id=?',(now(),shift['id']))
                    elif action in ('confirm','warn','confirm_second','warn_second') and (shift['review_status'] if action in ('confirm','warn') else shift['second_review_status'])=='pending' and (action in ('confirm','warn') or shift['second_selfie']):
                        if action in ('confirm','warn'):
                            db.execute('UPDATE driver_shifts SET review_status=?,reviewed_at=?,reviewed_by=? WHERE id=?',('confirmed' if action=='confirm' else 'warned',now(),user['id'],shift['id']))
                        else:
                            db.execute('UPDATE driver_shifts SET second_review_status=?,second_reviewed_at=?,reviewed_by=? WHERE id=?',('confirmed' if action=='confirm_second' else 'warned',now(),user['id'],shift['id']))
                        if action in ('warn','warn_second'):
                            db.execute('UPDATE drivers SET identity_blocked_shift=?,available=0 WHERE id=?',(shift['id'],shift['driver_id']))
                            db.execute('UPDATE driver_shifts SET ended_at=? WHERE driver_id=? AND ended_at IS NULL',(now(),shift['driver_id']))
                            for offered in db.execute("SELECT id FROM orders WHERE driver_id=? AND status='offered'",(shift['driver_id'],)).fetchall():
                                db.execute("UPDATE orders SET driver_id=NULL,status='awaiting_driver',offer_until=NULL WHERE id=?",(offered['id'],));assign(db,offered['id'])
                    else: raise ValueError('تمت مراجعة الصورة بالفعل أو الإجراء غير صحيح')
                    return self.respond({'ok':True})
                if path in ('/api/driver/shift/start','/api/driver/shift/end','/api/driver/shift/second'):
                    if user['role']!='driver': return self.respond({'error':'خاص بالطيار فقط'},403)
                    driver=db.execute('SELECT id FROM drivers WHERE user_id=?',(user['id'],)).fetchone()
                    if not driver: raise ValueError('حساب الطيار غير موجود')
                    did=driver['id']
                    db.execute('BEGIN IMMEDIATE')
                    active=db.execute('SELECT id,started_at,second_selfie FROM driver_shifts WHERE driver_id=? AND ended_at IS NULL',(did,)).fetchone()
                    if path.endswith('/start'):
                        if active and datetime.fromisoformat(active['started_at']).timestamp()+86400>time.time(): return self.respond({'ok':True,'shift_id':active['id']})
                        selfie=data.get('selfie','')
                        if not valid_image(selfie,1_500_000): raise ValueError('صورة وجه حديثة من الكاميرا مطلوبة لبدء الشيفت')
                        if active: db.execute('UPDATE driver_shifts SET ended_at=? WHERE id=?',(now(),active['id']))
                        sid=db.execute('INSERT INTO driver_shifts(driver_id,selfie,started_at) VALUES (?,?,?)',(did,selfie,now())).lastrowid
                        db.execute('UPDATE drivers SET available=1,break_until=0 WHERE id=?',(did,))
                        return self.respond({'ok':True,'shift_id':sid})
                    if path.endswith('/second'):
                        if not active: raise ValueError('ابدأ الشيفت أولًا')
                        elapsed=time.time()-datetime.fromisoformat(active['started_at']).timestamp()
                        if elapsed<21600 or elapsed>=86400: raise ValueError('الصورة الثانية متاحة بعد ٦ ساعات وقبل انتهاء الشيفت')
                        if active['second_selfie']: raise ValueError('الصورة الثانية مسجلة بالفعل')
                        selfie=data.get('selfie','')
                        if not valid_image(selfie,1_500_000): raise ValueError('التقط صورة وجه حديثة بالكاميرا')
                        db.execute('UPDATE driver_shifts SET second_selfie=?,second_photo_at=? WHERE id=?',(selfie,now(),active['id']))
                        return self.respond({'ok':True})
                    if db.execute("SELECT 1 FROM orders WHERE driver_id=? AND status IN ('assigned','ready','picked_up','on_way')",(did,)).fetchone(): raise ValueError('أكمل الطلب الجاري قبل إنهاء الشيفت')
                    db.execute('UPDATE driver_shifts SET ended_at=? WHERE driver_id=? AND ended_at IS NULL',(now(),did))
                    db.execute('UPDATE drivers SET available=0 WHERE id=?',(did,))
                    return self.respond({'ok':True})
                if path == '/api/admin/shift/photo':
                    if user['role']!='admin': return self.respond({'error':'خاص بالمسؤول فقط'},403)
                    shift=db.execute('SELECT selfie,second_selfie FROM driver_shifts WHERE id=?',(int(data.get('id',0)),)).fetchone()
                    if not shift: raise ValueError('الشيفت غير موجود')
                    photo=shift['second_selfie'] if data.get('second') else shift['selfie']
                    if not photo: raise ValueError('لم تُلتقط الصورة الثانية بعد')
                    return self.respond({'photo':photo})
                if path == '/api/admin/featured':
                    if user['role']!='admin': return self.respond({'error':'خاص بالمسؤول فقط'},403)
                    uid=int(data.get('user_id',0))
                    person=db.execute("SELECT id,role FROM users WHERE id=? AND disabled=0 AND role IN ('customer','driver')",(uid,)).fetchone()
                    if not person: raise ValueError('الحساب غير موجود')
                    action=data.get('action')
                    if action=='mark':
                        featured=int(bool(data.get('featured')))
                        db.execute('UPDATE users SET featured=? WHERE id=?',(featured,uid))
                        if person['role']=='driver': db.execute('UPDATE drivers SET featured=? WHERE user_id=?',(featured,uid))
                    elif action in ('discount','bonus'):
                        if (action=='discount' and person['role']!='customer') or (action=='bonus' and person['role']!='driver'): raise ValueError('نوع المكافأة لا يناسب الحساب')
                        amount=Decimal(str(data.get('amount','')))
                        if not amount.is_finite() or not Decimal('0.01')<=amount<=Decimal('10000'): raise ValueError('اكتب مبلغًا من ٠٫٠١ إلى ١٠٠٠٠ جنيه')
                        cents=int((amount*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                        db.execute('INSERT INTO featured_rewards(user_id,kind,amount_cents,created_at) VALUES (?,?,?,?)',(uid,action,cents,now()))
                        if action=='bonus':
                            did=db.execute('SELECT id FROM drivers WHERE user_id=?',(uid,)).fetchone()['id']
                            db.execute('INSERT INTO wallet_adjustments(driver_id,admin_id,amount_cents,reason,at,settled) VALUES (?,?,?,?,?,0)',(did,user['id'],cents,'بونص الطيار المميز',now()))
                    else: raise ValueError('الإجراء غير صحيح')
                    return self.respond({'ok':True})
                if path == '/api/admin/chat/archive':
                    if user['role']!='admin': return self.respond({'error':'خاص بالمسؤول فقط'},403)
                    oid=int(data.get('order_id',0))
                    if not db.execute('SELECT 1 FROM orders WHERE id=?',(oid,)).fetchone(): raise ValueError('الطلب غير موجود')
                    after=max(0,int(data.get('after_id',0)))
                    messages=rows(db,"SELECT m.id,m.body,m.at,m.driver_id,u.name AS sender_name,u.role AS sender_role FROM order_messages m JOIN users u ON u.id=m.sender_id WHERE m.order_id=? AND m.id>? ORDER BY m.id LIMIT 101",(oid,after))
                    more=len(messages)>100
                    messages=messages[:100]
                    return self.respond({'messages':messages,'next_id':messages[-1]['id'] if messages else after,'has_more':more})
                if path == '/api/order/chat':
                    oid=int(data['order_id'])
                    o=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                    d=db.execute('SELECT user_id FROM drivers WHERE id=?',(o['driver_id'],)).fetchone() if o and o['driver_id'] else None
                    is_customer=bool(o and user['role']=='customer' and o['user_id']==user['id'])
                    is_driver=bool(d and user['role']=='driver' and d['user_id']==user['id'])
                    if is_customer and o['status'] in ('delivered','cancelled'):
                        return self.respond({'error':'انتهى الطلب؛ سجل المحادثة محفوظ لدى إدارة ولعه'},403)
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
                    messages=rows(db,"SELECT id,sender_id,body,at FROM (SELECT id,sender_id,body,at FROM order_messages WHERE order_id=? AND (driver_id=? OR ?=1) ORDER BY id DESC LIMIT 200) ORDER BY id",(oid,o['driver_id'],int(is_customer)))
                    return self.respond({'messages':messages,'driver_id':o['driver_id'],'can_send':o['status'] in ('assigned','ready','picked_up','on_way')})
                if path == '/api/delivery-quote':
                    if user['role']!='customer': return self.respond({'error':'غير مصرح'},403)
                    return self.respond(quote_delivery(db,user['id'],data))
                if path == '/api/logout':
                    db.execute('DELETE FROM wallet_unlocks WHERE session_hash=?',(hashlib.sha256(self.headers['Authorization'][7:].encode()).hexdigest(),))
                    db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(self.headers['Authorization'][7:].encode()).hexdigest(),))
                    return self.respond({'ok':True})
                if path == '/api/driver/photo':
                    if user['role']!='driver': return self.respond({'error':'خاص بالطيار فقط'},403)
                    photo=data.get('photo')
                    if not valid_image(photo,1_500_000): raise ValueError('تعذر حفظ الصورة بعد ضغطها؛ اختر صورة أخرى')
                    db.execute('UPDATE drivers SET photo=? WHERE user_id=?',(photo,user['id']))
                    return self.respond({'ok':True})
                if path == '/api/driver/reset-password':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    driver=db.execute('SELECT user_id FROM drivers WHERE id=?',(int(data.get('driver_id',0)),)).fetchone()
                    if not driver: raise ValueError('الطيار غير موجود')
                    temporary=secrets.token_urlsafe(15)
                    salt=secrets.token_hex(16)
                    digest=hashlib.scrypt(temporary.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
                    db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,digest,driver['user_id']))
                    db.execute('DELETE FROM sessions WHERE user_id=?',(driver['user_id'],))
                    return self.respond({'ok':True,'temporary_password':temporary})
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
                    merchant_id=int(data.get('merchant_id') or 0) or warehouse_for_category(db,category)
                    if not db.execute('SELECT 1 FROM merchants WHERE id=? AND category=? AND active=1',(merchant_id,category)).fetchone(): raise ValueError('حدد موقع المخزن مرة واحدة من قسم المحلات، أو اختر محلًا نشطًا من نفس القسم')
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
                    merchant_id=int(data.get('merchant_id') or 0) or (warehouse_for_category(db,old['category']) if data.get('active') else None)
                    if data.get('active') and not db.execute('SELECT 1 FROM merchants WHERE id=? AND category=? AND active=1',(merchant_id,old['category'])).fetchone(): raise ValueError('حدد موقع المخزن مرة واحدة من قسم المحلات، أو اختر محلًا نشطًا من نفس القسم')
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
                elif path == '/api/warehouse/setup':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    area=str(data.get('area','')).strip();address=str(data.get('address','')).strip()
                    lat,lon=float(data['lat']),float(data['lon'])
                    if area not in AREAS or not 3<=len(address)<=200 or not all(math.isfinite(x) for x in (lat,lon)) or not (29.70<=lat<=30.02 and 31.10<=lon<=31.50):
                        raise ValueError('حدد عنوان وموقع المخزن الحقيقي داخل منطقة الخدمة')
                    for key,value in [('warehouse_area',area),('warehouse_address',address),('warehouse_lat',str(lat)),('warehouse_lon',str(lon))]:
                        db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
                    for category in db.execute("SELECT name FROM categories WHERE name<>'مطاعم'").fetchall():
                        name=category['name']
                        existing=db.execute("SELECT id FROM merchants WHERE name='مخزن ولعه' AND category=? ORDER BY id LIMIT 1",(name,)).fetchone()
                        if existing:
                            mid=existing['id']
                            db.execute('UPDATE merchants SET area=?,address=?,lat=?,lon=?,active=1 WHERE id=?',(area,address,lat,lon,mid))
                        else:
                            mid=warehouse_for_category(db,name)
                        db.execute('UPDATE products SET merchant_id=? WHERE category=? AND merchant_id IS NULL',(mid,name))
                    db.execute("UPDATE products SET active=1 WHERE catalog_preview=1 AND price_pending=0 AND price>0 AND stock>0 AND merchant_id IN (SELECT id FROM merchants WHERE active=1) AND category IN (SELECT name FROM categories WHERE active=1)")
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
                elif path == '/api/ride-rates':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    key=RIDE_RATE_KEYS.get(str(data.get('vehicle','')))
                    if not key: raise ValueError('المركبة غير معروفة')
                    base=str(data.get('base','')).strip()
                    extra=str(data.get('extra','')).strip()
                    if bool(base)!=bool(extra): raise ValueError('أدخل السعر الأساسي وزيادة الكيلومتر معًا')
                    values=[]
                    for value in (base,extra):
                        if not value:
                            values.append('')
                            continue
                        amount=Decimal(value)
                        if not amount.is_finite() or amount<=0 or amount>10000: raise ValueError('السعر يجب أن يكون أكبر من صفر وحتى 10000 جنيه')
                        values.append(str(amount.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)))
                    for suffix,value in zip(('_base','_extra'),values):
                        db.execute('UPDATE settings SET value=? WHERE key=?',(value,key+suffix))
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
                elif path == '/api/maps-settings':
                    if user['role']!='admin' or user['staff_permissions'] is not None: return self.respond({'error':'ربط الخرائط للمسؤول الرئيسي فقط'},403)
                    key=str(data.get('browser_key','')).strip()
                    if not re.fullmatch(r'AIza[A-Za-z0-9_-]{35}',key): raise ValueError('أدخل مفتاح Maps JavaScript API الصحيح من حساب جوجل')
                    db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('google_maps_browser_key',key))
                    return self.respond({'ok':True})
                elif path == '/api/settings':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    current=dict(db.execute('SELECT key,value FROM settings'))
                    fee=float(data.get('delivery_fee',current.get('delivery_fee','20')))
                    if not math.isfinite(fee) or fee<0: raise ValueError('رسوم التوصيل غير صحيحة')
                    values=[('wallet',str(data.get('wallet',current.get('wallet',''))).strip()),('instapay',str(data.get('instapay',current.get('instapay',''))).strip()),('whatsapp',str(data.get('whatsapp',current.get('whatsapp',''))).strip()),('delivery_fee',str(fee))]
                    if any(len(v)>100 for k,v in values): raise ValueError('رقم أو حساب التحويل طويل جدًا')
                    for k,v in values:
                        db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
                elif path == '/api/commission-rate':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    key=str(data.get('key',''))
                    if key not in ('products','delivery','custom',*RIDE_RATE_KEYS.values()): raise ValueError('نوع الخدمة غير معروف')
                    try: percent=Decimal(str(data.get('percent','')))
                    except InvalidOperation: raise ValueError('النسبة غير صحيحة')
                    if not percent.is_finite() or percent<0 or percent>100 or percent!=percent.quantize(Decimal('0.01')): raise ValueError('النسبة من صفر إلى 100 وبحد أقصى منزلتين عشريتين')
                    db.execute('UPDATE settings SET value=? WHERE key=?',(str(percent),'commission_'+key))
                elif path == '/api/area-fee':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    area,fee=data['area'],float(data['fee'])
                    if area not in AREAS or fee<0: raise ValueError('المنطقة أو الرسوم غير صحيحة')
                    db.execute('UPDATE area_fees SET fee=? WHERE area=?',(fee,area))
                elif path == '/api/driver/earning':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if not unlocked(self,db): raise ValueError('افتح المحفظة بكلمة السر أولًا')
                    verify_owner(self,db,user,data.get('wallet_password'))
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
                        if o['commission_percent'] or o['commission_cents']: raise ValueError('الأجر محسوب تلقائيًا من نسبة الخدمة')
                        db.execute('UPDATE orders SET driver_earning_cents=? WHERE id=?',(int(amount*100),o['id']))
                        log(db,o['id'],'حدد المسؤول أجر الطيار: '+str(amount)+' جنيه')
                elif path == '/api/driver/break':
                    if user['role']!='driver': return self.respond({'error':'خاص بالطيار فقط'},403)
                    minutes=data.get('minutes')
                    if type(minutes) is not int or minutes not in (0,15,30,45): raise ValueError('اختر 15 أو 30 أو 45 دقيقة؛ أقصى راحة 45 دقيقة')
                    driver=db.execute('SELECT id FROM drivers WHERE user_id=?',(user['id'],)).fetchone()
                    if not driver: raise ValueError('حساب الطيار غير موجود')
                    did=driver['id']
                    db.execute('UPDATE drivers SET break_until=? WHERE id=?',(int(time.time())+minutes*60 if minutes else 0,did))
                    if minutes:
                        for offer in db.execute("SELECT id FROM orders WHERE driver_id=? AND status='offered'",(did,)).fetchall():
                            db.execute('INSERT OR IGNORE INTO order_declines VALUES (?,?)',(offer['id'],did))
                            assign(db,offer['id'])
                    else: refresh_offers(db)
                elif path == '/api/driver/availability':
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    did=int(data['id'])
                    account=db.execute('SELECT u.disabled FROM users u JOIN drivers d ON d.user_id=u.id WHERE d.id=?',(did,)).fetchone()
                    if not account: raise ValueError('المندوب غير موجود')
                    if account['disabled']: raise ValueError('استرجع حساب الطيار أولًا')
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
                    if d and d['identity_blocked_shift'] is not None: raise ValueError('الطيار موقوف بإنذار الهوية')
                    if not o or not d or (o['kind']=='ride' and d['vehicle_type']!=o['vehicle']) or o['area']!=d['area'] or not d['available'] or d['break_until']>int(time.time()) or o['payment_status']!='confirmed' or o['status'] not in ('assigned','awaiting_driver','ready') or (o['kind']!='products' and not o['quote_accepted']):
                        raise ValueError('تعذر إسناد الطلب لهذا المندوب')
                    if not o['commission_locked']:
                        percent,share,net=commission_split(db,o['kind'],d['vehicle_type'],o['delivery_fee'])
                        db.execute('UPDATE orders SET commission_percent=?,commission_cents=?,driver_earning_cents=?,commission_locked=1 WHERE id=?',(percent,share,net,oid))
                    db.execute('UPDATE orders SET driver_id=?,status=? WHERE id=?',(did,'ready' if o['status']=='ready' else 'assigned',oid))
                    log(db,oid,'أعاد المسؤول إسناد الطلب إلى مندوب آخر')
                elif path == "/api/driver":
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if data["area"] not in AREAS: raise ValueError("منطقة غير معروفة")
                    vehicle=str(data.get('vehicle_type','موتوسيكل'))
                    if vehicle not in ('موتوسيكل','عجلة','توك توك','سيارة','ميكروباص','سكوتر'): raise ValueError('نوع المركبة غير معروف')
                    if data.get('password') != data.get('confirm_password'): raise ValueError('كلمتا المرور غير متطابقتين')
                    if not data.get('username'): raise ValueError('اسم المستخدم مطلوب')
                    email=str(data.get('email','')).strip().lower()
                    if email and (len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)): raise ValueError('أدخل إيميل الطيار الصحيح')
                    if email and db.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone(): raise ValueError('الإيميل مستخدم بحساب آخر')
                    uid=create_user(db,str(data['name']),str(data['phone']),'driver',str(data['password']),data.get('username'))
                    if email: db.execute('UPDATE users SET email=? WHERE id=?',(email,uid))
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
                    service_key='products' if kind=='products' else 'delivery' if kind=='delivery' else RIDE_RATE_KEYS.get(data.get('vehicle'),'') if kind=='ride' else str(data.get('service_key',''))
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
                    prescription_only = data.get('prescription_only') is True
                    if prescription_only and kind!='products': raise ValueError('طلب الروشتة خاص بالأدوية')
                    merchant=None
                    if kind == "products":
                        merchant=db.execute('SELECT * FROM merchants WHERE id=? AND active=1',(int(data.get('merchant_id') or 0),)).fetchone()
                        if not merchant: raise ValueError('اختر محلًا أو صيدلية متاحة')
                        if prescription_only:
                            if merchant['category']!='أدوية' or data.get('items'): raise ValueError('اختر صيدلية لطلب الروشتة دون منتجات')
                            medicine_review = requires_prescription = True
                        shop_anywhere=bool(data.get('shop_anywhere'))
                        if shop_anywhere and merchant['category']!='سوبر ماركت': raise ValueError('الشراء من أي متجر متاح للسوبر ماركت فقط')
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
                            requires_prescription |= p['category']=='أدوية' or bool(p['requires_prescription'])
                            subtotal += (p["price"] / weight_basis(p) if sold_by_weight(p) else p["price"]) * qty
                        if not items and not prescription_only: raise ValueError("السلة فارغة")
                    prescription=str(data.get('prescription',''))
                    if requires_prescription and not valid_image(prescription,2_500_000): raise ValueError('صورة الوصفة مطلوبة لهذا المنتج')
                    if prescription and not valid_image(prescription,2_500_000): raise ValueError('صورة الوصفة غير صالحة')
                    if payment=='wallet' and kind=='products' and not medicine_review and not valid_image(proof,2_500_000): raise ValueError('صورة إثبات التحويل مطلوبة')
                    if medicine_review and proof: raise ValueError('انتظر مراجعة طلب الأدوية قبل التحويل')
                    if kind == "ride" and data.get("vehicle") not in ("توك توك", "موتوسيكل", "سيارة", "ميكروباص", "عجلة", "سكوتر"): raise ValueError("اختر نوع المركبة")
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
                    ride_rate_key=RIDE_RATE_KEYS.get(data.get('vehicle','')) if kind=='ride' else None
                    ride_priced=bool(ride_rate_key and db.execute("SELECT value FROM settings WHERE key=?",(ride_rate_key+'_base',)).fetchone()[0])
                    if (rate and not prescription_only and kind in ('products','delivery') and not (kind=='products' and shop_anywhere)) or (kind=='ride' and ride_priced):
                        km_quote=verify_delivery_quote(db,user['id'],data)
                        fee=km_quote['fee']
                    ps = "confirmed" if payment == "cash" else "pending"
                    status = "awaiting_quote" if kind != "products" else ("medicine_review" if medicine_review else ("new" if payment == "cash" else "payment_review"))
                    cur = db.execute("INSERT INTO orders(user_id,client_request_id,kind,customer,phone,area,address,details,vehicle,pickup,destination,payment,proof,reference,prescription,medicine_review,payment_status,status,total,delivery_fee,quote_accepted,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (user['id'],request_id,kind, customer, phone, data["area"], address, str(data.get("details", "")), str(data.get("vehicle", "")), str(data.get("pickup", "")), str(data.get("destination", "")), payment, proof, str(data.get("reference", "")), prescription,1 if medicine_review else 0, ps, status, subtotal+fee, fee,1 if kind=='products' else 0, now()))
                    oid = cur.lastrowid
                    if kind=='products' and payment=='cash' and not medicine_review and not prescription_only:
                        reward=db.execute("SELECT id,amount_cents FROM featured_rewards WHERE user_id=? AND kind='discount' AND used_order_id IS NULL ORDER BY id LIMIT 1",(user['id'],)).fetchone()
                        if reward:
                            cents=min(reward['amount_cents'],int((Decimal(str(subtotal+fee))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP)))
                            db.execute('UPDATE orders SET total=total-?,discount_cents=? WHERE id=?',(cents/100,cents,oid))
                            db.execute('UPDATE featured_rewards SET used_order_id=? WHERE id=?',(oid,reward['id']))
                            log(db,oid,f'خصم مميز بقيمة {cents/100:.2f} جنيه')
                    if prescription_only: db.execute('UPDATE orders SET prescription_only=1,total=0,delivery_fee=0,quote_accepted=0 WHERE id=?',(oid,))
                    if km_quote:
                        db.execute('UPDATE orders SET route_km=?,km_rate=? WHERE id=?',(km_quote['km'],km_quote['rate'],oid))
                        if kind in ('delivery','ride'): db.execute("UPDATE orders SET status=?,quote_accepted=1 WHERE id=?",('new' if payment=='cash' else 'payment_review',oid))
                        log(db,oid,f"رسوم الطريق: {km_quote['km']} كم، {km_quote.get('vehicle','توصيل')} = {fee} ج")
                    if parcel:
                        db.execute('UPDATE orders SET shipment_type=?,shipment_other=?,sender_name=?,sender_phone=?,recipient_name=?,recipient_phone=? WHERE id=?',tuple(parcel[key] for key in ('shipment_type','shipment_other','sender_name','sender_phone','recipient_name','recipient_phone'))+(oid,))
                    db.execute('UPDATE orders SET service_key=?,latitude=?,longitude=?,merchant_id=?,pickup_lat=?,pickup_lon=?,shop_anywhere=?,pickup=? WHERE id=?',(service_key,lat,lon,merchant['id'] if merchant else None,None if kind=='products' and shop_anywhere else merchant['lat'] if merchant else pickup_lat,None if kind=='products' and shop_anywhere else merchant['lon'] if merchant else pickup_lon,1 if kind=='products' and shop_anywhere else 0,'أي سوبر ماركت قريب يختاره الطيار' if kind=='products' and shop_anywhere else str(data.get('pickup','')),oid))
                    for p, qty in items:
                        if not (kind=='products' and shop_anywhere): db.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty / weight_basis(p) if sold_by_weight(p) else qty, p["id"]))
                        db.execute("INSERT INTO order_items(order_id,product_id,name,quantity,unit_price,unit,stock_quantity) VALUES (?,?,?,?,?,?,?)", (oid, p["id"], p["name"], qty, p["price"] / weight_basis(p) if sold_by_weight(p) else p["price"], "كجم" if sold_by_weight(p) else "قطعة", 0 if kind=='products' and shop_anywhere else qty / weight_basis(p) if sold_by_weight(p) else qty))
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
                        if action=='accept_offer':
                            shift=db.execute('SELECT started_at,second_selfie FROM driver_shifts WHERE driver_id=? AND ended_at IS NULL',(d['id'],)).fetchone()
                            if not shift or datetime.fromisoformat(shift['started_at']).timestamp()+86400<=time.time(): raise ValueError('ابدأ الشيفت بصورة وجه حديثة قبل قبول طلب جديد')
                            if datetime.fromisoformat(shift['started_at']).timestamp()+21600<=time.time() and not shift['second_selfie']: raise ValueError('التقط صورة الشيفت الثانية قبل قبول طلب جديد')
                    elif user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if action == 'retry_dispatch' and user['role']=='admin' and o['status']=='awaiting_driver' and o['payment_status']=='confirmed' and (o['kind']=='products' or o['quote_accepted']):
                        db.execute('DELETE FROM order_declines WHERE order_id=?',(oid,))
                        db.execute('DELETE FROM order_offer_timeouts WHERE order_id=?',(oid,))
                        log(db,oid,'طلب المسؤول إعادة محاولة توزيع الطيارين')
                        assign(db,oid)
                    elif action == "confirm_payment" and o["payment_status"] == "pending" and o["proof"] and o["status"] == "payment_review":
                        db.execute("UPDATE orders SET payment_status='confirmed',status='new' WHERE id=?", (oid,))
                        log(db, oid, "أكد المسؤول وصول التحويل")
                        assign(db,oid)
                    elif action == 'approve_order' and o['status']=='new' and o['payment_status']=='confirmed':
                        log(db,oid,'راجع المسؤول الطلب ووافق على توزيعه')
                        assign(db,oid)
                    elif action == 'approve_medicine' and o['status']=='medicine_review' and o['medicine_review']:
                        db.execute("UPDATE orders SET medicine_review=0,status=? WHERE id=?",('awaiting_quote' if o['prescription_only'] else 'payment_review' if o['payment']=='wallet' else 'new',oid))
                        log(db,oid,'راجع المسؤول طلب الأدوية')
                        if o['payment']=='cash' and not o['prescription_only']: assign(db,oid)
                    elif action == "price" and (o["kind"] != "products" or o['prescription_only']) and o["status"] in ('awaiting_quote','quote_pending'):
                        try:
                            amount=Decimal(str(data.get('amount','')))
                        except InvalidOperation: raise ValueError('السعر غير صحيح')
                        if not amount.is_finite() or amount<0 or amount>100000 or amount!=amount.quantize(Decimal('0.01')): raise ValueError('اكتب سعرًا صحيحًا بحد أقصى منزلتين عشريتين')
                        delivery_amount=amount
                        if o['prescription_only']:
                            try: delivery_amount=Decimal(str(data.get('delivery_amount','')))
                            except InvalidOperation: raise ValueError('حدد مبلغ التوصيل')
                            if not delivery_amount.is_finite() or delivery_amount<0 or delivery_amount>amount or delivery_amount!=delivery_amount.quantize(Decimal('0.01')): raise ValueError('التوصيل يجب أن يكون مبلغًا صحيحًا لا يتجاوز الإجمالي')
                        db.execute("UPDATE orders SET total=?,delivery_fee=?,status='quote_pending',quote_accepted=0 WHERE id=?", (float(amount),float(delivery_amount),oid))
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
                        if action=='delivered' and not o['commission_locked']:
                            driver=db.execute('SELECT vehicle_type FROM drivers WHERE id=?',(o['driver_id'],)).fetchone()
                            percent,share,net=commission_split(db,o['kind'],driver['vehicle_type'] if driver else o['vehicle'],o['delivery_fee'])
                            db.execute('UPDATE orders SET commission_percent=?,commission_cents=?,driver_earning_cents=?,commission_locked=1 WHERE id=?',(percent,share,net,oid))
                        if action=='picked_up' and o['shop_anywhere']:
                            shop=str(data.get('purchase_shop','')).strip()
                            if not 3<=len(shop)<=120: raise ValueError('اكتب اسم أو عنوان السوبر ماركت الذي اشتريت منه')
                            coords=data.get('purchase_lat'),data.get('purchase_lon')
                            if coords[0] is not None or coords[1] is not None:
                                if coords[0] is None or coords[1] is None: raise ValueError('موقع الشراء غير مكتمل')
                                plat,plon=float(coords[0]),float(coords[1])
                                if not all(math.isfinite(x) for x in (plat,plon)) or not (-90<=plat<=90 and -180<=plon<=180): raise ValueError('موقع الشراء غير صحيح')
                                db.execute('UPDATE orders SET pickup_lat=?,pickup_lon=? WHERE id=?',(plat,plon,oid))
                            db.execute('UPDATE orders SET pickup=? WHERE id=?',(shop,oid))
                        db.execute("UPDATE orders SET status=? WHERE id=?", (action, oid))
                        if action=='delivered' and o['payment']=='cash': db.execute('UPDATE orders SET cash_collected=1 WHERE id=?',(oid,))
                        log(db, oid, {"picked_up": "استلم المندوب الطلب", "on_way": "المندوب في الطريق", "delivered": "تم التسليم"}[action])
                    elif action=='settle_cash' and o['payment']=='cash' and o['status']=='delivered' and o['cash_collected'] and not o['cash_settled']:
                        if not unlocked(self,db): raise ValueError('افتح المحفظة بكلمة السر أولًا')
                        verify_owner(self,db,user,data.get('wallet_password'))
                        db.execute('UPDATE orders SET cash_settled=1,commission_settled=1 WHERE id=?',(oid,))
                        log(db,oid,'أكد المسؤول استلام الكاش من المندوب')
                    elif action in ('cancel','reject_medicine','customer_cancel') and o["status"] not in ("delivered", "cancelled") and (action!='reject_medicine' or o['status']=='medicine_review') and (action!='customer_cancel' or (o['status'] in ('awaiting_quote','quote_pending','medicine_review','payment_review','new','awaiting_driver','offered','assigned') and not (o['payment']=='wallet' and (o['payment_status']=='confirmed' or bool(o['proof']))))):
                        db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
                        if o['discount_cents']:
                            db.execute('UPDATE featured_rewards SET used_order_id=NULL WHERE used_order_id=? AND kind=\'discount\'',(oid,))
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
        except (ValueError, InvalidOperation, KeyError, TypeError, sqlite3.Error) as e:
            self.respond({"error": str(e)}, 400)


if __name__ == "__main__":
    init()
    host=os.environ.get('WALLAHA_BIND','127.0.0.1')
    port=int(os.environ.get('WALLAHA_PORT',os.environ.get('PORT','8080')))
    print(f"Wallaha development server: http://{host}:{port}")
    ThreadingHTTPServer((host,port), Handler).serve_forever()
