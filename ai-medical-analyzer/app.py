from flask import Flask, render_template, request, redirect, url_for, session, flash
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
import os, hashlib, re, shutil
import sqlite3
from datetime import datetime
from functools import wraps
from threading import Lock

try:
    from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
except ImportError:
    MongoClient = None
    ASCENDING = 1
    DESCENDING = -1
    ReturnDocument = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
DATABASE_DIR = os.path.join(BASE_DIR, 'database')
SQLITE_DB_PATH = os.path.join(DATABASE_DIR, 'database.db')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017').strip()
MONGODB_DB_NAME = os.getenv('MONGODB_DB_NAME', 'ai_medical_analyzer').strip()

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


def ensure_mongodb_ready():
    if MongoClient is None:
        raise RuntimeError('MongoDB backend selected, but pymongo is not installed.')
    if not MONGODB_URI or not MONGODB_DB_NAME:
        raise RuntimeError('Set MONGODB_URI and MONGODB_DB_NAME for MongoDB connections.')


class MongoConnection:
    def __init__(self, client, db):
        self.client = client
        self.db = db

    def commit(self):
        return None

    def close(self):
        self.client.close()


class MongoResult:
    def __init__(self, inserted_id=None, matched_count=0, modified_count=0):
        self.lastrowid = inserted_id
        self.matched_count = matched_count
        self.modified_count = modified_count


class SQLiteResult:
    def __init__(self, cursor):
        self.lastrowid = cursor.lastrowid
        self.matched_count = cursor.rowcount
        self.modified_count = cursor.rowcount


class SQLiteConnection:
    def __init__(self, connection):
        self.connection = connection

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def now_utc():
    return datetime.utcnow()


def is_mongo_connection(conn):
    return isinstance(conn, MongoConnection)


def sqlite_row_to_dict(row):
    return dict(row) if row is not None else None


def normalize_query(query):
    return ' '.join(query.split()).strip().lower()


def get_next_sequence(conn, sequence_name):
    counter = conn.db.counters.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return counter['value']


def mongo_fetch_report_with_analysis(conn, report_id, user_id):
    report = conn.db.reports.find_one({'id': report_id, 'user_id': user_id}, {'_id': 0})
    if not report:
        return None
    analysis = conn.db.analysis_results.find_one({'report_id': report_id}, {'_id': 0})
    if analysis:
        merged = dict(report)
        merged.update(analysis)
        return merged
    return report


def mongo_fetch_report_history(conn, user_id, limit=None):
    reports = list(
        conn.db.reports.find({'user_id': user_id}, {'_id': 0}).sort('upload_date', DESCENDING)
    )
    analysis_map = {
        item['report_id']: item
        for item in conn.db.analysis_results.find(
            {'report_id': {'$in': [report['id'] for report in reports]}},
            {'_id': 0, 'report_id': 1, 'risk_level': 1, 'analysis_date': 1}
        )
    } if reports else {}

    rows = []
    for report in reports:
        row = dict(report)
        analysis = analysis_map.get(report['id'])
        if analysis:
            row['risk_level'] = analysis.get('risk_level')
            row['analysis_date'] = analysis.get('analysis_date')
        else:
            row['risk_level'] = None
            row['analysis_date'] = None
        rows.append(row)

    if limit is not None:
        return rows[:limit]
    return rows


def mongo_set_counter_if_higher(conn, sequence_name, value):
    conn.db.counters.update_one(
        {'_id': sequence_name},
        {'$max': {'value': value}},
        upsert=True
    )


def db_fetchall(conn, query, params=()):
    if not is_mongo_connection(conn):
        cursor = conn.connection.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    normalized = normalize_query(query)
    if normalized == 'select id, filename, filepath from reports':
        return list(conn.db.reports.find({}, {'_id': 0, 'id': 1, 'filename': 1, 'filepath': 1}))
    if normalized.startswith('select r.*, a.risk_level, a.analysis_date from reports r left join analysis_results a on r.id = a.report_id where r.user_id = ? order by r.upload_date desc'):
        return mongo_fetch_report_history(conn, params[0])
    if normalized.startswith('select r.*, a.risk_level from reports r left join analysis_results a on r.id = a.report_id where r.user_id = ? order by r.upload_date desc'):
        return mongo_fetch_report_history(conn, params[0], limit=5)
    raise NotImplementedError(f'Unsupported MongoDB fetchall query: {query}')


