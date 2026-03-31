# AI Medical Report Analyzer - FREE VERSION

Upload medical reports and get AI-powered analysis **WITHOUT any API costs!**

## Features
- User Registration/Login
- Upload PDF/Image reports
- FREE AI Analysis (No API keys needed)
- Health Recommendations
- Report History
- Risk Assessment

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

### 3. Configure MongoDB
Set the MongoDB connection before starting the app.

**Windows PowerShell:**
```powershell
$env:MONGODB_URI="mongodb://localhost:27017"
$env:MONGODB_DB_NAME="ai_medical_analyzer"
```

**Linux / Mac:**
```bash
export MONGODB_URI=mongodb://localhost:27017
export MONGODB_DB_NAME=ai_medical_analyzer
```

This project is now MongoDB-only. The app no longer uses SQLite or Oracle fallback code.

### 4. Run Application
```bash
python app.py
```

### 5. Open Browser
```
http://localhost:5000
```

## Usage
1. Register/Login
2. Upload medical report (PDF/Image)
3. Click "Analyze"
4. View results instantly

## Cost
**$0.00** - Completely FREE!
- No API keys needed
- No monthly fees
- Unlimited analyses

## Tech Stack
- Flask (Backend)
- MongoDB (Database)
- Tesseract OCR (Text extraction)
- Rule-based AI (Analysis)
- Bootstrap (Frontend)

## Note
This is for educational purposes only. Always consult with qualified healthcare professionals.
