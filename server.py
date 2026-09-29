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
import time
import math
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = Path(os.environ.get('WALLAHA_DB_PATH', str(ROOT / 'wallaha.sqlite3')))
AREAS = ["أبو رجوان البحري", "أبو رجوان القبلي", "أبو صير", "ميت رهينة", "سقارة", "دهشور", "زاوية دهشور", "الشوبك الغربي", "الطرفاية", "المرازيق", "الشنباب", "العزيزية"]


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
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), action TEXT NOT NULL, at TEXT NOT NULL);
        """)
        if not db.execute("SELECT 1 FROM settings WHERE key='wallet'").fetchone():
            db.executemany("INSERT INTO settings VALUES (?,?)", [("wallet", "01113887292"), ("whatsapp", "01113887292"), ("delivery_fee", "20")])
        if 'username' not in {x['name'] for x in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN username TEXT')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username) WHERE username IS NOT NULL')
        if not db.execute("SELECT 1 FROM users WHERE username='owner'").fetchone():
            db.execute("UPDATE users SET username='owner' WHERE role='admin' AND username IS NULL")
        db.execute("INSERT OR IGNORE INTO services(key,name) VALUES ('products','المنتجات'),('delivery','توصيل أوردر'),('ride_tuktuk','مشوار توك توك'),('ride_motorbike','مشوار موتوسيكل'),('ride_car','مشوار سيارة')")
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
        if not db.execute("SELECT 1 FROM products").fetchone():
            db.executemany("INSERT INTO products(name,category,price,stock) VALUES (?,?,?,?)", [("منتج تجريبي: أرز 1 كجم", "سوبر ماركت", 40, 20), ("منتج تجريبي: خضار مشكل", "خضار", 35, 15), ("منتج تجريبي: وجبة", "مطاعم", 85, 10)])
        if not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            password = os.environ.get('WALLAHA_ADMIN_PASSWORD', '')
            if not password or len(password) < 10:
                raise RuntimeError('Set WALLAHA_ADMIN_PASSWORD to at least 10 characters before first run')
            create_user(db, 'المسؤول', os.environ.get('WALLAHA_ADMIN_PHONE', '01113887292'), 'admin', password, os.environ.get('WALLAHA_ADMIN_USERNAME','owner'))


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


def log(db, oid, action):
    db.execute("INSERT INTO events(order_id,action,at) VALUES (?,?,?)", (oid, action, now()))


def assign(db, oid):
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o or o["payment_status"] != "confirmed" or (o['kind']!='products' and not o['quote_accepted']) or o['medicine_review']:
        return
    # Last known location must be recent. Busy and declined drivers are excluded.
    candidates = db.execute("""SELECT d.id,d.lat,d.lon FROM drivers d WHERE d.available=1
        AND d.lat IS NOT NULL AND d.lon IS NOT NULL AND d.location_at>=?
        AND NOT EXISTS (SELECT 1 FROM orders x WHERE x.driver_id=d.id AND x.id<>?
            AND x.status IN ('offered','assigned','ready','picked_up','on_way'))
        AND NOT EXISTS (SELECT 1 FROM order_declines x WHERE x.order_id=? AND x.driver_id=d.id)""",
        (datetime.fromtimestamp(time.time()-300,timezone.utc).isoformat(timespec='seconds'),oid,oid)).fetchall()
    origin=(o['pickup_lat'],o['pickup_lon']) if o['pickup_lat'] is not None else (o['latitude'],o['longitude'])
    if origin[0] is None or origin[1] is None: return
    def distance(d):
        a,b=map(math.radians,(origin[0],d['lat']))
        da=math.radians(d['lat']-origin[0]);dl=math.radians(d['lon']-origin[1])
        return 6371*2*math.asin(min(1,math.sqrt(math.sin(da/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2)))
    d=min(candidates,key=lambda x:(distance(x),x['id'])) if candidates else None
    if d:
        db.execute("UPDATE orders SET driver_id=?,status='offered',offer_until=? WHERE id=?", (d['id'],int(time.time())+90,oid))
        log(db, oid, "عُرض الطلب تلقائيًا على أقرب مندوب للمحل")
    else:
        db.execute("UPDATE orders SET driver_id=NULL,status='awaiting_driver',offer_until=NULL WHERE id=?", (oid,))
        if o['status']!='awaiting_driver': log(db, oid, "بانتظار مندوب متاح يشارك موقعًا حديثًا")


def refresh_offers(db):
    for o in db.execute("SELECT id,driver_id FROM orders WHERE status='offered' AND offer_until<?",(int(time.time()),)).fetchall():
        db.execute('INSERT OR IGNORE INTO order_declines VALUES (?,?)',(o['id'],o['driver_id']))
        log(db,o['id'],'انتهت مهلة قبول المندوب؛ يجري البحث عن التالي')
        assign(db,o['id'])
    for o in db.execute("SELECT id FROM orders WHERE status='awaiting_driver'").fetchall():
        assign(db,o['id'])


def rows(db, sql, args=()):
    return [dict(x) for x in db.execute(sql, args)]


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
                o["items"] = rows(db, "SELECT product_id,name,quantity,unit_price FROM order_items WHERE order_id=?", (o["id"],))
                o["events"] = rows(db, "SELECT action,at FROM events WHERE order_id=? ORDER BY id", (o["id"],))
                if user['role']!='admin':
                    if user['role']=='customer' and o['status'] not in ('assigned','ready','picked_up','on_way'):
                        o['driver_lat']=o['driver_lon']=o['driver_location_at']=None
                    o['has_proof']=bool(o['proof'])
                    o['has_prescription']=bool(o['prescription'])
                    o.pop('proof', None)
                    o.pop('reference', None)
                    o.pop('prescription', None)
            self.respond({"user":dict(user),"areas": AREAS,"area_fees":{x['area']:x['fee'] for x in db.execute('SELECT * FROM area_fees')} if user['role']!='driver' else {}, "categories": rows(db,"SELECT * FROM categories ORDER BY sort_order,name") if user['role']=='admin' else rows(db,"SELECT * FROM categories WHERE active=1 ORDER BY sort_order,name") if user['role']=='customer' else [], "merchants":rows(db,"SELECT * FROM merchants ORDER BY id DESC") if user['role']=='admin' else rows(db,"SELECT * FROM merchants WHERE active=1 ORDER BY id DESC") if user['role']=='customer' else [], "products": rows(db, "SELECT * FROM products ORDER BY id DESC") if user['role']!='driver' else [], "services":rows(db,"SELECT * FROM services ORDER BY rowid") if user['role']!='driver' else [], "drivers": rows(db, "SELECT d.*,u.username FROM drivers d JOIN users u ON u.id=d.user_id ORDER BY d.id") if user['role']=='admin' else [], "orders": orders, "settings": {x["key"]: x["value"] for x in db.execute("SELECT * FROM settings")} if user['role']!='driver' else {}})

    def do_POST(self):
        try:
            data = self.body()
            with connect() as db:
                path = urlparse(self.path).path
                if path == '/api/register':
                    uid=create_user(db,str(data['name']),str(data['phone']),'customer',str(data['password']))
                    return self.respond({'ok':True,'id':uid})
                if path == '/api/login':
                    phone=str(data.get('phone','')).strip()[:64]
                    remote=self.client_address[0]
                    if not login_allowed(db,phone,remote): return self.respond({'error':'محاولات دخول كثيرة. حاول لاحقًا'},429)
                    u=authenticate(db,phone,data.get('password',''))
                    if not u:
                        record_failed_login(db,phone,remote)
                        db.commit()
                        return self.respond({'error':'بيانات الدخول غير صحيحة'},401)
                    db.execute('DELETE FROM login_attempts WHERE phone=? AND remote=?',(phone,remote))
                    token=secrets.token_urlsafe(32)
                    db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],int(time.time())+86400*7))
                    return self.respond({'token':token,'role':u['role']})
                user=self.user(db)
                if not user: return self.respond({'error':'سجل الدخول أولًا'},401)
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
                    cur=db.execute('UPDATE products SET active=0 WHERE id=?',(int(data['id']),))
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
                    cur=db.execute('UPDATE products SET price=?,stock=?,active=?,requires_prescription=?,merchant_id=? WHERE id=?',(price,stock,1 if data.get('active') else 0,1 if data.get('requires_prescription') and old['category']=='أدوية' else 0,merchant_id or None,int(data['id'])))
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
                    if not o or not d or o['area']!=d['area'] or not d['available'] or o['payment_status']!='confirmed' or o['status'] not in ('assigned','awaiting_driver','ready') or (o['kind']!='products' and not o['quote_accepted']):
                        raise ValueError('تعذر إسناد الطلب لهذا المندوب')
                    db.execute('UPDATE orders SET driver_id=?,status=? WHERE id=?',(did,'ready' if o['status']=='ready' else 'assigned',oid))
                    log(db,oid,'أعاد المسؤول إسناد الطلب إلى مندوب آخر')
                elif path == "/api/driver":
                    if user['role']!='admin': return self.respond({'error':'غير مصرح'},403)
                    if data["area"] not in AREAS: raise ValueError("منطقة غير معروفة")
                    uid=create_user(db,str(data['name']),str(data['phone']),'driver',str(data['password']),data.get('username'))
                    db.execute("INSERT INTO drivers(user_id,name,phone,area) VALUES (?,?,?,?)", (uid,str(data["name"]).strip(), str(data["phone"]).strip(), data["area"]))
                elif path == "/api/order":
                    if user['role']!='customer': return self.respond({'error':'غير مصرح'},403)
                    request_id=str(data.get('client_request_id','')).strip()
                    if not request_id or len(request_id)>100: raise ValueError('معرف الطلب غير صالح')
                    previous=db.execute('SELECT id FROM orders WHERE user_id=? AND client_request_id=?',(user['id'],request_id)).fetchone()
                    if previous: return self.respond({'ok':True,'id':previous['id'],'duplicate':True})
                    kind = data["kind"]
                    if kind not in ("products", "delivery", "ride", "custom"): raise ValueError("نوع خدمة غير معروف")
                    service_key='products' if kind=='products' else 'delivery' if kind=='delivery' else {'توك توك':'ride_tuktuk','موتوسيكل':'ride_motorbike','سيارة':'ride_car'}.get(data.get('vehicle'),'') if kind=='ride' else str(data.get('service_key',''))
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
                            qty = int(it["quantity"])
                            p = db.execute("SELECT * FROM products WHERE id=? AND active=1", (it["product_id"],)).fetchone()
                            if not p or p['merchant_id']!=merchant['id'] or qty <= 0 or p["stock"] < qty: raise ValueError("اختر منتجات من نفس المحل وبكمية متاحة")
                            items.append((p, qty))
                            medicine_review |= p['category']=='أدوية'
                            requires_prescription |= bool(p['requires_prescription'])
                            subtotal += p["price"] * qty
                        if not items: raise ValueError("السلة فارغة")
                    prescription=str(data.get('prescription',''))
                    if requires_prescription and not valid_image(prescription,2_500_000): raise ValueError('صورة الوصفة مطلوبة لهذا المنتج')
                    if prescription and not valid_image(prescription,2_500_000): raise ValueError('صورة الوصفة غير صالحة')
                    if payment=='wallet' and kind=='products' and not medicine_review and not valid_image(proof,2_500_000): raise ValueError('صورة إثبات التحويل مطلوبة')
                    if medicine_review and proof: raise ValueError('انتظر مراجعة طلب الأدوية قبل التحويل')
                    if kind == "ride" and data.get("vehicle") not in ("توك توك", "موتوسيكل", "سيارة"): raise ValueError("اختر نوع المركبة")
                    if kind in ("ride", "delivery") and not data.get("pickup"): raise ValueError("اكتب مكان الاستلام")
                    if kind in ("ride", "delivery") and not data.get("destination"): raise ValueError("اكتب الوجهة")
                    pickup_lat,pickup_lon=None,None
                    if kind!='products':
                        if data.get('pickup_lat') is None or data.get('pickup_lon') is None: raise ValueError('حدد مكان الاستلام على الخريطة')
                        pickup_lat,pickup_lon=float(data['pickup_lat']),float(data['pickup_lon'])
                        if not (-90<=pickup_lat<=90 and -180<=pickup_lon<=180): raise ValueError('موقع الاستلام غير صحيح')
                    # Service prices require admin review; fee is shown only for catalog orders.
                    if kind != "products": fee = 0
                    ps = "confirmed" if payment == "cash" else "pending"
                    status = "awaiting_quote" if kind != "products" else ("medicine_review" if medicine_review else ("new" if payment == "cash" else "payment_review"))
                    cur = db.execute("INSERT INTO orders(user_id,client_request_id,kind,customer,phone,area,address,details,vehicle,pickup,destination,payment,proof,reference,prescription,medicine_review,payment_status,status,total,delivery_fee,quote_accepted,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (user['id'],request_id,kind, customer, phone, data["area"], address, str(data.get("details", "")), str(data.get("vehicle", "")), str(data.get("pickup", "")), str(data.get("destination", "")), payment, proof, str(data.get("reference", "")), prescription,1 if medicine_review else 0, ps, status, subtotal+fee, fee,1 if kind=='products' else 0, now()))
                    oid = cur.lastrowid
                    db.execute('UPDATE orders SET service_key=?,latitude=?,longitude=?,merchant_id=?,pickup_lat=?,pickup_lon=? WHERE id=?',(service_key,lat,lon,merchant['id'] if merchant else None,merchant['lat'] if merchant else pickup_lat,merchant['lon'] if merchant else pickup_lon,oid))
                    for p, qty in items:
                        db.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, p["id"]))
                        db.execute("INSERT INTO order_items VALUES (?,?,?,?,?)", (oid, p["id"], p["name"], qty, p["price"]))
                    log(db, oid, "أنشأ العميل الطلب")
                    if status=='new': assign(db,oid)
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
                            db.execute("UPDATE products SET stock=stock+? WHERE id=?", (it["quantity"], it["product_id"]))
                        log(db, oid, "ألغي الطلب وأعيدت المنتجات للكمية المتاحة")
                    else: raise ValueError("الإجراء غير متاح في حالة الطلب الحالية")
                elif path == '/api/location':
                    if user['role']!='driver': return self.respond({'error':'غير مصرح'},403)
                    lat,lon=float(data['lat']),float(data['lon'])
                    if not (-90<=lat<=90 and -180<=lon<=180): raise ValueError('الموقع غير صالح')
                    d=db.execute('SELECT id FROM drivers WHERE user_id=?',(user['id'],)).fetchone()
                    available=db.execute('SELECT available FROM drivers WHERE id=?',(d['id'],)).fetchone() if d else None
                    if not available or not available['available']: return self.respond({'error':'المندوب غير متاح'},403)
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