def db_fetchone(conn, query, params=()):
    if not is_mongo_connection(conn):
        cursor = conn.connection.execute(query, params)
        return sqlite_row_to_dict(cursor.fetchone())

    normalized = normalize_query(query)
    if normalized == 'select id from users where email = ?':
        return conn.db.users.find_one({'email': params[0]}, {'_id': 0, 'id': 1})
    if normalized == 'select * from users where email = ?':
        return conn.db.users.find_one({'email': params[0]}, {'_id': 0})
    if normalized == 'select count(*) as c from reports where user_id = ?':
        return {'c': conn.db.reports.count_documents({'user_id': params[0]})}
    if normalized == 'select count(*) as c from reports where user_id = ? and analyzed = 1':
        return {'c': conn.db.reports.count_documents({'user_id': params[0], 'analyzed': True})}
    if normalized == 'select * from reports where id = ? and user_id = ?':
        return conn.db.reports.find_one({'id': params[0], 'user_id': params[1]}, {'_id': 0})
    if normalized == 'select * from analysis_results where report_id = ?':
        return conn.db.analysis_results.find_one({'report_id': params[0]}, {'_id': 0})
    if normalized.startswith('select r.*, a.* from reports r left join analysis_results a on r.id = a.report_id where r.id = ? and r.user_id = ?'):
        return mongo_fetch_report_with_analysis(conn, params[0], params[1])
    raise NotImplementedError(f'Unsupported MongoDB fetchone query: {query}')


def db_execute(conn, query, params=()):
    if not is_mongo_connection(conn):
        cursor = conn.connection.execute(query, params)
        return SQLiteResult(cursor)

    normalized = normalize_query(query)
    if normalized == 'insert into users (name, email, mobile, password) values (?, ?, ?, ?)':
        user_id = get_next_sequence(conn, 'users')
        conn.db.users.insert_one({
            'id': user_id,
            'name': params[0],
            'email': params[1],
            'mobile': params[2],
            'password': params[3],
            'created_at': now_utc()
        })
        return MongoResult(inserted_id=user_id)
    if normalized.startswith('insert into analysis_results (report_id, extracted_text, medical_values, abnormal_findings, risk_level, suggestions) values (?, ?, ?, ?, ?, ?)'):
        analysis_id = get_next_sequence(conn, 'analysis_results')
        conn.db.analysis_results.insert_one({
            'id': analysis_id,
            'report_id': params[0],
            'extracted_text': params[1],
            'medical_values': params[2],
            'abnormal_findings': params[3],
            'risk_level': params[4],
            'suggestions': params[5],
            'analysis_date': now_utc()
        })
        return MongoResult(inserted_id=analysis_id)
    if normalized == 'update reports set filepath = ? where id = ?':
        result = conn.db.reports.update_one({'id': params[1]}, {'$set': {'filepath': params[0]}})
        return MongoResult(matched_count=result.matched_count, modified_count=result.modified_count)
    if normalized == 'update reports set analyzed = 1 where id = ?':
        result = conn.db.reports.update_one({'id': params[0]}, {'$set': {'analyzed': True}})
        return MongoResult(matched_count=result.matched_count, modified_count=result.modified_count)
    raise NotImplementedError(f'Unsupported MongoDB execute query: {query}')


def db_insert_and_get_id(conn, table_name, columns, values):
    if not is_mongo_connection(conn):
        placeholders = ', '.join('?' for _ in values)
        query = f'INSERT INTO {table_name} ({", ".join(columns)}) VALUES ({placeholders})'
        cursor = conn.connection.execute(query, values)
        return cursor.lastrowid

    if table_name != 'reports':
        raise NotImplementedError(f'Unsupported MongoDB insert table: {table_name}')
    report_id = get_next_sequence(conn, 'reports')
    document = {column: value for column, value in zip(columns, values)}
    document.update({
        'id': report_id,
        'upload_date': now_utc(),
        'analyzed': False
    })
    conn.db.reports.insert_one(document)
    return report_id


def get_db_connection():
    if app.config.get('DB_BACKEND') == 'mongo':
        ensure_mongodb_ready()
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        return MongoConnection(client, client[MONGODB_DB_NAME])

    connection = sqlite3.connect(SQLITE_DB_PATH)
    connection.row_factory = sqlite3.Row
    return SQLiteConnection(connection)


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
    project_root = os.path.dirname(BASE_DIR)
    legacy_upload_dir = os.path.join(project_root, 'static', 'uploads')

    if os.path.isdir(legacy_upload_dir):
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        for filename in os.listdir(legacy_upload_dir):
            source_path = os.path.join(legacy_upload_dir, filename)
            target_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(source_path) and not os.path.exists(target_path):
                shutil.copy2(source_path, target_path)


