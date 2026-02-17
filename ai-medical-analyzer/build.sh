#!/bin/bash
apt-get install -y tesseract-ocr
pip install -r requirements.txt
```

**`requirements.txt`** — add `gunicorn` at the bottom:
```
Flask==3.0.0
Werkzeug==3.0.1
Pillow==10.1.0
pytesseract==0.3.10
PyPDF2==3.0.1
gunicorn