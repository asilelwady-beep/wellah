import base64
import json
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import account_support as features


class AccountSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        server.DB=Path(cls.temp.name)/'test.sqlite3'
        os.environ['WALLAHA_ADMIN_PASSWORD']='test-owner-password-123'
        os.environ['WALLAHA_ADMIN_PHONE']='01000000000'
        server.init()
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        with server.connect() as db:
            cls.customer=server.create_user(db,'عميل اختبار','01000000001','customer','customer-password-123')
            cls.other=server.create_user(db,'عميل آخر','01000000002','customer','customer-password-123')
            cls.driver=server.create_user(db,'طيار اختبار','01000000003','driver','driver-password-123','طيار_تجربة')
            cls.did=db.execute('INSERT INTO drivers(user_id,name,phone,area) VALUES (?,?,?,?)',(cls.driver,'طيار اختبار','01000000003',server.AREAS[0])).lastrowid
            cls.mid=db.execute('INSERT INTO merchants(name,category,area,address,lat,lon) VALUES (?,?,?,?,?,?)',('صيدلية','أدوية',server.AREAS[0],'عنوان الصيدلية',29.85,31.27)).lastrowid
            cls.pid=db.execute('INSERT INTO products(name,category,price,stock,merchant_id,requires_prescription) VALUES (?,?,?,?,?,0)',('دواء اختبار','أدوية',10,10,cls.mid)).lastrowid
        cls.ct=cls.post('login',{'phone':'01000000001','password':'customer-password-123'})[1]['token']
        cls.ot=cls.post('login',{'phone':'01000000002','password':'customer-password-123'})[1]['token']
        cls.dt=cls.post('login',{'phone':'طيار_تجربة','password':'driver-password-123'})[1]['token']
        cls.at=cls.post('admin/login',{'phone':'owner','password':'test-owner-password-123'})[1]['token']

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.temp.cleanup()

    @classmethod
    def post(cls,path,data,token=''):
        req=Request(cls.base+'/api/'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        try:
            with urlopen(req) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)

    def test_sms_registration_binds_phone_and_is_single_use(self):
        phone='01000000121'
        with patch.object(features,'sms_ready',return_value=True), patch.object(features,'limit'), patch.object(features,'send_sms_code') as send:
            status,response=self.post('auth/send-code',{'phone':'+201000000121','purpose':'register'})
        self.assertEqual(status,200)
        code=send.call_args.args[1]
        data={'name':'اختبار موبايل','phone':'01000000122','password':'mobile-password-123','confirm_password':'mobile-password-123','challenge_id':response['challenge_id'],'code':code}
        self.assertEqual(self.post('register',data)[0],400)
        data['phone']=phone
        self.assertEqual(self.post('register',data)[0],200)
        self.assertEqual(self.post('register',data)[0],400)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT verified_phone FROM users WHERE phone=?',(phone,)).fetchone()['verified_phone'],phone)
        with patch.object(features,'sms_ready',return_value=True), patch.object(features,'limit'), patch.object(features,'send_sms_code') as send:
            status,response=self.post('auth/send-code',{'phone':phone,'purpose':'reset'})
        self.assertEqual(status,200)
        self.assertEqual(self.post('auth/reset-password',{'challenge_id':response['challenge_id'],'code':send.call_args.args[1],'password':'changed-mobile-password','confirm_password':'changed-mobile-password'})[0],200)

    def test_sms_unconfigured_does_not_create_challenge(self):
        with patch.object(features,'sms_ready',return_value=False):
            self.assertEqual(self.post('auth/send-code',{'phone':'01000000123','purpose':'register'})[0],400)
        self.assertEqual(features.normalized_mobile('٠١٠٠٠٠٠٠١٢٣'),'01000000123')

    @classmethod
    def state_for(cls, token):
        with urlopen(Request(cls.base+'/api/state',headers={'Authorization':'Bearer '+token})) as response: return json.load(response)

    def wallet_order(self, status='delivered'):
        with server.connect() as db:
            return db.execute("INSERT INTO orders(user_id,kind,customer,phone,area,address,payment,payment_status,status,total,delivery_fee,driver_id,created_at,driver_earning_cents,commission_percent,commission_cents,commission_locked,cash_collected) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(self.customer,'delivery','اختبار محفظة','01000000001',server.AREAS[0],'عنوان','cash','confirmed',status,100,100,self.did,server.now(),9000,10,1000,1,1)).lastrowid

    def test_wallet_reauthentication_settlement_and_redaction(self):
        self.dt=self.post('login',{'phone':'01000000003','password':'driver-password-123'})[1]['token']
        oid=self.wallet_order()
        owner=self.state_for(self.at)
        self.assertFalse(owner['wallet_unlocked']);self.assertEqual(owner['driver_wallets'],{})
        for tok in (self.ct,self.dt):
            self.assertEqual(self.post('admin/wallet/unlock',{'wallet_password':'test-owner-password-123'},tok)[0],403)
        self.assertEqual(self.post('admin/wallet/unlock',{'wallet_password':'wrong'},self.at)[0],400)
        self.assertEqual(self.post('admin/wallet/unlock',{'wallet_password':'test-owner-password-123'},self.at)[0],200)
        driver=self.state_for(self.dt)
        self.assertNotIn('commission_due',driver['driver_wallet']);self.assertNotIn('audit',driver['driver_wallet'])
        order=next(o for o in driver['orders'] if o['id']==oid)
        self.assertFalse(any(k.startswith('commission_') for k in order))
        self.assertEqual(order['driver_earning_cents'],9000)
        before=self.state_for(self.at)['driver_wallets'][str(self.did)]['balance']
        change={'driver_id':self.did,'action':'adjust','amount':'25','reason':'تعديل موثق','wallet_password':'wrong','request_id':'wallet-adjust-test-001'}
        self.assertEqual(self.post('admin/wallet/change',change,self.at)[0],400)
        self.assertEqual(self.state_for(self.at)['driver_wallets'][str(self.did)]['balance'],before)
        change['wallet_password']='test-owner-password-123'
        self.assertEqual(self.post('admin/wallet/change',change,self.at)[0],200)
        self.assertEqual(self.post('admin/wallet/change',change,self.at)[0],200)
        wallet=self.state_for(self.at)['driver_wallets'][str(self.did)]
        self.assertEqual(wallet['balance'],before+25);self.assertEqual(len(wallet['adjustments']),1)
        change.update(action='settle',reason='تم استلام وصرف الفلوس',request_id='wallet-settle-test-001')
        self.assertEqual(self.post('admin/wallet/change',change,self.at)[0],200)
        wallet=self.state_for(self.at)['driver_wallets'][str(self.did)]
        self.assertEqual(wallet['balance'],0);self.assertEqual(wallet['cash_due'],0);self.assertEqual(wallet['commission_due'],0)
        self.assertTrue(wallet['entries']);self.assertEqual(len(wallet['audit']),2)
        self.assertEqual(self.post('admin/wallet/lock',{},self.at)[0],200)
        self.assertEqual(self.post('admin/wallet/change',dict(change,request_id='wallet-locked-test-001'),self.at)[0],400)
        with server.connect() as db: self.assertIsNotNone(db.execute('SELECT id FROM orders WHERE id=?',(oid,)).fetchone())
        self.assertEqual(self.post('driver/earning',{'id':oid,'paid':True,'wallet_password':'test-owner-password-123'},self.at)[0],400)
        self.assertEqual(self.post('admin/wallet/unlock',{'wallet_password':'test-owner-password-123'},self.at)[0],200)
        with server.connect() as db: db.execute('UPDATE wallet_unlocks SET expires=0')
        self.assertFalse(self.state_for(self.at)['wallet_unlocked'])
        self.assertEqual(self.state_for(self.at)['driver_wallets'],{})

    def test_archived_chat_remains_for_admin_and_completed_order_hidden_from_customer(self):
        oid=self.wallet_order('assigned')
        self.assertEqual(self.post('order/chat',{'order_id':oid,'mode':'send','body':'محادثة محفوظة','request_id':'chat-archive-test-001'},self.ct)[0],200)
        with server.connect() as db: db.execute("UPDATE orders SET status='delivered' WHERE id=?",(oid,))
        self.assertFalse(any(o['id']==oid for o in self.state_for(self.ct)['orders']))
        self.assertEqual(self.post('order/chat',{'order_id':oid,'mode':'list'},self.ct)[0],403)
        for tok in (self.ct,self.dt): self.assertEqual(self.post('admin/chat/archive',{'order_id':oid},tok)[0],403)
        status,data=self.post('admin/chat/archive',{'order_id':oid},self.at)
        self.assertEqual(status,200);self.assertEqual(data['messages'][0]['body'],'محادثة محفوظة')
        self.assertEqual(data['messages'][0]['sender_role'],'customer')
        self.assertTrue(any(o['id']==oid for o in self.state_for(self.at)['orders']))
        self.assertTrue(any(r['id']==oid for r in self.state_for(self.ct)['pending_ratings']))

    def test_registration_requires_otp(self):
        code,result=self.post('register',{'name':'جديد','phone':'01000000004','password':'new-password-123','confirm_password':'new-password-123'})
        self.assertEqual(code,400);self.assertIn('رمز',result['error'])

    def test_otp_registration_single_use_and_password_reset(self):
        sent={}
        with patch.object(features,'mail_ready',return_value=True),patch.object(features,'send_code',side_effect=lambda email,code:sent.update({email:code})):
            code,r=self.post('auth/send-code',{'email':'new@example.test','purpose':'register'});self.assertEqual(code,200)
            data={'name':'جديد','phone':'01000000004','email':'new@example.test','password':'new-password-123','confirm_password':'new-password-123','challenge_id':r['challenge_id'],'code':sent['new@example.test']}
            self.assertEqual(self.post('register',data)[0],200)
            self.assertEqual(self.post('register',data)[0],400)
            with server.connect() as db:db.execute("DELETE FROM feature_limits WHERE scope='otp-email'")
            code,r=self.post('auth/send-code',{'email':'new@example.test','purpose':'reset'});self.assertEqual(code,200)
            reset={'challenge_id':r['challenge_id'],'code':sent['new@example.test'],'password':'replacement-pass-123','confirm_password':'replacement-pass-123'}
            self.assertEqual(self.post('auth/reset-password',reset)[0],200)
            self.assertEqual(self.post('login',{'phone':'new@example.test','password':'new-password-123'})[0],401)
            self.assertEqual(self.post('login',{'phone':'new@example.test','password':'replacement-pass-123'})[0],200)
            self.assertEqual(self.post('auth/reset-password',reset)[0],400)

    def test_code_attempts_survive_failed_requests(self):
        with patch.object(features,'mail_ready',return_value=True),patch.object(features,'send_code'):
            _,r=self.post('auth/send-code',{'email':'attempts@example.test','purpose':'register'})
        for _ in range(6):
            self.assertEqual(self.post('register',{'email':'attempts@example.test','password':'password-123456','confirm_password':'password-123456','challenge_id':r['challenge_id'],'code':'bad'})[0],400)
        with server.connect() as db:self.assertEqual(db.execute('SELECT attempts FROM account_codes WHERE id=?',(r['challenge_id'],)).fetchone()['attempts'],5)

    def test_medicine_without_product_flag_requires_prescription(self):
        data={'client_request_id':'medicine-test','kind':'products','area':server.AREAS[0],'payment':'cash','address':'عنوان العميل','latitude':29.85,'longitude':31.27,'merchant_id':self.mid,'items':[{'product_id':self.pid,'quantity':1}]}
        code,result=self.post('order',data,self.ct);self.assertEqual(code,400);self.assertIn('الوصفة',result['error'])
        data['prescription']='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*60).decode()
        code,result=self.post('order',data,self.ct);self.assertEqual(code,200,result)
        with server.connect() as db:self.assertEqual(db.execute('SELECT status FROM orders WHERE id=?',(result['id'],)).fetchone()['status'],'medicine_review')

    def test_driver_permissions_edit_delete_restore(self):
        self.assertEqual(self.post('driver/update',{'id':self.did},self.ct)[0],403)
        data={'id':self.did,'name':'اسم جديد','phone':'01000000003','username':'اسم_جديد','area':server.AREAS[0]}
        self.assertEqual(self.post('driver/update',data,self.at)[0],200)
        self.assertEqual(self.post('driver/delete',{'id':self.did},self.at)[0],200)
        self.assertEqual(self.post('login',{'phone':'اسم_جديد','password':'driver-password-123'})[0],401)
        with server.connect() as db:
            self.assertIsNotNone(db.execute('SELECT * FROM drivers WHERE id=?',(self.did,)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM sessions WHERE user_id=?',(self.driver,)).fetchone()[0],0)
        self.assertEqual(self.post('driver/availability',{'id':self.did,'available':True},self.at)[0],400)
        self.assertEqual(self.post('driver/restore',{'id':self.did},self.at)[0],200)
        self.assertEqual(self.post('login',{'phone':'اسم_جديد','password':'driver-password-123'})[0],200)

    def test_driver_with_open_order_cannot_be_deleted(self):
        with server.connect() as db:
            oid=db.execute("INSERT INTO orders(user_id,kind,customer,phone,area,address,payment,payment_status,status,driver_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(self.customer,'delivery','عميل','01000000001',server.AREAS[0],'عنوان','cash','confirmed','assigned',self.did,server.now())).lastrowid
        try:
            self.assertEqual(self.post('driver/delete',{'id':self.did},self.at)[0],400)
        finally:
            with server.connect() as db:db.execute("UPDATE orders SET status='cancelled' WHERE id=?",(oid,))

    def test_otp_expiry_is_enforced(self):
        with patch.object(features,'mail_ready',return_value=True),patch.object(features,'send_code'):
            _,r=self.post('auth/send-code',{'email':'expired@example.test','purpose':'register'})
        with server.connect() as db:db.execute('UPDATE account_codes SET expires=0 WHERE id=?',(r['challenge_id'],))
        self.assertEqual(self.post('register',{'email':'expired@example.test','password':'password-123456','confirm_password':'password-123456','challenge_id':r['challenge_id'],'code':'123456'})[0],400)

    def test_driver_password_confirmation(self):
        self.assertEqual(self.post('driver',{'name':'طيار جديد','phone':'01000000009','username':'طيار_جديد','password':'new-driver-password','confirm_password':'wrong','area':server.AREAS[0]},self.at)[0],400)

    def test_ratings_only_participants_of_delivered_order(self):
        with server.connect() as db:
            oid=db.execute("INSERT INTO orders(user_id,kind,customer,phone,area,address,payment,payment_status,status,driver_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(self.customer,'delivery','عميل','01000000001',server.AREAS[0],'عنوان','cash','confirmed','delivered',self.did,server.now())).lastrowid
        self.assertEqual(self.post('rating',{'order_id':oid,'stars':5},self.ot)[0],403)
        self.assertEqual(self.post('rating',{'order_id':oid,'stars':6},self.ct)[0],400)
        self.assertEqual(self.post('rating',{'order_id':oid,'stars':5},self.ct)[0],200)
        self.assertEqual(self.post('rating',{'order_id':oid,'stars':4},self.ct)[0],200)
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM ratings WHERE order_id=?',(oid,)).fetchone()[0],1)

    def test_support_and_admin_reply(self):
        code,r=self.post('support',{'message':'محتاج مساعدة','use_ai':False},self.ct);self.assertEqual(code,200);self.assertFalse(r['ai'])
        self.assertEqual(self.post('support/reply',{'id':r['id'],'answer':'تم الرد'},self.ot)[0],403)
        self.assertEqual(self.post('support/reply',{'id':r['id'],'answer':'تم الرد'},self.at)[0],200)
        with server.connect() as db:
            user=db.execute('SELECT * FROM users WHERE id=?',(self.other,)).fetchone()
            self.assertEqual(features.feature_state(db,user)['support_tickets'],[])

    def test_existing_account_can_link_email(self):
        sent={}
        with patch.object(features,'mail_ready',return_value=True),patch.object(features,'send_code',side_effect=lambda email,code:sent.update({email:code})):
            code,r=self.post('auth/send-code',{'email':'existing@example.test','purpose':'link'},self.ct);self.assertEqual(code,200)
            data={'challenge_id':r['challenge_id'],'code':sent['existing@example.test'],'current_password':'customer-password-123'}
            self.assertEqual(self.post('auth/link-email',data,self.ot)[0],400)
            self.assertEqual(self.post('auth/link-email',data,self.ct)[0],200)
            with server.connect() as db:self.assertEqual(db.execute('SELECT email FROM users WHERE id=?',(self.customer,)).fetchone()['email'],'existing@example.test')

class SMSDeliveryTests(unittest.TestCase):
    def test_sms_provider_request_and_failure(self):
        from urllib.parse import parse_qs
        env={'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'server-secret','TWILIO_MESSAGING_SERVICE_SID':'MG'+'b'*32}
        with patch.dict(os.environ,env,clear=True), patch.object(features,'urlopen') as send:
            send.return_value.__enter__.return_value=io.BytesIO(b'{"sid":"SMtest","status":"queued"}')
            features.send_sms_code('01000000124','123456')
            body=parse_qs(send.call_args.args[0].data.decode())
            self.assertEqual(body['To'],['+201000000124'])
            self.assertIn('123456',body['Body'][0])
            send.side_effect=OSError('server-secret')
            with self.assertRaises(ValueError) as failure: features.send_sms_code('01000000124','123456')
            self.assertNotIn('server-secret',str(failure.exception))


class EmailDeliveryTests(unittest.TestCase):
    def test_resend_requires_key_and_sender(self):
        with patch.dict(os.environ,{'RESEND_API_KEY':'test-key'},clear=True):
            self.assertFalse(features.mail_ready())
            with self.assertRaises(ValueError):features.send_code('client@example.test','123456')
        with patch.dict(os.environ,{'RESEND_API_KEY':'test-key','WALLAHA_EMAIL_FROM':'otp@verified.example.test'},clear=True):
            self.assertTrue(features.mail_ready())

    def test_resend_sends_only_code_to_requested_recipient(self):
        with patch.dict(os.environ,{'RESEND_API_KEY':'test-key','WALLAHA_EMAIL_FROM':'otp@verified.example.test'},clear=True),patch.object(features,'urlopen',return_value=io.StringIO('{"id":"email-test"}')) as request:
            features.send_code('client@example.test','123456')
            req=request.call_args.args[0]
            self.assertEqual(req.full_url,'https://api.resend.com/emails')
            payload=json.loads(req.data)
            self.assertEqual(payload['to'],['client@example.test'])
            self.assertIn('123456',payload['text'])
            self.assertEqual(set(payload),{'from','to','subject','text'})

    def test_resend_failure_does_not_report_success_or_leak_secret(self):
        with patch.dict(os.environ,{'RESEND_API_KEY':'test-key','WALLAHA_EMAIL_FROM':'otp@verified.example.test'},clear=True):
            for response in ('{}','not-json'):
                with patch.object(features,'urlopen',return_value=io.StringIO(response)),self.assertRaises(ValueError) as error:
                    features.send_code('client@example.test','123456')
                self.assertNotIn('test-key',str(error.exception))
                self.assertNotIn('123456',str(error.exception))
            with patch.object(features,'urlopen',side_effect=OSError('test-key')),self.assertRaises(ValueError) as error:
                features.send_code('client@example.test','123456')
            self.assertNotIn('test-key',str(error.exception))

if __name__=='__main__':unittest.main()
