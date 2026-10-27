"""Account verification, reversible driver management, ratings and support."""
import base64
from urllib.parse import urlencode
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen


def init_features(db):
    for name, definition in [('email', 'TEXT'), ('verified_phone', 'TEXT'), ('disabled', 'INTEGER NOT NULL DEFAULT 0'), ('recovery_salt','TEXT'), ('recovery_hash','TEXT'), ('recovery_question','TEXT')]:
        if name not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email) WHERE email IS NOT NULL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS account_codes (
        id TEXT PRIMARY KEY, purpose TEXT NOT NULL, email TEXT NOT NULL,
        user_id INTEGER REFERENCES users(id), code_hash TEXT NOT NULL,
        expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, remote TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ratings (
        order_id INTEGER NOT NULL REFERENCES orders(id), sender_id INTEGER NOT NULL REFERENCES users(id),
        target_id INTEGER NOT NULL REFERENCES users(id), stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
        comment TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, PRIMARY KEY(order_id,sender_id));
    CREATE TABLE IF NOT EXISTS driver_complaints (
        id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
        customer_id INTEGER NOT NULL REFERENCES users(id), driver_id INTEGER NOT NULL REFERENCES drivers(id),
        category TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
        at TEXT NOT NULL, UNIQUE(order_id,customer_id));
    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        message TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', ai INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open', at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_counter_resets (
        id INTEGER PRIMARY KEY, person_type TEXT NOT NULL CHECK(person_type IN ('customer','driver')),
        person_id INTEGER NOT NULL, day TEXT NOT NULL, admin_id INTEGER NOT NULL REFERENCES users(id), at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS daily_counter_resets_lookup ON daily_counter_resets(day,person_type,person_id,at);
    CREATE TABLE IF NOT EXISTS feature_limits (
        scope TEXT NOT NULL, actor TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY(scope,actor,bucket));
    ''')


def normalized_username(value):
    value = str(value).strip().lower()
    if not re.fullmatch(r'[a-z\u0621-\u064a][a-z0-9_\u0621-\u064a\u0660-\u0669]{2,29}', value):
        raise ValueError('اسم المستخدم من 3 إلى 30 حرفًا عربيًا أو إنجليزيًا وأرقام أو _، ويبدأ بحرف')
    return value


def mail_ready():
    return bool((os.environ.get('RESEND_API_KEY') and os.environ.get('WALLAHA_EMAIL_FROM')) or
                (os.environ.get('WALLAHA_SMTP_HOST') and os.environ.get('WALLAHA_SMTP_FROM')))


def send_code(email, code):
    if not mail_ready():
        raise ValueError('إرسال رمز التأكيد غير متاح حاليًا؛ تواصل مع الدعم')
    text = f'رمز التأكيد: {code}\nصالح لمدة 10 دقائق. لا تشارك الرمز مع أي شخص.'
    if os.environ.get('RESEND_API_KEY') and os.environ.get('WALLAHA_EMAIL_FROM'):
        payload = {'from': os.environ['WALLAHA_EMAIL_FROM'], 'to': [email],
                   'subject': 'رمز تأكيد حساب ولعه', 'text': text}
        req = Request('https://api.resend.com/emails', data=json.dumps(payload).encode(),
                      headers={'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'],
                               'Content-Type': 'application/json', 'User-Agent': 'Wellah/1.0'})
        try:
            with urlopen(req, timeout=15) as response:
                result = json.load(response)
            if not isinstance(result, dict) or not result.get('id'):
                raise ValueError('تعذر تأكيد إرسال البريد')
        except (OSError, ValueError):
            raise ValueError('تعذر إرسال رمز التأكيد. راجع ربط خدمة البريد أو حاول لاحقًا') from None
        return
    msg = EmailMessage()
    msg['Subject'] = 'رمز تأكيد حساب ولعه'
    msg['From'] = os.environ['WALLAHA_SMTP_FROM']
    msg['To'] = email
    msg.set_content(text)
    port = int(os.environ.get('WALLAHA_SMTP_PORT', '587'))
    try:
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(os.environ['WALLAHA_SMTP_HOST'], port, timeout=15) as smtp:
            if port != 465:
                smtp.starttls(context=ssl.create_default_context())
            if os.environ.get('WALLAHA_SMTP_USER'):
                smtp.login(os.environ['WALLAHA_SMTP_USER'], os.environ.get('WALLAHA_SMTP_PASSWORD', ''))
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        raise ValueError('تعذر إرسال رمز التأكيد. حاول لاحقًا') from None


def normalized_mobile(value):
    value=str(value).strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789'))
    value=re.sub(r'[\s()-]', '', value)
    if value.startswith('*0020'): value=value[1:]
    elif value.startswith('*20'): value='0'+value[3:]
    if value.startswith('0020'): value='0'+value[4:]
    elif value.startswith('+20'): value='0'+value[3:]
    elif value.startswith('20') and len(value)==12: value='0'+value[2:]
    if not re.fullmatch(r'01[0125][0-9]{8}',value):
        raise ValueError('اكتب رقم موبايل مصري صحيح من 11 رقمًا')
    return value

def normalize_recovery_answer(value):
    answer=' '.join(str(value or '').strip().casefold().split())
    if len(answer)<3 or len(answer)>120:
        raise ValueError('اكتب إجابة استرداد من 3 إلى 120 حرفًا')
    return answer


def set_recovery_answer(db, user_id, value):
    answer=normalize_recovery_answer(value)
    salt=secrets.token_hex(16)
    digest=hashlib.scrypt(answer.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
    db.execute('UPDATE users SET recovery_salt=?,recovery_hash=? WHERE id=?',(salt,digest,user_id))


def recover_account(db, data, remote):
    limit(db,'account-recovery-ip',remote,600,10)
    phone=normalized_mobile(data.get('phone',''))
    limit(db,'account-recovery-phone',phone,600,5)
    user=db.execute("SELECT id,recovery_salt,recovery_hash FROM users WHERE phone=? AND role IN ('customer','driver') AND disabled=0",(phone,)).fetchone()
    answer=normalize_recovery_answer(data.get('recovery_answer'))
    if not user or not user['recovery_salt'] or not user['recovery_hash']:
        raise ValueError('تعذر استرداد الحساب بهذه البيانات؛ راجع رقم الهاتف أو تواصل مع الدعم')
    candidate=hashlib.scrypt(answer.encode(),salt=bytes.fromhex(user['recovery_salt']),n=2**14,r=8,p=1).hex()
    if not hmac.compare_digest(candidate,user['recovery_hash']):
        raise ValueError('تعذر استرداد الحساب بهذه البيانات؛ راجع الإجابة وحاول لاحقًا')
    password=str(data.get('password',''))
    if password!=str(data.get('confirm_password','')):
        raise ValueError('كلمتا المرور غير متطابقتين')
    if len(password)<10:
        raise ValueError('كلمة المرور لازم تكون 10 أحرف على الأقل')
    salt=secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
    db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,digest,user['id']))
    db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
    return True


def admin_recovery_account(db, username):
    identifier=str(username or '').strip()[:64]
    return db.execute("SELECT u.* FROM users u LEFT JOIN staff_members sm ON sm.user_id=u.id WHERE (u.username=? OR u.phone=?) AND u.role='admin' AND u.disabled=0 AND sm.user_id IS NULL",(identifier,identifier)).fetchone()


def recover_admin_by_answer(db, data, remote):
    limit(db,'admin-answer-ip',remote,900,10)
    identifier=str(data.get('username','')).strip()[:64]
    limit(db,'admin-answer-account',identifier.casefold(),900,5)
    account=admin_recovery_account(db,identifier)
    answer=normalize_recovery_answer(data.get('recovery_answer'))
    # Do the same expensive comparison for unknown or unconfigured accounts.
    salt=account['recovery_salt'] if account and account['recovery_salt'] else '0'*32
    expected=account['recovery_hash'] if account and account['recovery_hash'] else '0'*128
    candidate=hashlib.scrypt(answer.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
    if not hmac.compare_digest(candidate,expected) or not account or not account['recovery_question']:
        raise ValueError('تعذر الاسترداد؛ راجع اسم المستخدم والإجابة السرية. يجب إعداد سؤال الاسترداد مسبقًا من الإعدادات.')
    password=str(data.get('password',''))
    if len(password)<10 or password!=str(data.get('confirm_password','')):
        raise ValueError('اكتب كلمة مرور من 10 أحرف على الأقل وأكدها بنفس القيمة')
    salt=secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
    db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,digest,account['id']))
    db.execute('DELETE FROM sessions WHERE user_id=?',(account['id'],))
    db.execute('DELETE FROM login_attempts WHERE phone IN (?,?)',(account['phone'],account['username']))


def sms_ready():
    return bool(re.fullmatch(r'AC[0-9a-fA-F]{32}',os.environ.get('TWILIO_ACCOUNT_SID','')) and os.environ.get('TWILIO_AUTH_TOKEN') and (os.environ.get('TWILIO_MESSAGING_SERVICE_SID') or os.environ.get('TWILIO_SMS_FROM')))


def send_sms_code(phone, code):
    if not sms_ready(): raise ValueError('خدمة رسائل SMS لم تُربط بعد؛ تواصل مع الدعم')
    sid=os.environ['TWILIO_ACCOUNT_SID']
    payload={'To':'+20'+normalized_mobile(phone)[1:], 'Body':f'رمز تأكيد ولعه: {code}. صالح لمدة 10 دقائق. لا تشاركه مع أحد.'}
    if os.environ.get('TWILIO_MESSAGING_SERVICE_SID'): payload['MessagingServiceSid']=os.environ['TWILIO_MESSAGING_SERVICE_SID']
    else: payload['From']=os.environ['TWILIO_SMS_FROM']
    credential=base64.b64encode((sid+':'+os.environ['TWILIO_AUTH_TOKEN']).encode()).decode()
    req=Request(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',data=urlencode(payload).encode(),headers={'Authorization':'Basic '+credential,'Content-Type':'application/x-www-form-urlencoded'})
    try:
        with urlopen(req,timeout=15) as response: result=json.load(response)
        if not isinstance(result,dict) or not result.get('sid') or result.get('status') not in ('accepted','queued','sending','sent','delivered'): raise ValueError()
    except (OSError,ValueError,TypeError):
        raise ValueError('تعذر إرسال رمز التأكيد للموبايل؛ حاول لاحقًا') from None


def limit(db, scope, actor, seconds, maximum):
    bucket = int(time.time()) // seconds
    db.execute('INSERT INTO feature_limits VALUES (?,?,?,1) ON CONFLICT(scope,actor,bucket) DO UPDATE SET count=count+1', (scope, actor, bucket))
    n = db.execute('SELECT count FROM feature_limits WHERE scope=? AND actor=? AND bucket=?', (scope, actor, bucket)).fetchone()['count']
    db.commit()  # Limits and failed code attempts must survive rejected requests.
    if n > maximum:
        raise ValueError('محاولات كثيرة؛ حاول لاحقًا')


def checked_code(db, data, purpose):
    ident = str(data.get('challenge_id', ''))
    row = db.execute('SELECT * FROM account_codes WHERE id=? AND purpose=?', (ident, purpose)).fetchone()
    if not row or row['used'] or row['expires'] <= int(time.time()) or row['attempts'] >= 5:
        raise ValueError('رمز التأكيد منتهي أو غير صالح؛ اطلب رمزًا جديدًا')
    db.execute('UPDATE account_codes SET attempts=attempts+1 WHERE id=?', (ident,))
    db.commit()
    candidate = hashlib.sha256((ident + ':' + str(data.get('code', ''))).encode()).hexdigest()
    if not hmac.compare_digest(row['code_hash'], candidate):
        raise ValueError('رمز التأكيد غير صحيح')
    return row


def complete_registration(db, data, create_user):
    if data.get('password') != data.get('confirm_password'):
        raise ValueError('كلمتا المرور غير متطابقتين')
    db.execute('BEGIN IMMEDIATE')
    code = checked_code(db, data, 'register')
    # checked_code commits attempts; reacquire a write lock and recheck consumption.
    db.execute('BEGIN IMMEDIATE')
    if db.execute('SELECT used FROM account_codes WHERE id=?', (code['id'],)).fetchone()['used']:
        raise ValueError('رمز التأكيد مستخدم بالفعل')
    mobile=code['email'].startswith('sms:')
    # Every customer account stores an Egyptian national mobile number, including
    # registrations verified by email. Do not let the email path bypass the
    # phone validation used by SMS registration.
    phone=normalized_mobile(data.get('phone',''))
    if mobile and phone != code['email'][4:]: raise ValueError('رقم الموبايل لا يطابق الرقم الذي تم تأكيده')
    data=dict(data,phone=phone)
    if not mobile and str(data.get('email', '')).strip().lower() != code['email']:
        raise ValueError('البريد لا يطابق البريد الذي تم تأكيده')
    uid = create_user(db, str(data.get('name', '')), str(data.get('phone', '')), 'customer', str(data.get('password', '')))
    if mobile: db.execute('UPDATE users SET verified_phone=? WHERE id=?',(code['email'][4:],uid))
    else: db.execute('UPDATE users SET email=? WHERE id=?', (code['email'], uid))
    db.execute('UPDATE account_codes SET used=1 WHERE id=?', (code['id'],))
    return uid


def feature_state(db, user):
    args = () if user['role'] == 'admin' else (user['id'],)
    clause = '' if user['role'] == 'admin' else ' WHERE r.sender_id=?'
    ratings = [dict(r) for r in db.execute('SELECT r.*,u.name AS sender_name,t.name AS target_name FROM ratings r JOIN users u ON u.id=r.sender_id JOIN users t ON t.id=r.target_id' + clause + ' ORDER BY r.at DESC LIMIT 500', args)]
    tickets = [dict(r) for r in db.execute('SELECT s.*,u.name FROM support_tickets s JOIN users u ON u.id=s.user_id' + ('' if user['role'] == 'admin' else ' WHERE s.user_id=?') + ' ORDER BY s.id DESC LIMIT 100', args)]
    pending=[dict(r) for r in db.execute("SELECT o.id,o.driver_id,'delivered' AS status FROM orders o WHERE o.user_id=? AND o.status='delivered' AND o.driver_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ratings r WHERE r.order_id=o.id AND r.sender_id=?) ORDER BY o.id DESC LIMIT 3",(user['id'],user['id']))] if user['role']=='customer' else []
    shift=dict(row) if user['role']=='driver' and (row:=db.execute('SELECT s.id,s.started_at,s.ended_at,s.second_photo_at FROM driver_shifts s JOIN drivers d ON d.id=s.driver_id WHERE d.user_id=? AND s.ended_at IS NULL',(user['id'],)).fetchone()) else None
    shifts=[dict(r) for r in db.execute('SELECT s.id,s.driver_id,s.started_at,s.ended_at,s.review_status,s.reviewed_at,s.restored_at,(length(s.selfie)>0) AS has_selfie,s.second_photo_at,(length(s.second_selfie)>0) AS has_second_selfie,s.second_review_status,s.second_reviewed_at,d.identity_blocked_shift,d.name,d.photo,u.email FROM driver_shifts s JOIN drivers d ON d.id=s.driver_id JOIN users u ON u.id=d.user_id ORDER BY s.id DESC LIMIT 100')] if user['role']=='admin' else []
    shift_required=user['role']=='driver' and (not shift or datetime.fromisoformat(shift['started_at']).timestamp()+86400<=time.time())
    second_required=bool(shift and not shift_required and not shift['second_photo_at'] and datetime.fromisoformat(shift['started_at']).timestamp()+21600<=time.time())
    rating=db.execute('SELECT ROUND(AVG(stars),2) AS average,COUNT(*) AS count FROM ratings WHERE target_id=?',(user['id'],)).fetchone()
    featured=[dict(r) for r in db.execute("""SELECT u.id,u.name,u.role,u.featured,ROUND(AVG(r.stars),2) AS average,COUNT(r.stars) AS rating_count,
        (SELECT COUNT(*) FROM orders o WHERE o.status='delivered' AND (o.user_id=u.id OR o.driver_id IN (SELECT id FROM drivers WHERE user_id=u.id))) AS completed,
        (SELECT id FROM drivers WHERE user_id=u.id) AS driver_id
        FROM users u LEFT JOIN ratings r ON r.target_id=u.id WHERE u.role IN ('customer','driver') AND u.disabled=0 GROUP BY u.id
        ORDER BY u.featured DESC,completed DESC,CASE WHEN COUNT(r.stars)>=3 THEN AVG(r.stars) ELSE 0 END DESC LIMIT 100""")] if user['role']=='admin' else []
    rewards=[dict(r) for r in db.execute('SELECT id,user_id,kind,amount_cents,created_at,used_order_id FROM featured_rewards ORDER BY id DESC LIMIT 100')] if user['role']=='admin' else []
    complaints=[dict(r) for r in db.execute('SELECT c.*,u.name AS customer_name,d.name AS driver_name FROM driver_complaints c JOIN users u ON u.id=c.customer_id JOIN drivers d ON d.id=c.driver_id ORDER BY c.id DESC LIMIT 100')] if user['role']=='admin' else []
    daily_resets=[dict(r) for r in db.execute('SELECT person_type,person_id,day,MAX(at) AS at FROM daily_counter_resets GROUP BY person_type,person_id,day ORDER BY day DESC LIMIT 2000')] if user['role']=='admin' else []
    daily_distances=[dict(r) for r in db.execute('SELECT driver_id,day,meters FROM driver_distance_daily ORDER BY day DESC')] if user['role']=='admin' else []
    chat_counts={str(r['order_id']):r['count'] for r in db.execute('SELECT order_id,COUNT(*) AS count FROM order_messages GROUP BY order_id')} if user['role']=='admin' else {}
    chat_threads=[dict(r) for r in db.execute('''SELECT m.order_id,o.customer,o.status,d.name AS driver_name,COUNT(*) AS message_count,MAX(m.id) AS last_message_id,MAX(m.at) AS last_at
        FROM order_messages m JOIN orders o ON o.id=m.order_id LEFT JOIN drivers d ON d.id=o.driver_id
        GROUP BY m.order_id ORDER BY last_message_id DESC''')] if user['role']=='admin' else []
    return {'admin_recovery_question': (db.execute('SELECT recovery_question FROM users WHERE id=?',(user['id'],)).fetchone()['recovery_question'] or '') if user['role']=='admin' else '', 'shift_required':shift_required,'shift_second_required':second_required,'driver_shift':shift,'driver_shifts':shifts,'my_rating':dict(rating),'featured_people':featured,'featured_rewards':rewards,'driver_complaints':complaints,'pending_ratings':pending,'ratings': ratings, 'support_tickets': tickets, 'chat_counts':chat_counts, 'daily_distances':daily_distances, 'daily_resets':daily_resets, 'chat_threads':chat_threads, 'email_otp_ready': mail_ready(), 'sms_otp_ready': sms_ready(), 'ai_ready': bool(os.environ.get('OPENAI_API_KEY'))}


def cairo_day_window(day):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(day)):
        raise ValueError('اختر تاريخًا صحيحًا')
    start=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=ZoneInfo('Africa/Cairo'))
    return start.astimezone(timezone.utc).isoformat(timespec='seconds'),(start+timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec='seconds')


def admin_activity(db, data):
    first,last=str(data.get('from','')),str(data.get('to',''))
    offset=int(data.get('offset',0))
    if offset<0 or offset>100000: raise ValueError('صفحة غير صالحة')
    start,_=cairo_day_window(first)
    _,end=cairo_day_window(last)
    if first>last or (datetime.strptime(last,'%Y-%m-%d')-datetime.strptime(first,'%Y-%m-%d')).days>366:
        raise ValueError('اختر فترة لا تزيد عن سنة')
    orders=[dict(r) for r in db.execute('SELECT o.id,o.created_at AS at,o.customer,o.total,o.status,o.driver_id,d.name AS driver_name FROM orders o LEFT JOIN drivers d ON d.id=o.driver_id WHERE o.created_at>=? AND o.created_at<? ORDER BY o.id DESC LIMIT 1000 OFFSET ?',(start,end,offset))]
    settlements=[dict(r) for r in db.execute('SELECT a.id,a.at,a.action,a.details,a.driver_id,d.name AS driver_name FROM wallet_audit a JOIN drivers d ON d.id=a.driver_id WHERE a.at>=? AND a.at<? ORDER BY a.id DESC LIMIT 1000 OFFSET ?',(start,end,offset))]
    events=[dict(r) for r in db.execute('SELECT e.id,e.at,e.action,e.order_id,o.customer,d.name AS driver_name FROM events e JOIN orders o ON o.id=e.order_id LEFT JOIN drivers d ON d.id=o.driver_id WHERE e.at>=? AND e.at<? ORDER BY e.id DESC LIMIT 1000 OFFSET ?',(start,end,offset))]
    resets=[dict(r) for r in db.execute("SELECT r.id,r.at,r.day,r.person_type,r.person_id,COALESCE(u.name,d.name) AS name FROM daily_counter_resets r LEFT JOIN users u ON r.person_type='customer' AND u.id=r.person_id LEFT JOIN drivers d ON r.person_type='driver' AND d.id=r.person_id WHERE r.at>=? AND r.at<? ORDER BY r.id DESC LIMIT 1000 OFFSET ?",(start,end,offset))]
    rewards=[dict(r) for r in db.execute('SELECT f.id,f.created_at AS at,f.kind,f.amount_cents,u.name AS name,u.role AS person_type FROM featured_rewards f JOIN users u ON u.id=f.user_id WHERE f.created_at>=? AND f.created_at<? ORDER BY f.id DESC LIMIT 1000 OFFSET ?',(start,end,offset))]
    return {'orders':orders,'events':events,'settlements':settlements,'resets':resets,'rewards':rewards,'has_more':any(len(group)>=1000 for group in (orders,events,settlements,resets,rewards))}


def feature_post(handler, db, path, data, user, create_user, areas, now):
    if path == '/api/auth/admin-recovery-question':
        limit(db,'admin-question-ip',handler.client_address[0],900,30)
        account=admin_recovery_account(db,data.get('username'))
        question=account['recovery_question'] if account and account['recovery_hash'] and account['recovery_question'] else 'ما إجابة الاسترداد السرية التي حفظتها في إعدادات حسابك؟'
        handler.respond({'question':question})
        return True
    if path == '/api/auth/admin-security/setup':
        remote=handler.client_address[0]
        limit(db,'admin-security-setup-ip',remote,900,10)
        identifier=str(data.get('username','')).strip()[:64]
        limit(db,'admin-security-setup-account',identifier.casefold(),900,5)
        account=admin_recovery_account(db,identifier)
        password=str(data.get('password',''))
        password_ok=False
        if account and password:
            candidate=hashlib.scrypt(password.encode(),salt=bytes.fromhex(account['salt']),n=2**14,r=8,p=1).hex()
            password_ok=hmac.compare_digest(candidate,account['password_hash'])
        if not password_ok or not account:
            raise ValueError('تعذر التحقق؛ راجع اسم المستخدم وكلمة المرور')
        has_recovery=bool(account['recovery_question'] and account['recovery_salt'] and account['recovery_hash'])
        recovery_question=account['recovery_question'] if has_recovery else str(data.get('security_question','')).strip()
        answer=normalize_recovery_answer(data.get('recovery_answer'))
        if has_recovery:
            answer_candidate=hashlib.scrypt(answer.encode(),salt=bytes.fromhex(account['recovery_salt']),n=2**14,r=8,p=1).hex()
            if not hmac.compare_digest(answer_candidate,account['recovery_hash']):
                raise ValueError('إجابة الاسترداد غير صحيحة')
        elif len(recovery_question)<8 or len(recovery_question)>180:
            raise ValueError('اكتب سؤال استرداد من 8 إلى 180 حرفًا')
        key=str(data.get('security_key',''))
        if len(key)<12 or len(key)>128 or key!=str(data.get('confirm_security_key','')):
            raise ValueError('رمز الأمان يجب أن يكون 12 حرفًا على الأقل، مع تأكيد مطابق')
        salt=secrets.token_hex(16)
        digest=hashlib.scrypt(key.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
        if has_recovery:
            db.execute('UPDATE users SET security_salt=?,security_hash=? WHERE id=?',(salt,digest,account['id']))
        else:
            recovery_salt=secrets.token_hex(16)
            recovery_hash=hashlib.scrypt(answer.encode(),salt=bytes.fromhex(recovery_salt),n=2**14,r=8,p=1).hex()
            db.execute('UPDATE users SET security_salt=?,security_hash=?,recovery_question=?,recovery_salt=?,recovery_hash=? WHERE id=?',(salt,digest,recovery_question,recovery_salt,recovery_hash,account['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?',(account['id'],))
        handler.respond({'ok':True})
        return True
    if path == '/api/auth/admin-recover':
        recover_admin_by_answer(db,data,handler.client_address[0])
        handler.respond({'ok':True})
        return True
    if path == '/api/account/admin-recovery-question':
        if not user or user['role']!='admin' or user['staff_permissions'] is not None:
            handler.respond({'error':'هذا الإعداد للمسؤول الرئيسي فقط'},403)
            return True
        limit(db,'admin-question-setup',str(user['id']),900,5)
        account=db.execute('SELECT salt,password_hash FROM users WHERE id=?',(user['id'],)).fetchone()
        digest=hashlib.scrypt(str(data.get('current_password','')).encode(),salt=bytes.fromhex(account['salt']),n=2**14,r=8,p=1).hex()
        if not hmac.compare_digest(digest,account['password_hash']):
            handler.respond({'error':'كلمة المرور الحالية غير صحيحة'},403)
            return True
        question=' '.join(str(data.get('question','')).split())
        if not 8<=len(question)<=180:
            raise ValueError('اكتب سؤالًا من 8 إلى 180 حرفًا')
        answer=normalize_recovery_answer(data.get('recovery_answer'))
        if answer!=normalize_recovery_answer(data.get('confirm_answer')):
            raise ValueError('الإجابتان غير متطابقتين')
        set_recovery_answer(db,user['id'],answer)
        db.execute('UPDATE users SET recovery_question=? WHERE id=?',(question,user['id']))
        handler.respond({'ok':True})
        return True
    if path == '/api/auth/recover':
        recover_account(db,data,handler.client_address[0])
        handler.respond({'ok':True})
        return True
    if path == '/api/account/recovery-answer':
        if not user or user['role'] not in ('customer','driver'):
            handler.respond({'error':'سجّل الدخول أولًا لتحديث إجابة الاسترداد'},401)
            return True
        account=db.execute('SELECT salt,password_hash FROM users WHERE id=?',(user['id'],)).fetchone()
        current=str(data.get('current_password',''))
        digest=hashlib.scrypt(current.encode(),salt=bytes.fromhex(account['salt']),n=2**14,r=8,p=1).hex()
        if not hmac.compare_digest(digest,account['password_hash']):
            handler.respond({'error':'كلمة المرور الحالية غير صحيحة'},403)
            return True
        set_recovery_answer(db,user['id'],data.get('recovery_answer'))
        handler.respond({'ok':True})
        return True
    if path in ('/api/admin/activity','/api/admin/daily/reset'):
        if not user or user['role']!='admin':
            handler.respond({'error':'خاص بالإدارة فقط'},403)
            return True
        if path.endswith('/activity'):
            handler.respond(admin_activity(db,data))
            return True
        role,person_id,day=str(data.get('person_type','')),int(data.get('person_id',0)),str(data.get('day',''))
        if day!=datetime.now(ZoneInfo('Africa/Cairo')).strftime('%Y-%m-%d'):
            raise ValueError('يمكن تصفير عداد اليوم الحالي فقط')
        table='users' if role=='customer' else 'drivers' if role=='driver' else ''
        if not table or not db.execute(f'SELECT 1 FROM {table} WHERE id=?'+(" AND role='customer'" if role=='customer' else ''),(person_id,)).fetchone():
            raise ValueError('الحساب غير موجود')
        db.execute('INSERT INTO daily_counter_resets(person_type,person_id,day,admin_id,at) VALUES (?,?,?,?,?)',(role,person_id,day,user['id'],now()))
        handler.respond({'ok':True})
        return True
    if path == '/api/auth/send-code':
        mobile=bool(data.get('phone'))
        phone=normalized_mobile(data['phone']) if mobile else ''
        email='sms:'+phone if mobile else str(data.get('email','')).strip().lower()
        purpose = str(data.get('purpose', 'register'))
        if purpose not in ('register', 'reset', 'link'):
            raise ValueError('طلب غير صالح')
        if purpose=='link' and user and user['role']=='driver':
            handler.respond({'error':'إيميل الطيار يحدده المسؤول من الداشبورد فقط'},403)
            return True
        if not mobile and (len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
            raise ValueError('أدخل بريدًا إلكترونيًا صحيحًا')
        limit(db, 'otp-ip', handler.client_address[0], 600, 10)
        limit(db, 'otp-email', email, 60, 1)
        if not (sms_ready() if mobile else mail_ready()):
            raise ValueError('خدمة إرسال رمز التأكيد لم تُربط بعد؛ تواصل مع الدعم')
        if purpose == 'link' and not user:
            handler.respond({'error':'سجل الدخول أولًا'},401)
            return True
        if mobile:
            if purpose=='link': raise ValueError('استخدم ربط البريد من إعدادات الحساب')
            account=next((u for u in db.execute('SELECT id,phone FROM users WHERE disabled=0') if re.sub(r'[^0-9]','',str(u['phone'])) in (phone,'20'+phone[1:],'0020'+phone[1:])),None)
        else: account = db.execute('SELECT id FROM users WHERE email=? AND disabled=0', (email,)).fetchone()
        if purpose == 'link' and account and account['id'] != user['id']:
            raise ValueError('هذا البريد مستخدم لحساب آخر')
        ident = secrets.token_urlsafe(24)
        # Reset responses never reveal whether an account exists.
        if purpose == 'reset' and not account:
            handler.respond({'ok': True, 'challenge_id': ident, 'message': ('إذا كان رقم الموبايل مسجلًا فسيصلك رمز التأكيد' if mobile else 'إذا كان البريد مسجلًا فسيصلك رمز التأكيد')})
            return True
        code = f'{secrets.randbelow(1000000):06d}'
        send_sms_code(phone,code) if mobile else send_code(email, code)
        ts = int(time.time())
        db.execute('UPDATE account_codes SET used=1 WHERE email=? AND purpose=? AND used=0', (email, purpose))
        db.execute('INSERT INTO account_codes(id,purpose,email,user_id,code_hash,expires,created,remote) VALUES (?,?,?,?,?,?,?,?)', (ident, purpose, email, user['id'] if purpose == 'link' else account['id'] if account else None, hashlib.sha256((ident + ':' + code).encode()).hexdigest(), ts + 600, ts, handler.client_address[0]))
        handler.respond({'ok': True, 'challenge_id': ident, 'message': ('إذا كان رقم الموبايل مسجلًا فسيصلك رمز التأكيد' if mobile else 'إذا كان البريد مسجلًا فسيصلك رمز التأكيد') if purpose == 'reset' else ('تم طلب إرسال رمز التأكيد برسالة SMS إلى موبايلك' if mobile else 'تم إرسال رمز التأكيد إلى بريدك')})
        return True
    if path == '/api/auth/link-email':
        if user and user['role']=='driver':
            handler.respond({'error':'إيميل الطيار يحدده المسؤول من الداشبورد فقط'},403)
            return True
        if not user:
            handler.respond({'error':'سجل الدخول أولًا'},401)
            return True
        account=db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        digest=hashlib.scrypt(str(data.get('current_password','')).encode(),salt=bytes.fromhex(account['salt']),n=2**14,r=8,p=1).hex()
        if not hmac.compare_digest(digest,account['password_hash']):
            raise ValueError('كلمة المرور الحالية غير صحيحة')
        db.execute('BEGIN IMMEDIATE')
        code=checked_code(db,data,'link')
        db.execute('BEGIN IMMEDIATE')
        if code['user_id']!=user['id'] or db.execute('SELECT used FROM account_codes WHERE id=?',(code['id'],)).fetchone()['used']:
            raise ValueError('رمز غير صالح لهذا الحساب')
        db.execute('UPDATE users SET email=? WHERE id=?',(code['email'],user['id']))
        db.execute('UPDATE account_codes SET used=1 WHERE id=?',(code['id'],))
        handler.respond({'ok':True})
        return True
    if path == '/api/auth/reset-password':
        password = str(data.get('password', ''))
        if len(password) < 10 or password != data.get('confirm_password'):
            raise ValueError('اكتب كلمة مرور من 10 أحرف على الأقل وأكدها بنفس القيمة')
        db.execute('BEGIN IMMEDIATE')
        row = checked_code(db, data, 'reset')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT used FROM account_codes WHERE id=?', (row['id'],)).fetchone()['used'] or not row['user_id']:
            raise ValueError('رمز التأكيد غير صالح')
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
        db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=? AND disabled=0', (salt, digest, row['user_id']))
        db.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
        db.execute('UPDATE account_codes SET used=1 WHERE id=?', (row['id'],))
        handler.respond({'ok': True})
        return True
    if path not in ('/api/driver/update', '/api/driver/delete', '/api/driver/restore', '/api/rating', '/api/driver/complaint', '/api/support', '/api/support/reply'):
        return False
    if not user:
        handler.respond({'error': 'سجل الدخول أولًا'}, 401)
        return True
    if path.startswith('/api/driver/') and path != '/api/driver/complaint':
        if user['role'] != 'admin':
            handler.respond({'error': 'غير مصرح'}, 403)
            return True
        driver = db.execute('SELECT * FROM drivers WHERE id=?', (int(data.get('id', 0)),)).fetchone()
        if not driver:
            raise ValueError('الطيار غير موجود')
        if path == '/api/driver/update':
            name, phone = str(data.get('name', '')).strip(), str(data.get('phone', '')).strip()
            username = normalized_username(data.get('username', ''))
            if not name or len(name) > 100 or not re.fullmatch(r'[+0-9٠-٩ ()-]{7,25}', phone) or data.get('area') not in areas:
                raise ValueError('راجع اسم الطيار وهاتفه ومنطقته')
            if 'email' in data:
                email=str(data.get('email','')).strip().lower()
                if email and (len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)): raise ValueError('أدخل إيميل الطيار الصحيح')
                if email and db.execute('SELECT 1 FROM users WHERE email=? AND id<>?',(email,driver['user_id'])).fetchone(): raise ValueError('الإيميل مستخدم بحساب آخر')
                db.execute('UPDATE users SET email=? WHERE id=?',(email or None,driver['user_id']))
            db.execute('UPDATE users SET name=?,phone=?,username=? WHERE id=?', (name, phone, username, driver['user_id']))
            db.execute('UPDATE drivers SET name=?,phone=?,area=? WHERE id=?', (name, phone, data['area'], driver['id']))
        else:
            disabled = 1 if path.endswith('/delete') else 0
            if disabled and db.execute("SELECT 1 FROM orders WHERE driver_id=? AND status NOT IN ('delivered','cancelled')", (driver['id'],)).fetchone():
                raise ValueError('أعد إسناد الطلبات الجارية قبل حذف الطيار')
            db.execute('UPDATE users SET disabled=? WHERE id=?', (disabled, driver['user_id']))
            db.execute('UPDATE drivers SET available=0 WHERE id=?', (driver['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?', (driver['user_id'],))
    elif path == '/api/rating':
        oid = int(data.get('order_id', 0))
        order = db.execute('SELECT o.*,d.user_id AS driver_user FROM orders o LEFT JOIN drivers d ON d.id=o.driver_id WHERE o.id=?', (oid,)).fetchone()
        stars = int(data.get('stars', 0))
        if not order or order['status'] != 'delivered' or not order['driver_user'] or not 1 <= stars <= 5:
            raise ValueError('التقييم متاح بعد تسليم الطلب فقط من 1 إلى 5')
        if user['role'] == 'customer' and order['user_id'] == user['id']:
            target = order['driver_user']
        elif user['role'] == 'driver' and order['driver_user'] == user['id']:
            target = order['user_id']
        else:
            handler.respond({'error': 'غير مصرح بتقييم هذا الطلب'}, 403)
            return True
        comment = str(data.get('comment', '')).strip()[:500]
        db.execute('INSERT INTO ratings VALUES (?,?,?,?,?,?) ON CONFLICT(order_id,sender_id) DO UPDATE SET stars=excluded.stars,comment=excluded.comment,at=excluded.at', (oid, user['id'], target, stars, comment, now()))
    elif path == '/api/driver/complaint':
        oid=int(data.get('order_id',0))
        order=db.execute('SELECT id,driver_id FROM orders WHERE id=? AND user_id=? AND driver_id IS NOT NULL',(oid,user['id'])).fetchone() if user['role']=='customer' else None
        if not order: raise ValueError('الشكوى متاحة للعميل بعد قبول الطيار لطلبه')
        category=str(data.get('category','')).strip()
        if category not in ('تأخير','أسلوب التعامل','مشكلة في الطلب','مشكلة في القيادة','أخرى'): raise ValueError('اختر نوع الشكوى')
        details=str(data.get('details','')).strip()
        if len(details)>1000: raise ValueError('التفاصيل حتى ١٠٠٠ حرف')
        db.execute('INSERT INTO driver_complaints(order_id,customer_id,driver_id,category,details,at) VALUES (?,?,?,?,?,?) ON CONFLICT(order_id,customer_id) DO UPDATE SET category=excluded.category,details=excluded.details,at=excluded.at,status=\'open\'',(oid,user['id'],order['driver_id'],category,details,now()))
    elif path == '/api/support/reply':
        if user['role'] != 'admin':
            handler.respond({'error': 'غير مصرح'}, 403)
            return True
        answer = str(data.get('answer', '')).strip()
        if not answer or len(answer) > 2000:
            raise ValueError('اكتب ردًا حتى 2000 حرف')
        cur = db.execute("UPDATE support_tickets SET answer=?,ai=0,status='closed' WHERE id=?", (answer, int(data.get('id', 0))))
        if not cur.rowcount:
            raise ValueError('رسالة الدعم غير موجودة')
    elif path == '/api/support':
        message = str(data.get('message', '')).strip()
        if not message or len(message) > 1000:
            raise ValueError('اكتب رسالتك حتى 1000 حرف')
        limit(db, 'support', str(user['id']), 3600, 20)
        answer, ai = '', 0
        if os.environ.get('OPENAI_API_KEY') and data.get('use_ai'):
            payload = {'model': os.environ.get('WALLAHA_AI_MODEL', 'gpt-4.1-mini'), 'store': False, 'max_output_tokens': 350,
                       'instructions': 'أنت مساعد دعم تطبيق ولعه. رد بالعربية باختصار. الروشتة إلزامية لطلبات الأدوية. رمز إنشاء الحساب واسترجاع كلمة المرور بالبريد. لا تطلب كلمة مرور أو OTP. لا تقدم تشخيصًا أو وصف دواء أو جرعات. لا تدعي تعديل الطلبات أو الحسابات ولا تخترع حالة طلب أو أسعارًا. عند مشكلة حساب أو دفع اطلب التواصل مع المسؤول.', 'input': message}
            try:
                req = Request('https://api.openai.com/v1/responses', data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
                with urlopen(req, timeout=20) as response:
                    result = json.load(response)
                answer = '\n'.join(c['text'] for item in result.get('output', []) for c in item.get('content', []) if c.get('type') == 'output_text')[:2000]
                ai = int(bool(answer))
            except (OSError, ValueError, KeyError):
                pass
        cur = db.execute('INSERT INTO support_tickets(user_id,message,answer,ai,at) VALUES (?,?,?,?,?)', (user['id'], message, answer, ai, now()))
        handler.respond({'ok': True, 'id': cur.lastrowid, 'answer': answer or 'وصلت رسالتك للدعم، وسيراجعها المسؤول.', 'ai': bool(ai)})
        return True
    handler.respond({'ok': True})
    return True
