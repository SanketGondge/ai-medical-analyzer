from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import os, sqlite3, hashlib
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-secret-key-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'png', 'jpg', 'jpeg'}
DATABASE = 'database.db'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL, mobile TEXT, password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        filename TEXT NOT NULL, filepath TEXT NOT NULL, file_type TEXT NOT NULL,
        upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, analyzed BOOLEAN DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS analysis_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL,
        extracted_text TEXT, medical_values TEXT, abnormal_findings TEXT,
        risk_level TEXT, suggestions TEXT,
        analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (report_id) REFERENCES reports (id))''')
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        if not all([name, email, password]):
            flash('All fields required', 'danger')
            return redirect(url_for('register'))
        conn = get_db_connection()
        if conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            flash('Email already registered', 'danger')
            conn.close()
            return redirect(url_for('register'))
        conn.execute('INSERT INTO users (name, email, mobile, password) VALUES (?, ?, ?, ?)',
                    (name, email, mobile, hash_password(password)))
        conn.commit()
        conn.close()
        flash('Registration successful!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        if user and user['password'] == hash_password(password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash(f'Welcome, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    total = conn.execute('SELECT COUNT(*) as c FROM reports WHERE user_id = ?', 
                        (session['user_id'],)).fetchone()['c']
    analyzed = conn.execute('SELECT COUNT(*) as c FROM reports WHERE user_id = ? AND analyzed = 1',
                           (session['user_id'],)).fetchone()['c']
    recent = conn.execute('''SELECT r.*, a.risk_level FROM reports r 
                            LEFT JOIN analysis_results a ON r.id = a.report_id 
                            WHERE r.user_id = ? ORDER BY r.upload_date DESC LIMIT 5''',
                         (session['user_id'],)).fetchall()
    conn.close()
    return render_template('dashboard.html', total_reports=total, 
                         analyzed_reports=analyzed, recent_reports=recent)

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        if 'report_file' not in request.files:
            flash('No file', 'danger')
            return redirect(url_for('upload'))
        file = request.files['report_file']
        if file.filename == '' or not allowed_file(file.filename):
            flash('Invalid file', 'danger')
            return redirect(url_for('upload'))
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        file_type = filename.rsplit('.', 1)[1].lower()
        conn = get_db_connection()
        cursor = conn.execute('INSERT INTO reports (user_id, filename, filepath, file_type) VALUES (?, ?, ?, ?)',
                            (session['user_id'], filename, filepath, file_type))
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()
        flash('Uploaded!', 'success')
        return redirect(url_for('analyze_report', report_id=report_id))
    return render_template('upload.html')

@app.route('/analyze/<int:report_id>')
@login_required
def analyze_report(report_id):
    from utils.ocr_processor import extract_text_from_file
    from utils.ai_analyzer import analyze_medical_report
    conn = get_db_connection()
    report = conn.execute('SELECT * FROM reports WHERE id = ? AND user_id = ?',
                         (report_id, session['user_id'])).fetchone()
    if not report:
        flash('Not found', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    if conn.execute('SELECT * FROM analysis_results WHERE report_id = ?', (report_id,)).fetchone():
        conn.close()
        return redirect(url_for('view_analysis', report_id=report_id))
    try:
        text = extract_text_from_file(report['filepath'], report['file_type'])
        if not text or len(text.strip()) < 10:
            text = "Unable to extract text."
        analysis = analyze_medical_report(text)
        conn.execute('''INSERT INTO analysis_results 
                       (report_id, extracted_text, medical_values, abnormal_findings, risk_level, suggestions)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (report_id, text, analysis['medical_values'], 
                     analysis['abnormal_findings'], analysis['risk_level'], analysis['suggestions']))
        conn.execute('UPDATE reports SET analyzed = 1 WHERE id = ?', (report_id,))
        conn.commit()
        flash('Analyzed!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    conn.close()
    return redirect(url_for('view_analysis', report_id=report_id))

@app.route('/analysis/<int:report_id>')
@login_required
def view_analysis(report_id):
    conn = get_db_connection()
    report = conn.execute('''SELECT r.*, a.* FROM reports r 
                            LEFT JOIN analysis_results a ON r.id = a.report_id 
                            WHERE r.id = ? AND r.user_id = ?''',
                         (report_id, session['user_id'])).fetchone()
    conn.close()
    if not report:
        flash('Not found', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('analysis.html', report=report)

@app.route('/history')
@login_required
def history():
    conn = get_db_connection()
    reports = conn.execute('''SELECT r.*, a.risk_level, a.analysis_date FROM reports r 
                             LEFT JOIN analysis_results a ON r.id = a.report_id 
                             WHERE r.user_id = ? ORDER BY r.upload_date DESC''',
                          (session['user_id'],)).fetchall()
    conn.close()
    return render_template('history.html', reports=reports)

if __name__ == '__main__':
    init_db()
    print("🏥 AI Medical Analyzer - FREE VERSION")
    print("http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
