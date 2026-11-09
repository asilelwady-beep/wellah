"""Read receipts and opt-in Web Push delivery for order conversations."""
import base64
import importlib.util
import hashlib
import json
import logging
import re
import threading
import time
from urllib.parse import urlsplit


def init_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS chat_reads(
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
          driver_id INTEGER NOT NULL, through_id INTEGER NOT NULL,
          PRIMARY KEY(user_id,order_id,driver_id));
        CREATE TABLE IF NOT EXISTS chat_push_key(id INTEGER PRIMARY KEY, scalar TEXT NOT NULL, public TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chat_push_devices(
          id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL, role TEXT NOT NULL, user_id INTEGER NOT NULL,
          session_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
          subscription TEXT NOT NULL, UNIQUE(endpoint,role));
        CREATE TABLE IF NOT EXISTS chat_push_queue(
          device_id INTEGER NOT NULL REFERENCES chat_push_devices(id) ON DELETE CASCADE,
          message_id INTEGER NOT NULL REFERENCES order_messages(id) ON DELETE CASCADE,
          retry_at INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(device_id,message_id));
    """)


def unread(db,user_id,order_id,driver_id):
    row=db.execute("""SELECT COUNT(*) AS count, MAX(id) AS latest FROM order_messages
        WHERE order_id=? AND driver_id=? AND sender_id!=? AND id>COALESCE(
          (SELECT through_id FROM chat_reads WHERE user_id=? AND order_id=? AND driver_id=?),0)""",
        (order_id,driver_id,user_id,user_id,order_id,driver_id)).fetchone()
    return {'unread_count':row['count'],'latest_incoming':row['latest'] or 0}


def mark_read(db,user_id,order_id,driver_id,through):
    db.execute("""INSERT INTO chat_reads(user_id,order_id,driver_id,through_id)
        SELECT ?,?,?,MAX(id) FROM order_messages WHERE order_id=? AND driver_id=?
        AND sender_id!=? AND id<=? HAVING MAX(id) IS NOT NULL
        ON CONFLICT(user_id,order_id,driver_id) DO UPDATE SET through_id=MAX(through_id,excluded.through_id)""",
        (user_id,order_id,driver_id,order_id,driver_id,user_id,int(through)))


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def public_key(db):
    if importlib.util.find_spec('pywebpush') is None:
        raise ValueError('إشعارات الجهاز غير متاحة على هذا الخادم')
    row=db.execute('SELECT public FROM chat_push_key WHERE id=1').fetchone()
    if row:return row['public']
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key=ec.generate_private_key(ec.SECP256R1())
    public=b64url(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint))
    scalar=b64url(key.private_numbers().private_value.to_bytes(32,'big'))
    db.execute('INSERT OR IGNORE INTO chat_push_key VALUES(1,?,?)',(scalar,public))
    return db.execute('SELECT public FROM chat_push_key WHERE id=1').fetchone()['public']


def subscribe(db,user,authorization,subscription):
    if not isinstance(subscription,dict):raise ValueError('اشتراك تنبيهات غير صالح')
    endpoint=subscription.get('endpoint','')
    url=urlsplit(endpoint)
    providers={'fcm.googleapis.com':'/fcm/send/', 'updates.push.services.mozilla.com':'/wpush/', 'web.push.apple.com':'/'}
    prefix=providers.get(url.netloc)
    if len(endpoint)>3000 or url.scheme!='https' or not prefix or not url.path.startswith(prefix) or len(url.path)<=len(prefix):
        raise ValueError('خدمة إشعارات المتصفح غير مدعومة على هذا الجهاز')
    keys=subscription.get('keys',{})
    for name,size in [('auth',16),('p256dh',65)]:
        encoded=keys.get(name,'') if isinstance(keys,dict) else ''
        if not isinstance(encoded,str) or not re.fullmatch('[A-Za-z0-9_-]{20,90}={0,2}',encoded):
            raise ValueError('مفتاح اشتراك غير صالح')
        raw=base64.urlsafe_b64decode(encoded+'===')
        if len(raw)!=size or (name=='p256dh' and raw[0]!=4):raise ValueError('مفتاح اشتراك غير صالح')
    digest=hashlib.sha256(authorization[7:].encode()).hexdigest()
    # Rebinding an endpoint to another account also removes its undelivered work.
    db.execute('DELETE FROM chat_push_devices WHERE endpoint=? AND role=? AND user_id!=?',(endpoint,user['role'],user['id']))
    db.execute("""INSERT INTO chat_push_devices(endpoint,role,user_id,session_hash,subscription) VALUES(?,?,?,?,?)
        ON CONFLICT(endpoint,role) DO UPDATE SET session_hash=excluded.session_hash,subscription=excluded.subscription""",
        (endpoint,user['role'],user['id'],digest,json.dumps({'endpoint':endpoint,'keys':keys})))


def enqueue(db,message_id,recipient):
    db.execute('INSERT OR IGNORE INTO chat_push_queue(device_id,message_id) SELECT id,? FROM chat_push_devices WHERE user_id=?',
               (message_id,recipient))


def deliver_next(connect,send):
    with connect() as db:
        job=db.execute("""SELECT q.*, d.subscription,d.user_id,d.role,m.order_id,m.driver_id,k.scalar,
            o.status,o.driver_id AS assigned,o.user_id AS customer_id,c.user_id AS courier_id,s.expires,u.disabled,
            COALESCE(r.through_id,0) AS through_id FROM chat_push_queue q JOIN chat_push_devices d ON d.id=q.device_id
            JOIN chat_push_key k ON k.id=1 JOIN sessions s ON s.token_hash=d.session_hash JOIN users u ON u.id=d.user_id
            JOIN order_messages m ON m.id=q.message_id JOIN orders o ON o.id=m.order_id JOIN drivers c ON c.id=m.driver_id
            LEFT JOIN chat_reads r ON r.user_id=d.user_id AND r.order_id=m.order_id AND r.driver_id=m.driver_id
            WHERE q.retry_at<=? ORDER BY q.retry_at,q.message_id LIMIT 1""",(int(time.time()),)).fetchone()
    if job is None:return False
    attempts=job['attempts'];remove_device=False;retry=False
    allowed=job['user_id']==(job['customer_id'] if job['role']=='customer' else job['courier_id'])
    if (allowed and not job['disabled'] and (not job['expires'] or job['expires']>time.time()) and
        job['message_id']>job['through_id'] and job['driver_id']==job['assigned'] and
        job['status'] in ('assigned','ready','picked_up','on_way','returning')):
        try:
            send(subscription_info=json.loads(job['subscription']),data=json.dumps({'order':job['order_id'],
                 'role':job['role'],'message':job['message_id']}),vapid_private_key=job['scalar'],
                 vapid_claims={'sub':'https://walla3ha.com'},ttl=900,timeout=8)
        except Exception as error:
            status=getattr(getattr(error,'response',None),'status_code',None)
            remove_device=status in (404,410)
            retry=not remove_device and attempts<6 and (not status or status==429 or status>=500)
            logging.warning('Chat push delivery failed (status %s)',status)
    with connect() as db:
        if remove_device:db.execute('DELETE FROM chat_push_devices WHERE id=?',(job['device_id'],))
        elif retry:db.execute('UPDATE chat_push_queue SET attempts=attempts+1,retry_at=? WHERE device_id=? AND message_id=?',
                              (int(time.time())+min(300,5*2**attempts),job['device_id'],job['message_id']))
        else:db.execute('DELETE FROM chat_push_queue WHERE device_id=? AND message_id=?',(job['device_id'],job['message_id']))
    return True


def start_worker(connect):
    try:
        from pywebpush import webpush
    except ImportError:
        logging.warning('Install requirements.txt to enable chat Web Push on this server')
        return
    def work():
        while True:
            try:
                if deliver_next(connect,webpush):continue
            except Exception:
                logging.exception('Could not process the chat notification queue')
            time.sleep(4)
    threading.Thread(target=work,name='chat-push',daemon=True).start()
