from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
import io, os, hashlib, re, shutil, sqlite3
from datetime import datetime
from functools import wraps
from threading import Lock
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')
TEMPLATE_DIR = os.path.join(FRONTEND_DIR, 'templates')
STATIC_DIR = os.path.join(FRONTEND_DIR, 'static')
UPLOAD_DIR = os.path.join(STATIC_DIR, 'uploads')
DATABASE_DIR = os.path.join(BASE_DIR, 'database')
DEFAULT_SQLITE_PATH = os.path.join(DATABASE_DIR, 'app_database.db')


def load_local_env():
    env_path = os.path.join(BASE_DIR, '.env')
    if not os.path.exists(env_path):
        return

    with open(env_path, 'r', encoding='utf-8') as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_local_env()

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
RAW_SQLITE_DB_PATH = os.getenv('SQLITE_DB_PATH', DEFAULT_SQLITE_PATH).strip() or DEFAULT_SQLITE_PATH


def resolve_sqlite_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)


SQLITE_DB_PATH = resolve_sqlite_path(RAW_SQLITE_DB_PATH)

app.config['SECRET_KEY'] = 'change-this-secret-key-in-production'
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'png', 'jpg', 'jpeg'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(DATABASE_DIR, exist_ok=True)

_startup_lock = Lock()
_startup_initialized = False
app.config['DB_BACKEND'] = 'sqlite'
app.config['STARTUP_ERROR'] = None

UI_TRANSLATIONS = {
    'en': {
        'app_name': 'Medical Analyzer',
        'dashboard': 'Dashboard',
        'history': 'History',
        'profile': 'Profile',
        'logout': 'Logout',
        'upload_report': 'Upload Report',
        'upload_new_report': 'Upload New Report',
        'back_to_dashboard': 'Back to Dashboard',
        'report_history': 'Report History',
        'search_reports': 'Search reports',
        'risk_level': 'Risk Level',
        'date_from': 'Date From',
        'date_to': 'Date To',
        'apply_filters': 'Apply Filters',
        'clear': 'Clear',
        'delete': 'Delete',
        'view': 'View',
        'analyze': 'Analyze',
        'not_analyzed': 'Not Analyzed',
        'recent_reports': 'Recent Reports',
        'total_reports': 'Total Reports',
        'analyzed_reports': 'Analyzed Reports',
        'upcoming_reminders': 'Upcoming Reminders',
        'follow_up_date': 'Follow-up Date',
        'reminder_note': 'Reminder Note',
        'save_reminder': 'Save Reminder',
        'language': 'Language',
        'english': 'English',
        'hindi': 'Hindi',
        'marathi': 'Marathi',
        'admin_panel': 'Admin Panel',
        'admin_login': 'Admin Login',
        'users': 'Users',
        'reports': 'Reports',
        'report_count': 'Report Count',
        'flag': 'Flag',
        'flagged': 'Flagged',
        'ocr_quality': 'OCR Quality',
        'character_count': 'Character Count',
        'quality_warning': 'The extracted text looks short, so the analysis may be less reliable.',
        'quality_good': 'The OCR output looks detailed enough for a normal analysis pass.',
        'quality_fair': 'The OCR output is usable, but double-check the extracted text for missing details.',
        'profile_settings': 'Profile Settings',
        'full_name': 'Full Name',
        'mobile_number': 'Mobile Number',
        'new_password': 'New Password',
        'confirm_password': 'Confirm Password',
        'update_profile': 'Update Profile',
    },
    'hi': {
        'app_name': 'मेडिकल एनालाइज़र',
        'dashboard': 'डैशबोर्ड',
        'history': 'इतिहास',
        'profile': 'प्रोफाइल',
        'logout': 'लॉगआउट',
        'upload_report': 'रिपोर्ट अपलोड करें',
        'upload_new_report': 'नई रिपोर्ट अपलोड करें',
        'back_to_dashboard': 'डैशबोर्ड पर वापस जाएं',
        'report_history': 'रिपोर्ट इतिहास',
        'search_reports': 'रिपोर्ट खोजें',
        'risk_level': 'रिस्क लेवल',
        'date_from': 'शुरुआती तारीख',
        'date_to': 'अंतिम तारीख',
        'apply_filters': 'फिल्टर लागू करें',
        'clear': 'साफ करें',
        'delete': 'हटाएं',
        'view': 'देखें',
        'analyze': 'विश्लेषण करें',
        'not_analyzed': 'विश्लेषण नहीं हुआ',
        'recent_reports': 'हाल की रिपोर्ट',
        'total_reports': 'कुल रिपोर्ट',
        'analyzed_reports': 'विश्लेषित रिपोर्ट',
        'upcoming_reminders': 'आगामी रिमाइंडर',
        'follow_up_date': 'फॉलो-अप तारीख',
        'reminder_note': 'रिमाइंडर नोट',
        'save_reminder': 'रिमाइंडर सेव करें',
        'language': 'भाषा',
        'english': 'अंग्रेजी',
        'hindi': 'हिंदी',
        'marathi': 'मराठी',
        'admin_panel': 'एडमिन पैनल',
        'admin_login': 'एडमिन लॉगिन',
        'users': 'यूज़र्स',
        'reports': 'रिपोर्ट',
        'report_count': 'रिपोर्ट संख्या',
        'flag': 'फ्लैग करें',
        'flagged': 'फ्लैग किया गया',
        'ocr_quality': 'OCR गुणवत्ता',
        'character_count': 'अक्षर संख्या',
        'quality_warning': 'निकाला गया टेक्स्ट छोटा लग रहा है, इसलिए विश्लेषण कम भरोसेमंद हो सकता है।',
        'quality_good': 'OCR आउटपुट सामान्य विश्लेषण के लिए पर्याप्त लग रहा है।',
        'quality_fair': 'OCR आउटपुट उपयोगी है, लेकिन निकाले गए टेक्स्ट को एक बार जांच लें।',
        'profile_settings': 'प्रोफाइल सेटिंग्स',
        'full_name': 'पूरा नाम',
        'mobile_number': 'मोबाइल नंबर',
        'new_password': 'नया पासवर्ड',
        'confirm_password': 'पासवर्ड की पुष्टि करें',
        'update_profile': 'प्रोफाइल अपडेट करें',
    },
    'mr': {
        'app_name': 'मेडिकल अॅनालायझर',
        'dashboard': 'डॅशबोर्ड',
        'history': 'इतिहास',
        'profile': 'प्रोफाइल',
        'logout': 'लॉगआउट',
        'upload_report': 'रिपोर्ट अपलोड करा',
        'upload_new_report': 'नवीन रिपोर्ट अपलोड करा',
        'back_to_dashboard': 'डॅशबोर्डकडे परत जा',
        'report_history': 'रिपोर्ट इतिहास',
        'search_reports': 'रिपोर्ट शोधा',
        'risk_level': 'जोखीम पातळी',
        'date_from': 'सुरुवातीची तारीख',
        'date_to': 'शेवटची तारीख',
        'apply_filters': 'फिल्टर लागू करा',
        'clear': 'साफ करा',
        'delete': 'हटवा',
        'view': 'पहा',
        'analyze': 'विश्लेषण करा',
        'not_analyzed': 'विश्लेषण झालेले नाही',
        'recent_reports': 'अलीकडील रिपोर्ट',
        'total_reports': 'एकूण रिपोर्ट',
        'analyzed_reports': 'विश्लेषित रिपोर्ट',
        'upcoming_reminders': 'आगामी स्मरणपत्रे',
        'follow_up_date': 'फॉलो-अप तारीख',
        'reminder_note': 'स्मरणपत्र टीप',
        'save_reminder': 'स्मरणपत्र जतन करा',
        'language': 'भाषा',
        'english': 'इंग्रजी',
        'hindi': 'हिंदी',
        'marathi': 'मराठी',
        'admin_panel': 'अॅडमिन पॅनेल',
        'admin_login': 'अॅडमिन लॉगिन',
        'users': 'वापरकर्ते',
        'reports': 'रिपोर्ट',
        'report_count': 'रिपोर्ट संख्या',
        'flag': 'फ्लॅग करा',
        'flagged': 'फ्लॅग केलेले',
        'ocr_quality': 'OCR गुणवत्ता',
        'character_count': 'अक्षर संख्या',
        'quality_warning': 'काढलेला मजकूर खूप कमी वाटतो, त्यामुळे विश्लेषण कमी विश्वासार्ह असू शकते.',
        'quality_good': 'OCR आउटपुट सामान्य विश्लेषणासाठी पुरेसे दिसते.',
        'quality_fair': 'OCR आउटपुट उपयोगी आहे, पण काढलेला मजकूर पुन्हा तपासा.',
        'profile_settings': 'प्रोफाइल सेटिंग्स',
        'full_name': 'पूर्ण नाव',
        'mobile_number': 'मोबाइल नंबर',
        'new_password': 'नवीन पासवर्ड',
        'confirm_password': 'पासवर्ड पुष्टी करा',
        'update_profile': 'प्रोफाइल अपडेट करा',
    },
}


