# AI Medical Report Analyzer - FREE VERSION

Upload medical reports and get AI-powered analysis without any API costs.

## Features
- User registration and login
- Upload PDF and image reports
- Free AI-style analysis with no API keys
- Health recommendations
- Report history
- Risk assessment

## Installation

### 1. Install Tesseract OCR

**Windows:**
Download from: https://github.com/UB-Mannheim/tesseract/wiki

**Linux:**
```bash
sudo apt-get update
sudo apt-get install tesseract-ocr
```

**Mac:**
```bash
brew install tesseract
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure SQLite
The app now uses SQLite by default. You can keep the default database file or set a custom path.

**Windows PowerShell:**
```powershell
$env:SQLITE_DB_PATH="database/app_database.db"
```

**Linux / Mac:**
```bash
export SQLITE_DB_PATH=database/app_database.db
```

If `SQLITE_DB_PATH` is not set, the app automatically uses `database/app_database.db`.

### 4. Run Application
```bash
python app.py
```

### 5. Open Browser
```text
http://localhost:5000
```

## Usage
1. Register or log in
2. Upload a medical report
3. Click Analyze
4. View the generated results

## Cost
**$0.00** - Completely free
- No API keys needed
- No monthly fees
- Unlimited analyses

## Tech Stack
- Flask
- SQLite
- Tesseract OCR
- Rule-based AI analysis
- Bootstrap

## Note
This project is for educational purposes only. Always consult a qualified healthcare professional.
