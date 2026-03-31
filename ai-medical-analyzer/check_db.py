import os

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017').strip()
MONGODB_DB_NAME = os.getenv('MONGODB_DB_NAME', 'ai_medical_analyzer').strip()


def get_connection():
    if MongoClient is None:
        raise RuntimeError('Install pymongo before using this script.')
    client = MongoClient(MONGODB_URI)
    return client, client[MONGODB_DB_NAME]


def print_rows(title, rows):
    print(f'=== {title} ===')
    if rows:
        for row in rows:
            print(row)
    else:
        print('No rows found!')
    print()


client, db = get_connection()
print(f'MongoDB database: {MONGODB_DB_NAME}')
print_rows('USERS', list(db.users.find({}, {'_id': 0})))
print_rows('REPORTS', list(db.reports.find({}, {'_id': 0})))
print_rows('ANALYSIS_RESULTS', list(db.analysis_results.find({}, {'_id': 0})))
client.close()