def ensure_sqlite_ready():
    if not SQLITE_DB_PATH:
        raise RuntimeError('Set SQLITE_DB_PATH for the SQLite database location.')
    os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)


def now_utc():
    return datetime.utcnow()


def db_fetchall(conn, query, params=()):
    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def db_fetchone(conn, query, params=()):
    cursor = conn.execute(query, params)
    row = cursor.fetchone()
    return dict(row) if row else None


def db_execute(conn, query, params=()):
    return conn.execute(query, params)


def db_insert_and_get_id(conn, table_name, columns, values):
    placeholders = ', '.join('?' for _ in values)
    query = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
    cursor = conn.execute(query, values)
    return cursor.lastrowid


def get_db_connection():
    ensure_sqlite_ready()
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table_name):
    return {row['name'] for row in db_fetchall(conn, f'PRAGMA table_info({table_name})')}


def ensure_column(conn, table_name, column_name, definition):
    if column_name not in table_columns(conn, table_name):
        conn.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}')


def get_ui_language():
    language_code = (session.get('ui_language') or 'en').strip().lower()
    return language_code if language_code in UI_TRANSLATIONS else 'en'


def ui_text(key, default=None):
    language_code = get_ui_language()
    return UI_TRANSLATIONS.get(language_code, UI_TRANSLATIONS['en']).get(
        key,
        UI_TRANSLATIONS['en'].get(key, default or key)
    )


def set_user_session(user):
    session['user_id'] = user['id']
    session['user_name'] = user['name']
    session['is_admin'] = bool(user.get('is_admin'))


def ocr_quality_label(character_count):
    if character_count < 80:
        return 'Low'
    if character_count < 250:
        return 'Medium'
    return 'High'


def ocr_quality_message(character_count):
    label = ocr_quality_label(character_count)
    if label == 'Low':
        return ui_text('quality_warning')
    if label == 'Medium':
        return ui_text('quality_fair')
    return ui_text('quality_good')


def remove_report_assets(report):
    file_path = report.get('filepath')
    if file_path and os.path.isfile(file_path):
        os.remove(file_path)


def normalize_report_filepaths(conn):
    reports = db_fetchall(conn, 'SELECT id, filename, filepath FROM reports')
    for report in reports:
        normalized_path = os.path.join(app.config['UPLOAD_FOLDER'], report['filename'])
        if report['filepath'] != normalized_path:
            db_execute(
                conn,
                'UPDATE reports SET filepath = ? WHERE id = ?',
                (normalized_path, report['id'])
            )


def migrate_legacy_storage():
    legacy_upload_dir = os.path.join(BASE_DIR, 'static', 'uploads')

    if os.path.isdir(legacy_upload_dir):
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        for filename in os.listdir(legacy_upload_dir):
            source_path = os.path.join(legacy_upload_dir, filename)
            target_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(source_path) and not os.path.exists(target_path):
                shutil.copy2(source_path, target_path)


