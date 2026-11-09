import unittest
import test_trip_proximity as fixtures

class BannerOfferTests(unittest.TestCase):
    def setUp(self):
        self.app = fixtures.TripProximityTests()
        self.app.setUp()
        self.addCleanup(self.app.doCleanups)
        self.offers = [
            {'title': title, 'detail': 'تفاصيل العرض', 'visible': title != 'مخفي'}
            for title in ('عرض أول', 'مخفي', 'عرض ثان', 'عرض ثالث', 'عرض رابع')
        ]

    def state(self, uid, path):
        code, value = self.app.request(uid, path)
        self.assertEqual(code, 200, value)
        return value

    def publish(self, uid, revision, items):
        return self.app.request(uid, '/api/admin/banner-offers', {'revision': revision, 'offers': items})

    def test_publish_order_visibility_retry_and_clear(self):
        self.assertEqual(self.state(self.app.customer, '/api/catalog')['banner_offers'], [])
        code, saved = self.publish(self.app.owner, 0, self.offers)
        self.assertEqual(code, 200, saved)
        self.assertEqual(self.publish(self.app.owner, 0, self.offers), [code, saved])
        expected = [o['title'] for o in self.offers if o['visible']]
        for path in ['/api/catalog', '/api/state']:
            actual = self.state(self.app.customer, path)['banner_offers']
            self.assertEqual([o['title'] for o in actual], expected)
        admin = self.state(self.app.owner, '/api/state?admin_catalog=summary')['banner_editor']
        self.assertEqual(admin, saved)
        self.assertEqual(admin['offers'], self.offers)
        self.assertEqual(self.publish(self.app.owner, saved['revision'], self.offers[::-1])[0], 200)
        self.assertEqual(self.publish(self.app.owner, saved['revision'], [])[0], 409)
        code, newest = self.publish(self.app.owner, saved['revision']+1, [])
        self.assertEqual(code, 200, newest)
        self.assertEqual(self.state(self.app.customer, '/api/catalog')['banner_offers'], [])

    def test_validation_and_access(self):
        for uid in [self.app.customer, self.app.driver]:
            self.assertEqual(self.publish(uid, 0, self.offers)[0], 403)
        bad = {**self.offers[0], 'title': '  '}
        self.assertEqual(self.publish(self.app.owner, 0, [bad])[0], 400)
        self.assertEqual(self.state(self.app.customer, '/api/catalog')['banner_offers'], [])
