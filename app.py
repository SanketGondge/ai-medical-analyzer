from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
APP_FILE = BASE_DIR / 'ai-medical-analyzer' / 'app.py'

spec = spec_from_file_location('ai_medical_analyzer_app', APP_FILE)
module = module_from_spec(spec)
spec.loader.exec_module(module)

app = module.app