def init_db():
    conn = get_db_connection()
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            mobile TEXT NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )'''
    )
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            file_type TEXT NOT NULL,
            upload_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            analyzed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )'''
    )
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL UNIQUE,
            extracted_text TEXT,
            medical_values TEXT,
            abnormal_findings TEXT,
            risk_level TEXT,
            suggestions TEXT,
            analysis_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (report_id) REFERENCES reports (id)
        )'''
    )
    ensure_column(conn, 'users', 'is_admin', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column(conn, 'users', 'preferred_language', "TEXT NOT NULL DEFAULT 'en'")
    ensure_column(conn, 'reports', 'follow_up_date', 'TEXT')
    ensure_column(conn, 'reports', 'reminder_note', 'TEXT')
    ensure_column(conn, 'reports', 'is_flagged', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column(conn, 'reports', 'flag_reason', 'TEXT')
    ensure_column(conn, 'analysis_results', 'ocr_character_count', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column(conn, 'analysis_results', "ocr_quality_label", "TEXT NOT NULL DEFAULT 'Low'")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_reports_user_upload_date ON reports(user_id, upload_date DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_analysis_report_id ON analysis_results(report_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_reports_follow_up_date ON reports(user_id, follow_up_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_reports_flagged ON reports(is_flagged)')
    admin_email = (os.getenv('ADMIN_EMAIL') or 'admin@medical.local').strip().lower()
    admin_password = os.getenv('ADMIN_PASSWORD') or 'admin12345'
    admin_name = os.getenv('ADMIN_NAME') or 'Admin Demo'
    admin_mobile = os.getenv('ADMIN_MOBILE') or '9876543210'
    existing_admin = db_fetchone(conn, 'SELECT id FROM users WHERE email = ?', (admin_email,))
    if existing_admin:
        db_execute(
            conn,
            'UPDATE users SET name = ?, mobile = ?, password = ?, is_admin = 1 WHERE id = ?',
            (admin_name, admin_mobile, hash_password(admin_password), existing_admin['id'])
        )
    else:
        db_execute(
            conn,
            'INSERT INTO users (name, email, mobile, password, is_admin) VALUES (?, ?, ?, ?, 1)',
            (admin_name, admin_email, admin_mobile, hash_password(admin_password))
        )
    normalize_report_filepaths(conn)
    conn.commit()
    conn.close()


def try_initialize_sqlite():
    init_db()


def initialize_app_once():
    global _startup_initialized

    if _startup_initialized:
        return

    with _startup_lock:
        if _startup_initialized:
            return
        migrate_legacy_storage()
        try:
            try_initialize_sqlite()
            app.config['STARTUP_ERROR'] = None
        except Exception as exc:
            app.config['STARTUP_ERROR'] = db_error_message(exc)
        _startup_initialized = True


def db_error_message(exc):
    return (
        'SQLite database initialization failed. Set a valid SQLITE_DB_PATH '
        f'environment variable if needed. Details: {exc}'
    )


def render_auth_template(template_name, startup_error=None, **context):
    return render_template(template_name, startup_error=startup_error, **context)


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


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('Admin access required', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)

    return decorated_function


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def is_valid_email(email):
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email))


def is_valid_indian_mobile(mobile):
    return bool(re.fullmatch(r'[6-9]\d{9}', mobile))


def extract_attention_items(abnormal_findings):
    if not abnormal_findings:
        return []

    match = re.search(r'Attention:\s*(.+)', abnormal_findings, re.IGNORECASE)
    if not match:
        return []

    items = []
    for item in match.group(1).split(','):
        cleaned = item.strip().strip('.')
        if cleaned:
            items.append(cleaned)
    return items


def get_medical_term_library():
    return [
        {
            'aliases': ['glucose', 'sugar'],
            'term': 'Glucose',
            'meaning': 'Glucose means blood sugar. It shows how much sugar is present in your blood.',
            'why_it_matters': 'High glucose can be linked with diabetes risk. Low glucose can cause weakness, sweating, or dizziness.'
        },
        {
            'aliases': ['hba1c'],
            'term': 'HbA1c',
            'meaning': 'HbA1c shows your average blood sugar over the last 2 to 3 months.',
            'why_it_matters': 'Doctors use it to understand long-term sugar control.'
        },
        {
            'aliases': ['cholesterol'],
            'term': 'Cholesterol',
            'meaning': 'Cholesterol is a type of fat in the blood.',
            'why_it_matters': 'High cholesterol may increase heart disease and stroke risk over time.'
        },
        {
            'aliases': ['ldl'],
            'term': 'LDL',
            'meaning': 'LDL is often called bad cholesterol.',
            'why_it_matters': 'Higher LDL can build up in blood vessels and affect heart health.'
        },
        {
            'aliases': ['hdl'],
            'term': 'HDL',
            'meaning': 'HDL is often called good cholesterol.',
            'why_it_matters': 'It helps remove extra cholesterol from the blood.'
        },
        {
            'aliases': ['triglyceride', 'triglycerides'],
            'term': 'Triglycerides',
            'meaning': 'Triglycerides are another type of fat in the blood.',
            'why_it_matters': 'High levels may be linked with heart risk, diabetes, or lifestyle factors.'
        },
        {
            'aliases': ['hemoglobin', 'hb'],
            'term': 'Hemoglobin',
            'meaning': 'Hemoglobin is the part of red blood cells that carries oxygen.',
            'why_it_matters': 'Low hemoglobin can be related to anemia and may cause tiredness or weakness.'
        },
        {
            'aliases': ['wbc'],
            'term': 'WBC',
            'meaning': 'WBC means white blood cell count.',
            'why_it_matters': 'It can change during infections, inflammation, or some blood-related conditions.'
        },
        {
            'aliases': ['creatinine'],
            'term': 'Creatinine',
            'meaning': 'Creatinine is a blood test that gives clues about kidney function.',
            'why_it_matters': 'Higher levels can suggest the kidneys need closer review.'
        },
        {
            'aliases': ['sgpt', 'alt'],
            'term': 'SGPT / ALT',
            'meaning': 'SGPT or ALT is a liver-related blood test.',
            'why_it_matters': 'High values can happen when the liver is under stress or inflamed.'
        },
    ]


def find_medical_term_details(label):
    if not label:
        return None

    label_lower = label.lower()
    for item in get_medical_term_library():
        tokens = [item['term'].lower(), *[alias.lower() for alias in item['aliases']]]
        if any(token in label_lower or label_lower in token for token in tokens):
            return {
                'term': item['term'],
                'meaning': item['meaning'],
                'why_it_matters': item['why_it_matters']
            }
    return None


def extract_report_terms(report):
    text = ' '.join([
        report['medical_values'] or '',
        report['abnormal_findings'] or '',
        report['suggestions'] or ''
    ]).lower()

    found_terms = []
    for item in get_medical_term_library():
        if any(token in text for token in item['aliases']):
            found_terms.append({
                'term': item['term'],
                'meaning': item['meaning'],
                'why_it_matters': item['why_it_matters']
            })
    return found_terms[:5]


def annotate_medical_text(text):
    if not text:
        return Markup('')

    term_index = {}
    for item in get_medical_term_library():
        for alias in item['aliases']:
            term_index[alias.lower()] = item

    aliases = sorted(term_index.keys(), key=len, reverse=True)
    pattern = r'\b(' + '|'.join(re.escape(alias) for alias in aliases) + r')\b'

    parts = []
    last_end = 0
    for match in re.finditer(pattern, text, re.IGNORECASE):
        start, end = match.span()
        parts.append(escape(text[last_end:start]))
        term_data = term_index[match.group(0).lower()]
        parts.append(Markup(
            f'<button type="button" class="term-trigger" '
            f'data-term="{escape(term_data["term"])}" '
            f'data-meaning="{escape(term_data["meaning"])}" '
            f'data-why="{escape(term_data["why_it_matters"])}">{escape(text[start:end])}</button>'
        ))
        last_end = end

    parts.append(escape(text[last_end:]))
    html = ''.join(str(part) for part in parts).replace('\n', '<br>')
    return Markup(html)


def parse_medical_values(text):
    if not text:
        return []

    values = []
    blocks = [block.strip() for block in text.split('\n\n') if 'Status:' in block and ':' in block]

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue

        first_line = re.sub(r'^[^\w]+', '', lines[0])
        value_match = re.match(r'(?P<test>[^:]+):\s*(?P<value>[\d.]+)\s*(?P<unit>.+)', first_line)
        range_match = re.match(r'Normal:\s*(?P<range>.+)', lines[1])
        status_match = re.match(r'Status:\s*(?P<status>[A-Z]+)', lines[2])

        if not value_match or not range_match or not status_match:
            continue

        status = status_match.group('status').upper()
        test_name = value_match.group('test').strip()
        term_details = find_medical_term_details(test_name)

        values.append({
            'test': test_name,
            'value': value_match.group('value').strip(),
            'unit': value_match.group('unit').strip(),
            'range': range_match.group('range').strip(),
            'status': status,
            'meaning': term_details['meaning'] if term_details else 'This test gives a simple clue about how this part of the body is doing.',
            'why_it_matters': term_details['why_it_matters'] if term_details else 'A doctor can tell whether this result matters after comparing it with your age, symptoms, and other tests.',
            'status_class': {
                'HIGH': 'danger',
                'LOW': 'warning',
                'NORMAL': 'success'
            }.get(status, 'secondary')
        })
    return values


def build_local_language_support(report, guidance, structured_values):
    risk_level = (report['risk_level'] or 'LOW').upper()
    language_data = {
        'en': {
            'label': 'English',
            'title': 'Translated support for patients and family',
            'summary': {
                'HIGH': 'This report has values that may need quick doctor guidance. Share this simple summary with your family if they help with care.',
                'MEDIUM': 'This report has some values that should be discussed with a doctor. A translated summary can make that conversation easier.',
                'LOW': 'This report does not show a major danger sign right now, but translated guidance can still help everyone understand the result.'
            },
            'questions_title': 'Questions to ask my doctor',
            'values_title': 'Simple explanation of important values',
            'copy_label': 'Copy translation'
        },
        'hi': {
            'label': 'हिंदी',
            'title': 'रिपोर्ट का आसान हिंदी सारांश',
            'summary': {
                'HIGH': 'इस रिपोर्ट में कुछ मान सामान्य सीमा से बाहर हो सकते हैं। कृपया डॉक्टर को जल्दी दिखाएं और यह सारांश परिवार के साथ साझा करें।',
                'MEDIUM': 'इस रिपोर्ट के कुछ मान डॉक्टर से समझना जरूरी हो सकता है। घबराने की जरूरत नहीं, लेकिन फॉलो-अप करें।',
                'LOW': 'इस रिपोर्ट में अभी कोई बड़ा खतरे का संकेत नहीं दिख रहा, लेकिन आसान भाषा में समझना फिर भी उपयोगी है।'
            },
            'questions_title': 'डॉक्टर से पूछने वाले सवाल',
            'values_title': 'महत्वपूर्ण वैल्यू का आसान मतलब',
            'copy_label': 'अनुवाद कॉपी करें'
        },
        'mr': {
            'label': 'मराठी',
            'title': 'रिपोर्टचा सोपा मराठी सारांश',
            'summary': {
                'HIGH': 'या रिपोर्टमधील काही मूल्ये सामान्य मर्यादेबाहेर असू शकतात. कृपया डॉक्टरांना लवकर दाखवा आणि हा सारांश कुटुंबासोबत शेअर करा.',
                'MEDIUM': 'या रिपोर्टमधील काही मूल्यांबद्दल डॉक्टरांशी बोलणे गरजेचे असू शकते. घाबरू नका, पण फॉलो-अप करा.',
                'LOW': 'या रिपोर्टमध्ये आत्ता मोठा धोक्याचा इशारा दिसत नाही, पण सोप्या भाषेत समजून घेणे तरीही उपयोगाचे आहे.'
            },
            'questions_title': 'डॉक्टरांना विचारायचे प्रश्न',
            'values_title': 'महत्त्वाच्या चाचण्यांचा सोपा अर्थ',
            'copy_label': 'भाषांतर कॉपी करा'
        }
    }

    translated_questions = {
        'en': list(guidance['doctor_questions']),
        'hi': [
            'इस रिपोर्ट में मेरे लिए सबसे महत्वपूर्ण वैल्यू कौन-सी है?',
            'क्या मुझे यह टेस्ट दोबारा कराना चाहिए या किसी विशेषज्ञ को दिखाना चाहिए?',
            'इस रिपोर्ट के बाद मुझे किन लक्षणों पर तुरंत ध्यान देना चाहिए?'
        ],
        'mr': [
            'या रिपोर्टमधील माझ्यासाठी सर्वात महत्त्वाची वैल्यू कोणती आहे?',
            'मला ही चाचणी पुन्हा करावी लागेल का किंवा तज्ज्ञ डॉक्टरांना भेटावे लागेल का?',
            'या रिपोर्टनंतर कोणती लक्षणे दिसली तर लगेच डॉक्टरांशी संपर्क करावा?'
        ]
    }

    if guidance['attention_items']:
        top_items = ', '.join(guidance['attention_items'][:3])
        translated_questions['hi'].insert(0, f'कृपया मुझे {top_items} का मतलब आसान भाषा में समझाइए।')
        translated_questions['mr'].insert(0, f'कृपया {top_items} याचा अर्थ मला सोप्या भाषेत समजावून सांगा.')

    status_translation = {
        'en': {'LOW': 'Low', 'NORMAL': 'Normal', 'HIGH': 'High'},
        'hi': {'LOW': 'कम', 'NORMAL': 'सामान्य', 'HIGH': 'ज्यादा'},
        'mr': {'LOW': 'कमी', 'NORMAL': 'सामान्य', 'HIGH': 'जास्त'}
    }

    value_templates = {
        'en': '{test}: {value} {unit}. Status: {status}. Normal range: {range}. Meaning: {meaning}',
        'hi': '{test}: {value} {unit}. स्थिति: {status}. सामान्य सीमा: {range}. मतलब: {meaning}',
        'mr': '{test}: {value} {unit}. स्थिती: {status}. सामान्य श्रेणी: {range}. अर्थ: {meaning}'
    }

    values_by_language = {}
    for language_code, template in value_templates.items():
        values_by_language[language_code] = [
            template.format(
                test=item['test'],
                value=item['value'],
                unit=item['unit'],
                status=status_translation[language_code].get(item['status'], item['status']),
                range=item['range'],
                meaning=item['meaning']
            )
            for item in structured_values[:6]
        ]

    return {
        'title': 'Translation Help for Patients and Family',
        'options': [
            {
                'code': code,
                'label': config['label'],
                'title': config['title'],
                'summary': config['summary'].get(risk_level, config['summary']['LOW']),
                'questions_title': config['questions_title'],
                'values_title': config['values_title'],
                'copy_label': config['copy_label'],
                'questions': translated_questions[code],
                'values': values_by_language[code]
            }
            for code, config in language_data.items()
        ]
    }


def build_patient_guidance(report):
    risk_level = (report['risk_level'] or 'LOW').upper()
    attention_items = extract_attention_items(report['abnormal_findings'] or '')
    detected_items = [item for item in attention_items[:3]]
    known_terms = extract_report_terms(report)

    summary_map = {
        'HIGH': 'This report may have some important abnormal values. Please show it to a doctor soon so they can explain what needs attention first.',
        'MEDIUM': 'This report shows a few values that may need a doctor review. It does not always mean something serious, but it should be understood properly.',
        'LOW': 'This report does not show a major risk flag right now, but some medical words can still be confusing. Use the explanation section below to understand the result better.',
    }

    simple_explanation_map = {
        'HIGH': 'Simple meaning: one or more values look more concerning than usual. The safest next step is a doctor review instead of guessing from the report alone.',
        'MEDIUM': 'Simple meaning: some values may be outside the usual range. This often needs medical explanation, repeat testing, or comparison with symptoms.',
        'LOW': 'Simple meaning: no strong danger signal was found by the app, but this is still not the same as a doctor giving final confirmation.'
    }

    doctor_questions = [
        'Which value in this report is most important for me?',
        'Do I need repeat tests, treatment, or a specialist doctor?',
        'Which symptoms should I watch for after this report?'
    ]

    if detected_items:
        doctor_questions.insert(0, f'Please explain the meaning of {", ".join(detected_items)} in simple words.')

    return {
        'summary': summary_map.get(risk_level, summary_map['LOW']),
        'simple_explanation': simple_explanation_map.get(risk_level, simple_explanation_map['LOW']),
        'attention_items': attention_items,
        'known_terms': known_terms,
        'doctor_questions': doctor_questions
    }


def build_easy_language_report_text(report, guidance, structured_values, language_support, language_code='en'):
    option_map = {option['code']: option for option in language_support['options']}
    selected_language = option_map.get(language_code, option_map['en'])
    lines = [
        'AI Medical Analyzer - Easy Language Report',
        '',
        f'Original file: {report["filename"]}',
        f'Report date: {report.get("analysis_date") or report.get("upload_date") or "Not available"}',
        f'Risk level: {report.get("risk_level") or "LOW"}',
        '',
        'Simple summary',
        guidance['summary'],
        '',
        'What this means',
        guidance['simple_explanation'],
    ]

    if structured_values:
        lines.extend([
            '',
            'Important values',
        ])
        for index, item in enumerate(structured_values[:6], start=1):
            lines.extend([
                f'{index}. {item["test"]}: {item["value"]} {item["unit"]}',
                f'   Status: {item["status"]}',
                f'   Normal range: {item["range"]}',
                f'   Meaning: {item["meaning"]}',
                f'   Why it matters: {item["why_it_matters"]}',
            ])

    if guidance['doctor_questions']:
        lines.extend([
            '',
            'Questions to ask your doctor',
        ])
        for index, question in enumerate(guidance['doctor_questions'], start=1):
            lines.append(f'{index}. {question}')

    if selected_language:
        lines.extend([
            '',
            f'Translated help: {selected_language["label"]}',
            selected_language['summary'],
        ])
        if selected_language['values']:
            lines.extend([
                '',
                selected_language['values_title'],
            ])
            for index, value in enumerate(selected_language['values'], start=1):
                lines.append(f'{index}. {value}')

    lines.extend([
        '',
        'Medical note',
        'This report is educational support only and does not replace a doctor.',
    ])
    return '\n'.join(lines)


def get_pdf_font_name(language_code):
    if language_code not in {'hi', 'mr'}:
        return 'Helvetica'

    font_name = 'NirmalaUI'
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name

    font_path = r'C:\Windows\Fonts\Nirmala.ttc'
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont(font_name, font_path))
        return font_name
    return 'Helvetica'


def build_easy_language_report_pdf(report, guidance, structured_values, language_support, language_code='en'):
    option_map = {option['code']: option for option in language_support['options']}
    selected_language = option_map.get(language_code, option_map['en'])
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=42,
        bottomMargin=36
    )

    font_name = get_pdf_font_name(language_code)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#183b78'),
        spaceAfter=8
    )
    section_style = ParagraphStyle(
        'ReportSection',
        parent=styles['Heading2'],
        fontName=font_name,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#244a8f'),
        spaceBefore=8,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        'ReportBody',
        parent=styles['BodyText'],
        fontName=font_name,
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor('#243244'),
        spaceAfter=6
    )
    meta_style = ParagraphStyle(
        'ReportMeta',
        parent=body_style,
        fontSize=9.5,
        textColor=colors.HexColor('#5d6b82'),
        spaceAfter=4
    )

    story = [
        Paragraph('AI Medical Analyzer - Easy Language Report', title_style),
        Paragraph(f'Original file: {escape(report["filename"])}', meta_style),
        Paragraph(f'Report date: {escape(str(report.get("analysis_date") or report.get("upload_date") or "Not available"))}', meta_style),
        Paragraph(f'Risk level: {escape(report.get("risk_level") or "LOW")}', meta_style),
        Spacer(1, 0.12 * inch),
        Paragraph('Simple summary', section_style),
        Paragraph(escape(guidance['summary']), body_style),
        Paragraph('What this means', section_style),
        Paragraph(escape(guidance['simple_explanation']), body_style),
    ]

    if structured_values:
        story.append(Paragraph('Important values', section_style))
        for item in structured_values[:6]:
            story.extend([
                Paragraph(
                    f'<b>{escape(item["test"])}</b>: {escape(item["value"])} {escape(item["unit"])}',
                    body_style
                ),
                Paragraph(f'Status: {escape(item["status"])} | Normal range: {escape(item["range"])}', body_style),
                Paragraph(f'Meaning: {escape(item["meaning"])}', body_style),
                Paragraph(f'Why it matters: {escape(item["why_it_matters"])}', body_style),
                Spacer(1, 0.08 * inch),
            ])

    if guidance['doctor_questions']:
        story.append(Paragraph('Questions to ask your doctor', section_style))
        story.append(
            ListFlowable(
                [ListItem(Paragraph(escape(question), body_style)) for question in guidance['doctor_questions']],
                bulletType='1',
                leftIndent=16
            )
        )

    story.extend([
        Spacer(1, 0.12 * inch),
        Paragraph(f'Translated help: {escape(selected_language["label"])}', section_style),
        Paragraph(escape(selected_language['summary']), body_style),
    ])

    if selected_language['values']:
        story.append(Paragraph(escape(selected_language['values_title']), section_style))
        story.append(
            ListFlowable(
                [ListItem(Paragraph(escape(value), body_style)) for value in selected_language['values']],
                bulletType='1',
                leftIndent=16
            )
        )

    story.extend([
        Spacer(1, 0.14 * inch),
        Paragraph('Medical note', section_style),
        Paragraph('This report is educational support only and does not replace a doctor.', body_style),
    ])

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


@app.route('/')
def index():
    return render_template('index.html')


@app.before_request
def ensure_app_ready():
    try:
        initialize_app_once()
        app.config['STARTUP_ERROR'] = None
    except Exception as exc:
        app.config['STARTUP_ERROR'] = db_error_message(exc)
    if 'ui_language' not in session:
        session['ui_language'] = 'en'


@app.context_processor
def inject_ui_helpers():
    return {
        'ui_text': ui_text,
        'ui_language': get_ui_language(),
    }


@app.route('/set-language', methods=['POST'])
def set_language():
    language_code = request.form.get('language', 'en').strip().lower()
    if language_code not in UI_TRANSLATIONS:
        language_code = 'en'
    session['ui_language'] = language_code
    if session.get('user_id'):
        conn = get_db_connection()
        db_execute(
            conn,
            'UPDATE users SET preferred_language = ? WHERE id = ?',
            (language_code, session['user_id'])
        )
        conn.commit()
        conn.close()
    next_url = request.form.get('next') or request.referrer or url_for('dashboard')
    return redirect(next_url)


@app.route('/register', methods=['GET', 'POST'])
def register():
    form_data = {'name': '', 'email': '', 'mobile': ''}
    startup_error = app.config.get('STARTUP_ERROR')
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        form_data = {'name': name, 'email': email, 'mobile': mobile}

        if not all([name, email, mobile, password, confirm_password]):
            flash('All fields required', 'danger')
            return render_auth_template('register.html', startup_error, form_data=form_data)
        if not is_valid_email(email):
            flash('Enter a valid email address', 'danger')
            return render_auth_template('register.html', startup_error, form_data=form_data)
        if not is_valid_indian_mobile(mobile):
            flash('Enter a valid 10-digit Indian mobile number', 'danger')
            return render_auth_template('register.html', startup_error, form_data=form_data)
        if len(password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
            return render_auth_template('register.html', startup_error, form_data=form_data)
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_auth_template('register.html', startup_error, form_data=form_data)
        try:
            conn = get_db_connection()
            if db_fetchone(conn, 'SELECT id FROM users WHERE email = ?', (email,)):
                flash('Email already registered', 'danger')
                conn.close()
                return render_auth_template('register.html', startup_error, form_data=form_data)
            db_execute(
                conn,
                'INSERT INTO users (name, email, mobile, password, preferred_language) VALUES (?, ?, ?, ?, ?)',
                (name, email, mobile, hash_password(password), get_ui_language())
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            if not startup_error:
                flash(db_error_message(exc), 'danger')
            return render_auth_template('register.html', startup_error, form_data=form_data)
        flash('Registration successful!', 'success')
        return redirect(url_for('login'))
    return render_auth_template('register.html', startup_error, form_data=form_data)


@app.route('/login', methods=['GET', 'POST'])
def login():
    startup_error = app.config.get('STARTUP_ERROR')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        try:
            conn = get_db_connection()
            user = db_fetchone(conn, 'SELECT * FROM users WHERE email = ?', (email,))
            conn.close()
        except Exception as exc:
            if not startup_error:
                flash(db_error_message(exc), 'danger')
            return render_auth_template('login.html', startup_error)
        if user and not user.get('is_admin') and user['password'] == hash_password(password):
            set_user_session(user)
            session['ui_language'] = user.get('preferred_language') or 'en'
            flash(f'Welcome, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_auth_template('login.html', startup_error)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    startup_error = app.config.get('STARTUP_ERROR')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        try:
            conn = get_db_connection()
            user = db_fetchone(conn, 'SELECT * FROM users WHERE email = ? AND is_admin = 1', (email,))
            conn.close()
        except Exception as exc:
            if not startup_error:
                flash(db_error_message(exc), 'danger')
            return render_auth_template('login.html', startup_error, admin_mode=True)
        if user and user['password'] == hash_password(password):
            set_user_session(user)
            session['ui_language'] = user.get('preferred_language') or 'en'
            flash('Admin login successful', 'success')
            return redirect(url_for('admin_panel'))
        flash('Invalid admin credentials', 'danger')
    return render_auth_template('login.html', startup_error, admin_mode=True)


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    user = db_fetchone(conn, 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
    if user and user.get('preferred_language') and user['preferred_language'] != session.get('ui_language'):
        session['ui_language'] = user['preferred_language']
    total = db_fetchone(
        conn,
        'SELECT COUNT(*) as c FROM reports WHERE user_id = ?',
        (session['user_id'],)
    )['c']
    analyzed = db_fetchone(
        conn,
        'SELECT COUNT(*) as c FROM reports WHERE user_id = ? AND analyzed = 1',
        (session['user_id'],)
    )['c']
    recent_query = '''SELECT r.*, a.risk_level FROM reports r
        LEFT JOIN analysis_results a ON r.id = a.report_id
        WHERE r.user_id = ? ORDER BY r.upload_date DESC'''
    recent_query += ' LIMIT 5'
    recent = db_fetchall(conn, recent_query, (session['user_id'],))
    risk_breakdown_rows = db_fetchall(
        conn,
        '''SELECT COALESCE(a.risk_level, 'NOT_ANALYZED') AS risk_level, COUNT(*) AS total
        FROM reports r
        LEFT JOIN analysis_results a ON r.id = a.report_id
        WHERE r.user_id = ?
        GROUP BY COALESCE(a.risk_level, 'NOT_ANALYZED')
        ORDER BY total DESC''',
        (session['user_id'],)
    )
    reminders = db_fetchall(
        conn,
        '''SELECT id, filename, follow_up_date, reminder_note, analyzed
        FROM reports
        WHERE user_id = ? AND follow_up_date IS NOT NULL
        ORDER BY follow_up_date ASC
        LIMIT 5''',
        (session['user_id'],)
    )
    conn.close()
    return render_template(
        'dashboard.html',
        total_reports=total,
        analyzed_reports=analyzed,
        recent_reports=recent,
        risk_breakdown=risk_breakdown_rows,
        reminders=reminders,
        current_user=user
    )


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = get_db_connection()
    user = db_fetchone(conn, 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
    if not user:
        conn.close()
        session.clear()
        flash('Please login', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not mobile:
            flash('Name and mobile are required', 'danger')
        elif not is_valid_indian_mobile(mobile):
            flash('Enter a valid 10-digit Indian mobile number', 'danger')
        elif password and len(password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
        elif password and password != confirm_password:
            flash('Passwords do not match', 'danger')
        else:
            if password:
                db_execute(
                    conn,
                    'UPDATE users SET name = ?, mobile = ?, password = ? WHERE id = ?',
                    (name, mobile, hash_password(password), session['user_id'])
                )
            else:
                db_execute(
                    conn,
                    'UPDATE users SET name = ?, mobile = ? WHERE id = ?',
                    (name, mobile, session['user_id'])
                )
            conn.commit()
            session['user_name'] = name
            user = db_fetchone(conn, 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
            flash('Profile updated successfully', 'success')

    conn.close()
    return render_template('profile.html', user=user)


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
        report_id = db_insert_and_get_id(
            conn,
            'reports',
            ('user_id', 'filename', 'filepath', 'file_type'),
            (session['user_id'], filename, filepath, file_type)
        )
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
    report = db_fetchone(
        conn,
        'SELECT * FROM reports WHERE id = ? AND user_id = ?',
        (report_id, session['user_id'])
    )
    if not report:
        flash('Not found', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    if db_fetchone(conn, 'SELECT * FROM analysis_results WHERE report_id = ?', (report_id,)):
        conn.close()
        return redirect(url_for('view_analysis', report_id=report_id))
    try:
        extraction = extract_text_from_file(report['filepath'], report['file_type'])
        if isinstance(extraction, dict):
            text = extraction.get('text', '')
            character_count = extraction.get('character_count', len((extraction.get('text') or '').strip()))
        else:
            text = extraction
            character_count = len((text or '').strip())
        if not text or len(text.strip()) < 10:
            text = 'Unable to extract text.'
            character_count = len(text)
        analysis = analyze_medical_report(text)
        db_execute(
            conn,
            '''INSERT INTO analysis_results
            (report_id, extracted_text, medical_values, abnormal_findings, risk_level, suggestions, ocr_character_count, ocr_quality_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                report_id,
                text,
                analysis['medical_values'],
                analysis['abnormal_findings'],
                analysis['risk_level'],
                analysis['suggestions'],
                character_count,
                ocr_quality_label(character_count)
            )
        )
        db_execute(conn, 'UPDATE reports SET analyzed = 1 WHERE id = ?', (report_id,))
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
    report = db_fetchone(
        conn,
        '''SELECT r.*, a.* FROM reports r
        LEFT JOIN analysis_results a ON r.id = a.report_id
        WHERE r.id = ? AND r.user_id = ?''',
        (report_id, session['user_id'])
    )
    conn.close()
    if not report:
        flash('Not found', 'danger')
        return redirect(url_for('dashboard'))

    guidance = build_patient_guidance(report)
    structured_values = parse_medical_values(report['medical_values'] or '')
    language_support = build_local_language_support(report, guidance, structured_values)
    interactive_report = {
        'medical_values': annotate_medical_text(report['medical_values'] or ''),
        'abnormal_findings': annotate_medical_text(report['abnormal_findings'] or ''),
        'suggestions': annotate_medical_text(report['suggestions'] or '')
    }
    return render_template(
        'analysis.html',
        report=report,
        guidance=guidance,
        interactive_report=interactive_report,
        structured_values=structured_values,
        language_support=language_support,
        ocr_quality_message=ocr_quality_message(report.get('ocr_character_count') or 0)
    )