def init_db():
    conn = get_db_connection()
    if is_mongo_connection(conn):
        conn.db.users.create_index([('id', ASCENDING)], unique=True)
        conn.db.users.create_index([('email', ASCENDING)], unique=True)
        conn.db.reports.create_index([('id', ASCENDING)], unique=True)
        conn.db.reports.create_index([('user_id', ASCENDING), ('upload_date', DESCENDING)])
        conn.db.analysis_results.create_index([('id', ASCENDING)], unique=True)
        conn.db.analysis_results.create_index([('report_id', ASCENDING)], unique=True)
    else:
        conn.connection.executescript(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                mobile TEXT NOT NULL,
                password TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_type TEXT NOT NULL,
                upload_date TEXT DEFAULT CURRENT_TIMESTAMP,
                analyzed INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL UNIQUE,
                extracted_text TEXT,
                medical_values TEXT,
                abnormal_findings TEXT,
                risk_level TEXT,
                suggestions TEXT,
                analysis_date TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_reports_user_date
            ON reports(user_id, upload_date DESC);
            '''
        )
    normalize_report_filepaths(conn)
    conn.commit()
    conn.close()


def try_initialize_mongo():
    ensure_mongodb_ready()
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    conn = MongoConnection(client, client[MONGODB_DB_NAME])
    conn.db.users.create_index([('id', ASCENDING)], unique=True)
    conn.db.users.create_index([('email', ASCENDING)], unique=True)
    conn.db.reports.create_index([('id', ASCENDING)], unique=True)
    conn.db.reports.create_index([('user_id', ASCENDING), ('upload_date', DESCENDING)])
    conn.db.analysis_results.create_index([('id', ASCENDING)], unique=True)
    conn.db.analysis_results.create_index([('report_id', ASCENDING)], unique=True)
    normalize_report_filepaths(conn)
    conn.commit()
    conn.close()


def initialize_app_once():
    global _startup_initialized

    if _startup_initialized:
        return

    with _startup_lock:
        if _startup_initialized:
            return
        migrate_legacy_storage()
        startup_error = None
        try:
            try_initialize_mongo()
            app.config['DB_BACKEND'] = 'mongo'
        except Exception as exc:
            startup_error = db_error_message(exc)
            app.config['DB_BACKEND'] = 'sqlite'
            init_db()
        app.config['STARTUP_ERROR'] = startup_error if app.config['DB_BACKEND'] == 'mongo' else None
        _startup_initialized = True


def db_error_message(exc):
    return (
        'Database connection failed. On Render, set valid '
        'MONGODB_URI and MONGODB_DB_NAME environment variables.'
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
                'INSERT INTO users (name, email, mobile, password) VALUES (?, ?, ?, ?)',
                (name, email, mobile, hash_password(password))
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
        if user and user['password'] == hash_password(password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash(f'Welcome, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_auth_template('login.html', startup_error)


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
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
    conn.close()
    return render_template(
        'dashboard.html',
        total_reports=total,
        analyzed_reports=analyzed,
        recent_reports=recent
    )


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
        text = extract_text_from_file(report['filepath'], report['file_type'])
        if not text or len(text.strip()) < 10:
            text = 'Unable to extract text.'
        analysis = analyze_medical_report(text)
        db_execute(
            conn,
            '''INSERT INTO analysis_results
            (report_id, extracted_text, medical_values, abnormal_findings, risk_level, suggestions)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (
                report_id,
                text,
                analysis['medical_values'],
                analysis['abnormal_findings'],
                analysis['risk_level'],
                analysis['suggestions']
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
        language_support=language_support
    )


@app.route('/history')
@login_required
def history():
    conn = get_db_connection()
    reports = db_fetchall(
        conn,
        '''SELECT r.*, a.risk_level, a.analysis_date FROM reports r
        LEFT JOIN analysis_results a ON r.id = a.report_id
        WHERE r.user_id = ? ORDER BY r.upload_date DESC''',
        (session['user_id'],)
    )
    conn.close()
    return render_template('history.html', reports=reports)


if __name__ == '__main__':
    migrate_legacy_storage()
    init_db()
    print('AI Medical Analyzer - FREE VERSION')
    print('http://localhost:5000')
    app.run(debug=True, host='0.0.0.0', port=5000)
