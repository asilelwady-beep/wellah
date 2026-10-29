import re
import subprocess
import tempfile
import unittest
from pathlib import Path
import server

class AdminStartupTests(unittest.TestCase):
    def test_dashboard_boots_without_external_scripts(self):
        html = server.app_document('/admin').decode()
        self.assertNotRegex(html, r'<script src="/(maps|features)\.js')
        self.assertIn('<script src="/maps.js', server.app_document('/customer').decode())
        self.assertLess(html.index('id="admin-early-state"'), html.index('<link'))
        self.assertIn('href="/leaflet.css?v=1.9.4" media="print"', html)
        with tempfile.TemporaryDirectory() as folder:
            for i, match in enumerate(re.finditer(r'<script(?:\s[^>]*)?>(.*?)</script>', html, re.S)):
                if not match[1].strip():
                    continue
                script = Path(folder)/f'inline-{i}.js'
                script.write_text(match[1])
                subprocess.run(['node', '--check', str(script)], check=True, capture_output=True)