@app.route('/analysis/<int:report_id>/download')
@login_required
def download_analysis_report(report_id):
    language_code = request.args.get('lang', 'en').strip().lower() or 'en'
    conn = get_db_connection()
    report = db_fetchone(
        conn,
        '''SELECT r.*, a.* FROM reports r
        LEFT JOIN analysis_results a ON r.id = a.report_id
        WHERE r.id = ? AND r.user_id = ?''',
        (report_id, session['user_id'])
    )
    conn.close()
    if not report:
        flash('Not found', 'danger')
        return redirect(url_for('dashboard'))

    guidance = build_patient_guidance(report)
    structured_values = parse_medical_values(report['medical_values'] or '')
    language_support = build_local_language_support(report, guidance, structured_values)
    pdf_bytes = build_easy_language_report_pdf(
        report,
        guidance,
        structured_values,
        language_support,
        language_code=language_code
    )
    safe_name = re.sub(r'[^a-zA-Z0-9_-]+', '_', os.path.splitext(report['filename'])[0]).strip('_') or f'report_{report_id}'
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename={safe_name}_easy_report_{language_code}.pdf'
    return response


@app.route('/analysis/<int:report_id>/reminder', methods=['POST'])
@login_required
def save_reminder(report_id):
    follow_up_date = request.form.get('follow_up_date', '').strip()
    reminder_note = request.form.get('reminder_note', '').strip()
    conn = get_db_connection()
    report = db_fetchone(
        conn,
        'SELECT id FROM reports WHERE id = ? AND user_id = ?',
        (report_id, session['user_id'])
    )
    if not report:
        conn.close()
        flash('Not found', 'danger')
        return redirect(url_for('dashboard'))
    db_execute(
        conn,
        'UPDATE reports SET follow_up_date = ?, reminder_note = ? WHERE id = ?',
        (follow_up_date or None, reminder_note or None, report_id)
    )
    conn.commit()
    conn.close()
    flash('Follow-up reminder saved', 'success')
    return redirect(url_for('view_analysis', report_id=report_id))


