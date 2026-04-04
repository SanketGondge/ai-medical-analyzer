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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
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
    conn.execute('CREATE INDEX IF NOT EXISTS idx_reports_user_upload_date ON reports(user_id, upload_date DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_analysis_report_id ON analysis_results(report_id)')
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
    initialize_app_once()
    print('AI Medical Analyzer - FREE VERSION')
    print('http://localhost:5000')
    if app.config.get('STARTUP_ERROR'):
        print(app.config['STARTUP_ERROR'])
    app.run(debug=True, host='0.0.0.0', port=5000)
