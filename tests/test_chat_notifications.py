import json
import unittest
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import chat_notifications as notices
import server
import test_trip_proximity as fixtures


class ChatNotificationTests(unittest.TestCase):
    def setUp(self):
        self.app=fixtures.TripProximityTests()
        self.app.setUp()
        self.addCleanup(self.app.doCleanups)
        self.app.action(self.app.driver,'accept_offer')
        self.record={'order_id':self.app.oid,'driver_id':self.app.did}
        self.seq=0

    def chat(self,user,mode,**data):
        return self.app.post(user,'/api/order/chat',dict(self.record,mode=mode,**data))

    def send(self,user):
        self.seq+=1
        return self.chat(user,'send',request_id='notification-test-'+str(self.seq),body='Test message')

    def test_unread_is_recipient_specific_and_survives_reload(self):
        self.send(self.app.driver)
        received=self.chat(self.app.customer,'list')
        self.assertEqual(received['unread_count'],1)
        last=received['latest_incoming']
        self.assertEqual(self.chat(self.app.customer,'list',known_last_id=last)['unread_count'],1)
        self.assertEqual(self.chat(self.app.driver,'list')['unread_count'],0)
        self.send(self.app.driver)
        self.assertEqual(self.chat(self.app.customer,'read',through=last)['unread_count'],1)
        self.assertEqual(self.chat(self.app.customer,'list')['unread_count'],1)
        self.send(self.app.customer)
        self.assertEqual(self.chat(self.app.driver,'list')['unread_count'],1)

    def test_push_routing_retry_and_read_suppression(self):
        webpush=self.app.post(self.app.customer,'/api/chat/push-key',{})
        self.assertTrue(webpush['key'])
        endpoint='https://fcm.googleapis.com/fcm/send/test-device'
        key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
        subscription={'endpoint':endpoint,'keys':{'auth':notices.b64url(bytes(16)), 'p256dh':notices.b64url(key)}}
        self.app.post(self.app.customer,'/api/chat/subscribe',{'subscription':subscription})
        self.send(self.app.customer)
        send=Mock()
        self.assertFalse(notices.deliver_next(server.connect,send))
        self.send(self.app.driver)
        failed=Mock(side_effect=TimeoutError())
        self.assertTrue(notices.deliver_next(server.connect,failed))
        with server.connect() as db:db.execute('UPDATE chat_push_queue SET retry_at=0')
        self.assertTrue(notices.deliver_next(server.connect,send))
        body=json.loads(send.call_args.kwargs['data'])
        self.assertEqual((body['order'],body['role']),(self.app.oid,'customer'))
        self.assertNotIn('body',body)
        self.send(self.app.driver)
        self.chat(self.app.customer,'read',through=self.chat(self.app.customer,'list')['latest_incoming'])
        self.assertTrue(notices.deliver_next(server.connect,send))
        self.assertEqual(send.call_count,1)
        self.app.post(self.app.customer,'/api/logout',{})
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM chat_push_devices').fetchone()[0],0)