@app.route('/history')
@login_required
def history():
    conn = get_db_connection()
    search_term = request.args.get('q', '').strip()
    selected_risk = request.args.get('risk', '').strip().upper()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    query = '''SELECT r.*, a.risk_level, a.analysis_date FROM reports r
        LEFT JOIN analysis_results a ON r.id = a.report_id
        WHERE r.user_id = ?'''
    params = [session['user_id']]
    if search_term:
        query += ' AND r.filename LIKE ?'
        params.append(f'%{search_term}%')
    if selected_risk in {'LOW', 'MEDIUM', 'HIGH'}:
        query += ' AND a.risk_level = ?'
        params.append(selected_risk)
    if date_from:
        query += ' AND date(r.upload_date) >= date(?)'
        params.append(date_from)
    if date_to:
        query += ' AND date(r.upload_date) <= date(?)'
        params.append(date_to)
    query += ' ORDER BY r.upload_date DESC'
    reports = db_fetchall(conn, query, tuple(params))
    conn.close()
    filters = {
        'q': search_term,
        'risk': selected_risk,
        'date_from': date_from,
        'date_to': date_to,
    }
    return render_template('history.html', reports=reports, filters=filters)


@app.route('/history/<int:report_id>/delete', methods=['POST'])
@login_required
def delete_report(report_id):
    conn = get_db_connection()
    report = db_fetchone(
        conn,
        'SELECT * FROM reports WHERE id = ? AND user_id = ?',
        (report_id, session['user_id'])
    )
    if not report:
        conn.close()
        flash('Not found', 'danger')
        return redirect(url_for('history'))
    db_execute(conn, 'DELETE FROM analysis_results WHERE report_id = ?', (report_id,))
    db_execute(conn, 'DELETE FROM reports WHERE id = ?', (report_id,))
    conn.commit()
    conn.close()
    remove_report_assets(report)
    flash('Report deleted successfully', 'success')
    return redirect(url_for('history'))


