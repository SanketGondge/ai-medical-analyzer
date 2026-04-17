import os
import sqlite3


BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
DEFAULT_SQLITE_PATH = os.path.join(BASE_DIR, 'database', 'app_database.db')


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

RAW_SQLITE_DB_PATH = os.getenv('SQLITE_DB_PATH', DEFAULT_SQLITE_PATH).strip() or DEFAULT_SQLITE_PATH


def resolve_sqlite_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)


SQLITE_DB_PATH = resolve_sqlite_path(RAW_SQLITE_DB_PATH)


def get_connection():
    if not SQLITE_DB_PATH:
        raise RuntimeError('Set SQLITE_DB_PATH in your environment or .env file.')
    if not os.path.exists(SQLITE_DB_PATH):
        raise RuntimeError(f'SQLite database not found at: {SQLITE_DB_PATH}')
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def print_rows(conn, title, query):
    rows = [dict(row) for row in conn.execute(query).fetchall()]
    print(f'=== {title} ===')
    if rows:
        for row in rows:
            print(row)
    else:
        print('No rows found!')
    print()


connection = get_connection()
print(f'SQLite database: {SQLITE_DB_PATH}')
print_rows(connection, 'USERS', 'SELECT * FROM users')
print_rows(connection, 'REPORTS', 'SELECT * FROM reports')
print_rows(connection, 'ANALYSIS_RESULTS', 'SELECT * FROM analysis_results')
connection.close()
