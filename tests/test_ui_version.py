import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
import server

class UiVersionTests(unittest.TestCase):
    def test_customer_boot_has_current_revision_and_no_blocking_dependencies(self):
        html = server.app_document('/customer').decode()
        self.assertIn('name="wallaha-ui-revision" content="'+server.APP_UI_REVISION+'"',html)
        self.assertNotIn('__WALLAHA_UI_REVISION__',html)
        self.assertNotIn('<script src="/maps.js',html)
        self.assertNotIn('<script src="/features.js',html)
        self.assertIn('function checkAppUpdate(',html)

    def test_version_is_public_small_and_not_cached(self):
        http = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread = threading.Thread(target=http.serve_forever,daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}/api/ui-version') as response:
                self.assertEqual(response.headers['Cache-Control'],'no-store')
                body=response.read()
                self.assertLess(len(body),100)
                self.assertEqual(json.loads(body),{'revision':server.APP_UI_REVISION})
        finally:
            http.shutdown();http.server_close();thread.join()

if __name__=='__main__':unittest.main()
