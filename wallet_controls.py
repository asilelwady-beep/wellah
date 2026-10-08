"""Owner reauthentication, auditable wallet changes, and retained chat review."""
import hashlib
import hmac
import json
import re
import time
from decimal import Decimal, InvalidOperation
from account_support import limit


def init_wallet_controls(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS wallet_unlocks(session_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS wallet_adjustments(id INTEGER PRIMARY KEY,driver_id INTEGER NOT NULL REFERENCES drivers(id),admin_id INTEGER NOT NULL REFERENCES users(id),amount_cents INTEGER NOT NULL,reason TEXT NOT NULL,at TEXT NOT NULL,settled INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS wallet_audit(id INTEGER PRIMARY KEY,driver_id INTEGER NOT NULL REFERENCES drivers(id),admin_id INTEGER NOT NULL REFERENCES users(id),request_id TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,at TEXT NOT NULL,UNIQUE(admin_id,request_id));
    ''')
    if 'commission_settled' not in {r['name'] for r in db.execute('PRAGMA table_info(orders)')}:
        db.execute('ALTER TABLE orders ADD COLUMN commission_settled INTEGER NOT NULL DEFAULT 0')


def session_hash(handler):
    return hashlib.sha256(handler.headers.get('Authorization','')[7:].encode()).hexdigest()


def unlocked(handler,db):
    return bool(db.execute('SELECT 1 FROM wallet_unlocks WHERE session_hash=? AND expires>?',(session_hash(handler),int(time.time()))).fetchone())


def verify_owner(handler,db,user,password):
    if not user or user['role']!='admin': raise ValueError('محافظ الإدارة خاصة بالمسؤول فقط')
    actor=str(user['id'])+':'+handler.client_address[0]
    failures=db.execute('SELECT count FROM feature_limits WHERE scope=? AND actor=? AND bucket=?',('wallet-password',actor,int(time.time())//600)).fetchone()
    if failures and failures['count']>=10: raise ValueError('محاولات كلمة سر كثيرة؛ حاول بعد عشر دقائق')
    row=db.execute('SELECT salt,password_hash FROM users WHERE id=?',(user['id'],)).fetchone()
    digest=hashlib.scrypt(str(password or '').encode(),salt=bytes.fromhex(row['salt']),n=2**14,r=8,p=1).hex()
    if not hmac.compare_digest(digest,row['password_hash']):
        limit(db,'wallet-password',actor,600,10)
        raise ValueError('كلمة سر لوحة التحكم غير صحيحة')


def redact_wallet(wallet):
    wallet.pop('commission_due',None)
    wallet.pop('audit',None)
    wallet['paid']=0
    wallet['entries']=[entry for entry in wallet['entries'] if not entry['driver_earning_paid'] or (entry['payment']=='cash' and entry['cash_collected'] and not entry['cash_settled'])]
    wallet['adjustments']=[entry for entry in wallet['adjustments'] if not entry['settled']]
    for entry in wallet['entries']:
        for key in list(entry):
            if key.startswith('commission_'): entry.pop(key)
    return wallet


def wallet_post(handler,db,path,data,user,now,driver_wallet):
    if path not in ('/api/admin/wallet/unlock','/api/admin/wallet/lock','/api/admin/wallet/change','/api/admin/chat/archive'): return False
    if not user or user['role']!='admin':
        handler.respond({'error':'غير مصرح'},403);return True
    if path=='/api/admin/chat/archive':
        oid=int(data.get('order_id',0));after=int(data.get('after_id',0))
        if not db.execute('SELECT 1 FROM orders WHERE id=?',(oid,)).fetchone(): raise ValueError('الطلب غير موجود')
        messages=[dict(r) for r in db.execute('SELECT m.id,m.driver_id,m.sender_id,m.body,m.at,u.name AS sender_name,u.role AS sender_role FROM order_messages m JOIN users u ON u.id=m.sender_id WHERE m.order_id=? AND m.id>? ORDER BY m.id LIMIT 200',(oid,after))]
        handler.respond({'messages':messages,'next_id':messages[-1]['id'] if messages else after,'has_more':bool(messages and db.execute('SELECT 1 FROM order_messages WHERE order_id=? AND id>?',(oid,messages[-1]['id'])).fetchone())});return True
    if path.endswith('/lock'):
        db.execute('DELETE FROM wallet_unlocks WHERE session_hash=?',(session_hash(handler),));handler.respond({'ok':True});return True
    verify_owner(handler,db,user,data.get('wallet_password'))
    if path.endswith('/unlock'):
        db.execute('INSERT INTO wallet_unlocks VALUES (?,?) ON CONFLICT(session_hash) DO UPDATE SET expires=excluded.expires',(session_hash(handler),int(time.time())+300));handler.respond({'ok':True});return True
    if not unlocked(handler,db): raise ValueError('افتح المحفظة بكلمة السر أولًا؛ القفل يعود بعد خمس دقائق')
    driver_id=int(data.get('driver_id',0));action=str(data.get('action',''));request_id=str(data.get('request_id',''))
    if not re.fullmatch(r'[a-zA-Z0-9_-]{10,100}',request_id): raise ValueError('معرف تسوية غير صالح')
    reason=str(data.get('reason','')).strip()
    if not 3<=len(reason)<=300: raise ValueError('اكتب سبب التعديل أو التسوية من 3 إلى 300 حرف')
    # Lock before reading balances or settling: simultaneous submissions cannot duplicate adjustments.
    db.execute('BEGIN IMMEDIATE')
    driver=db.execute('SELECT user_id FROM drivers WHERE id=?',(driver_id,)).fetchone()
    if not driver: raise ValueError('الطيار غير موجود')
    existing=db.execute('SELECT driver_id,action,details FROM wallet_audit WHERE admin_id=? AND request_id=?',(user['id'],request_id)).fetchone()
    intent={'reason':reason,'amount':str(data.get('amount','')),'email':str(data.get('email','')).strip().lower()}
    if existing:
        previous=json.loads(existing['details'])
        if existing['driver_id']!=driver_id or existing['action']!=action or previous['intent']!=intent: raise ValueError('معرف التسوية مستخدم لتعديل آخر')
        handler.respond({'ok':True,'wallet':driver_wallet(db,driver_id)});return True
    before=driver_wallet(db,driver_id)
    if action=='adjust':
        try: amount=Decimal(intent['amount'])
        except InvalidOperation: raise ValueError('المبلغ غير صالح')
        if not amount.is_finite() or amount==0 or abs(amount)>100000 or amount!=amount.quantize(Decimal('0.01')): raise ValueError('اكتب مبلغ إضافة أو خصم بحد أقصى منزلتين عشريتين')
        db.execute('INSERT INTO wallet_adjustments(driver_id,admin_id,amount_cents,reason,at) VALUES (?,?,?,?,?)',(driver_id,user['id'],int(amount*100),reason,now()))
    elif action in ('settle','pay','cash'):
        if action in ('settle','pay'):
            db.execute("UPDATE orders SET driver_earning_paid=1 WHERE driver_id=? AND status='delivered'",(driver_id,))
            db.execute('UPDATE wallet_adjustments SET settled=1 WHERE driver_id=?',(driver_id,))
        if action in ('settle','cash'):
            db.execute("UPDATE orders SET cash_settled=1,commission_settled=1 WHERE driver_id=? AND status='delivered'",(driver_id,))
    elif action=='identity':
        email=intent['email']
        if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): raise ValueError('اكتب بريد الطيار الصحيح')
        db.execute('UPDATE users SET email=? WHERE id=?',(email,driver['user_id']))
    else: raise ValueError('تعديل المحفظة غير معروف')
    after=driver_wallet(db,driver_id)
    if action=='settle' and any(abs(after[key])>0.001 for key in ('balance','commission_due','cash_due')): raise ValueError('تعذر تصفير جميع مبالغ الطيار؛ راجع بيانات المشاوير')
    details=json.dumps({'intent':intent,'before':{k:v for k,v in before.items() if k not in ('entries','adjustments','audit')},'after':{k:v for k,v in after.items() if k not in ('entries','adjustments','audit')}},ensure_ascii=False)
    db.execute('INSERT INTO wallet_audit(driver_id,admin_id,request_id,action,details,at) VALUES (?,?,?,?,?,?)',(driver_id,user['id'],request_id,action,details,now()))
    handler.respond({'ok':True,'wallet':after});return True
