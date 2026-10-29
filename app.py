import hmac
import io
import os
import secrets
import sqlite3
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash


def create_app(data_dir=None):
    app = Flask(__name__)
    root = Path(data_dir or os.environ.get('K2BS_DATA_DIR', Path(__file__).parent / 'data'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / 'photos').mkdir(exist_ok=True, mode=0o700)
    keyfile = root / 'session.key'
    if not keyfile.exists():
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_hex(32))
    app.config.update(SECRET_KEY=keyfile.read_text(), MAX_CONTENT_LENGTH=12 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('K2BS_HTTPS') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

    def db():
        conn = sqlite3.connect(root / 'clinic.sqlite3')
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS patients(id INTEGER PRIMARY KEY, chart TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, birth TEXT NOT NULL DEFAULT '', memo TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
            day TEXT NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0, content TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
            day TEXT NOT NULL, caption TEXT NOT NULL, filename TEXT UNIQUE NOT NULL);
        ''')

    @app.before_request
    def protect():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        if request.method == 'POST' and not hmac.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, '画面を開き直してから再度お試しください。')
        if request.endpoint not in ('login', 'static') and not session.get('logged_in'):
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'"
        return response

    @app.context_processor
    def context():
        return {'today': date.today().isoformat()}

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        password_file = root / 'password.hash'
        if request.method == 'POST':
            if not password_file.exists():
                flash('先に端末で python app.py set-password を実行してください。')
            elif check_password_hash(password_file.read_text(), request.form.get('password', '')):
                session.clear()
                session.update(logged_in=True, csrf=secrets.token_hex(32))
                session.permanent = True
                return redirect(url_for('index'))
            else:
                flash('パスワードが違います。')
        return render_template('login.html', configured=password_file.exists())

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def index():
        q = request.args.get('q', '').strip()
        with db() as conn:
            patients = conn.execute('SELECT * FROM patients WHERE instr(chart,?)>0 OR instr(name,?)>0 ORDER BY id DESC', (q, q)).fetchall()
            count = conn.execute('SELECT count(*) FROM patients').fetchone()[0]
        return render_template('index.html', patients=patients, q=q, count=count)

    @app.post('/patients')
    def add_patient():
        chart, name = request.form.get('chart', '').strip(), request.form.get('name', '').strip()
        birth = request.form.get('birth', '')
        if not chart or not name or len(chart) > 50 or len(name) > 100:
            flash('カルテ番号と氏名を入力してください（番号50文字・氏名100文字まで）。')
            return redirect(url_for('index'))
        if birth:
            try:
                date.fromisoformat(birth)
            except ValueError:
                abort(400, '生年月日が正しくありません。')
        try:
            with db() as conn:
                cursor = conn.execute('INSERT INTO patients(chart,name,birth,memo) VALUES(?,?,?,?)',
                                      (chart, name, birth, request.form.get('memo', '')[:4000]))
                patient_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            flash('そのカルテ番号は登録済みです。')
            return redirect(url_for('index'))
        return redirect(url_for('patient', patient_id=patient_id))

    def get_patient(patient_id):
        with db() as conn:
            result = conn.execute('SELECT * FROM patients WHERE id=?', (patient_id,)).fetchone()
        if result is None:
            abort(404)
        return result

    @app.get('/patients/<int:patient_id>')
    def patient(patient_id):
        person = get_patient(patient_id)
        with db() as conn:
            records = conn.execute('SELECT * FROM records WHERE patient_id=? ORDER BY day DESC,id DESC', (patient_id,)).fetchall()
            photos = conn.execute('SELECT * FROM photos WHERE patient_id=? ORDER BY day DESC,id DESC', (patient_id,)).fetchall()
        return render_template('patient.html', patient=person, records=records, photos=photos,
                               total=sum(r['amount'] for r in records if r['kind'] == '入金'))

    @app.post('/patients/<int:patient_id>/records')
    def add_record(patient_id):
        get_patient(patient_id)
        kind = request.form.get('kind')
        content = request.form.get('content', '').strip()
        try:
            day = date.fromisoformat(request.form.get('day', '')).isoformat()
            amount = int(request.form.get('amount', '0') or '0') if kind == '入金' else 0
            if kind not in ('来院', '治療', '入金') or not content or len(content) > 10000 or not 0 <= amount <= 100000000:
                raise ValueError
        except ValueError:
            flash('日付・内容・金額を確認してください。金額は0円以上の整数で入力します。')
            return redirect(url_for('patient', patient_id=patient_id))
        with db() as conn:
            conn.execute('INSERT INTO records(patient_id,day,kind,amount,content) VALUES(?,?,?,?,?)',
                         (patient_id, day, kind, amount, content))
        flash('記録を保存しました。')
        return redirect(url_for('patient', patient_id=patient_id))

    @app.post('/patients/<int:patient_id>/photos')
    def add_photo(patient_id):
        get_patient(patient_id)
        uploaded = request.files.get('photo')
        filename = secrets.token_hex(24) + '.jpg'
        try:
            day = date.fromisoformat(request.form.get('day', '')).isoformat()
            if not uploaded:
                raise ValueError
            with Image.open(uploaded.stream) as original:
                if original.format not in ('JPEG', 'PNG', 'WEBP') or original.width * original.height > 25000000:
                    raise ValueError
                picture = ImageOps.exif_transpose(original).convert('RGB')
                picture.thumbnail((2400, 2400))
                picture.save(root / 'photos' / filename, 'JPEG', quality=90)
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            flash('JPEG・PNG・WebP の画像を選択してください（12MB・2500万画素以下）。')
            return redirect(url_for('patient', patient_id=patient_id))
        try:
            with db() as conn:
                conn.execute('INSERT INTO photos(patient_id,day,caption,filename) VALUES(?,?,?,?)',
                             (patient_id, day, request.form.get('caption', '')[:500], filename))
        except Exception:
            (root / 'photos' / filename).unlink(missing_ok=True)
            raise
        flash('写真を保存しました。')
        return redirect(url_for('patient', patient_id=patient_id))

    @app.get('/photos/<int:photo_id>')
    def photo(photo_id):
        with db() as conn:
            item = conn.execute('SELECT filename FROM photos WHERE id=?', (photo_id,)).fetchone()
        if item is None:
            abort(404)
        return send_file(root / 'photos' / item['filename'], mimetype='image/jpeg')

    @app.errorhandler(413)
    def too_large(error):
        return render_template('error.html', message='ファイルは12MB以下でアップロードしてください。'), 413

    app.config['DATA_ROOT'] = root
    return app


if __name__ == '__main__':
    import getpass
    import sys
    application = create_app()
    if sys.argv[1:] == ['set-password']:
        password = getpass.getpass('共有パスワード（12文字以上）: ')
        if len(password) < 12 or password != getpass.getpass('確認: '):
            sys.exit('12文字以上で、同じパスワードを2回入力してください。')
        path = application.config['DATA_ROOT'] / 'password.hash'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(generate_password_hash(password))
        print('設定しました。ログインして利用できます。')
    else:
        application.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')), debug=False)
