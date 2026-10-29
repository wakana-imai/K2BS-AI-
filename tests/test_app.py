import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app


class ClinicTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.app.config['TESTING'] = True
        (self.root / 'password.hash').write_text(generate_password_hash('test-only-password'))
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, data=None):
        self.client.get('/login')
        with self.client.session_transaction() as session:
            token = session['csrf']
        return self.client.post(path, data={'csrf': token, **(data or {})}, follow_redirects=True)

    def login(self):
        self.post('/login', {'password': 'test-only-password'})

    def patient(self, chart='K-001', name='テスト 花子'):
        return self.post('/patients', {'chart': chart, 'name': name, 'birth': '2016-04-10'})

    def test_login_and_access_control(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.client.get('/photos/1').status_code, 302)
        self.assertEqual(self.client.post('/patients', data={}).status_code, 400)
        response = self.post('/login', {'password': 'wrong'})
        self.assertIn('パスワードが違います', response.text)
        self.login()
        self.assertIn('患者一覧', self.client.get('/').text)
        self.post('/logout')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_patient_records_search_and_persistence(self):
        self.login()
        self.assertIn('テスト 花子', self.patient().text)
        self.assertIn('登録済み', self.patient().text)
        for kind, content, amount in [('来院', '定期チェック', '0'), ('治療', '拡大装置の調整', '0'), ('入金', '現金', '12000')]:
            result = self.post('/patients/1/records', {'kind': kind, 'day': '2026-10-29', 'content': content, 'amount': amount})
            self.assertIn(content, result.text)
        self.assertIn('¥12,000', result.text)
        self.assertIn('テスト 花子', self.client.get('/?q=K-001').text)
        self.assertIn('該当する患者がいません', self.client.get('/?q=unknown').text)
        self.assertNotIn('/patients/1', self.client.get('/?q=unknown').text)
        self.assertIn('テスト 花子', self.client.get('/?q=花子').text)
        second = create_app(self.root).test_client()
        with second.session_transaction() as session:
            session['logged_in'] = True
        self.assertIn('拡大装置の調整', second.get('/patients/1').text)
        self.assertIn('¥12,000', second.get('/patients/1').text)

    def test_validation_and_escaping(self):
        self.login()
        self.patient(name='<script>alert(1)</script>')
        page = self.client.get('/patients/1').text
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        for amount in ['-1', '1.5', 'oops']:
            self.post('/patients/1/records', {'kind': '入金', 'day': '2026-10-29', 'content': 'invalid', 'amount': amount})
        self.post('/patients/1/records', {'kind': '治療', 'day': 'invalid', 'content': 'invalid'})
        with sqlite3.connect(self.root / 'clinic.sqlite3') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM records').fetchone()[0], 0)
        self.assertEqual(self.client.get('/patients/999').status_code, 404)

    def test_photo_upload_and_authentication(self):
        self.login()
        self.patient()
        image = io.BytesIO()
        Image.new('RGB', (80, 60), 'white').save(image, 'PNG')
        image.seek(0)
        page = self.post('/patients/1/photos', {'day': '2026-10-29', 'caption': '初診時・正面', 'photo': (image, 'sample.png')})
        self.assertIn('初診時・正面', page.text)
        response = self.client.get('/photos/1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        with Image.open(io.BytesIO(response.data)) as saved:
            self.assertEqual(saved.size, (80, 60))
            self.assertFalse(saved.getexif())
        response.close()
        self.post('/patients/1/photos', {'day': '2026-10-29', 'photo': (io.BytesIO(b'not an image'), 'fake.jpg')})
        with sqlite3.connect(self.root / 'clinic.sqlite3') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM photos').fetchone()[0], 1)
        self.post('/logout')
        self.assertEqual(self.client.get('/photos/1').status_code, 302)


if __name__ == '__main__':
    unittest.main()