@app.route('/admin')
@admin_required
def admin_panel():
    conn = get_db_connection()
    users = db_fetchall(
        conn,
        '''SELECT u.id, u.name, u.email, u.mobile, u.created_at, u.is_admin, COUNT(r.id) AS report_count
        FROM users u
        LEFT JOIN reports r ON u.id = r.user_id
        GROUP BY u.id
        ORDER BY u.is_admin DESC, u.created_at DESC'''
    )
    reports = db_fetchall(
        conn,
        '''SELECT r.id, r.filename, r.upload_date, r.is_flagged, r.flag_reason, r.follow_up_date,
        u.name AS user_name, u.email AS user_email,
        a.risk_level
        FROM reports r
        JOIN users u ON u.id = r.user_id
        LEFT JOIN analysis_results a ON a.report_id = r.id
        ORDER BY r.upload_date DESC
        LIMIT 100'''
    )
    conn.close()
    return render_template('admin.html', users=users, reports=reports)


@app.route('/admin/reports/<int:report_id>/flag', methods=['POST'])
@admin_required
def admin_flag_report(report_id):
    reason = request.form.get('flag_reason', '').strip() or 'Flagged by admin'
    conn = get_db_connection()
    db_execute(
        conn,
        'UPDATE reports SET is_flagged = 1, flag_reason = ? WHERE id = ?',
        (reason, report_id)
    )
    conn.commit()
    conn.close()
    flash('Report flagged for review', 'warning')
    return redirect(url_for('admin_panel'))


@app.route('/admin/reports/<int:report_id>/delete', methods=['POST'])
@admin_required
def admin_delete_report(report_id):
    conn = get_db_connection()
    report = db_fetchone(conn, 'SELECT * FROM reports WHERE id = ?', (report_id,))
    if report:
        db_execute(conn, 'DELETE FROM analysis_results WHERE report_id = ?', (report_id,))
        db_execute(conn, 'DELETE FROM reports WHERE id = ?', (report_id,))
        conn.commit()
    conn.close()
    if report:
        remove_report_assets(report)
        flash('Report deleted from admin panel', 'success')
    else:
        flash('Report not found', 'danger')
    return redirect(url_for('admin_panel'))


if __name__ == '__main__':
    initialize_app_once()
    print('AI Medical Analyzer - FREE VERSION')
    print('http://localhost:5000')
    if app.config.get('STARTUP_ERROR'):
        print(app.config['STARTUP_ERROR'])
    app.run(debug=True, host='0.0.0.0', port=5000)
