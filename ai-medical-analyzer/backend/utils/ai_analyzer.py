"""FREE AI Analyzer - NO API COSTS"""
import re

class MedicalKnowledgeBase:
    def __init__(self):
        self.normal_ranges = {
            'glucose_fasting': {'min': 70, 'max': 100, 'unit': 'mg/dL', 'name': 'Fasting Glucose'},
            'hba1c': {'min': 4.0, 'max': 5.6, 'unit': '%', 'name': 'HbA1c'},
            'total_cholesterol': {'min': 0, 'max': 200, 'unit': 'mg/dL', 'name': 'Total Cholesterol'},
            'ldl': {'min': 0, 'max': 100, 'unit': 'mg/dL', 'name': 'LDL'},
            'hdl_male': {'min': 40, 'max': 999, 'unit': 'mg/dL', 'name': 'HDL'},
            'triglycerides': {'min': 0, 'max': 150, 'unit': 'mg/dL', 'name': 'Triglycerides'},
            'hemoglobin_male': {'min': 13.5, 'max': 17.5, 'unit': 'g/dL', 'name': 'Hemoglobin'},
            'wbc': {'min': 4.0, 'max': 11.0, 'unit': '10³/µL', 'name': 'WBC'},
            'creatinine_male': {'min': 0.7, 'max': 1.3, 'unit': 'mg/dL', 'name': 'Creatinine'},
            'sgpt': {'min': 7, 'max': 56, 'unit': 'U/L', 'name': 'SGPT'},
        }
        self.suggestions = {
            'high_glucose': ['Reduce sugar', 'Exercise daily', 'Consult doctor'],
            'high_cholesterol': ['Low-fat diet', 'Cardio exercise', 'Heart checkup'],
            'low_hemoglobin': ['Iron-rich foods', 'Vitamin C', 'Rest well'],
        }

def analyze_medical_report(text):
    """FREE analyzer"""
    pattern = r'(\w+[\s\w]*?)[\s:=]+([\d.]+)\s*([a-zA-Z/%]+)'
    matches = re.findall(pattern, text, re.IGNORECASE)
    kb = MedicalKnowledgeBase()
    results = []
    abnormal = []
    
    for test, value, unit in matches:
        try:
            val = float(value)
            test_lower = test.lower().strip()
            std_test = None
            if 'glucose' in test_lower or 'sugar' in test_lower:
                std_test = 'glucose_fasting'
            elif 'hba1c' in test_lower:
                std_test = 'hba1c'
            elif 'cholesterol' in test_lower and 'ldl' not in test_lower and 'hdl' not in test_lower:
                std_test = 'total_cholesterol'
            elif 'ldl' in test_lower:
                std_test = 'ldl'
            elif 'hdl' in test_lower:
                std_test = 'hdl_male'
            elif 'triglyceride' in test_lower:
                std_test = 'triglycerides'
            elif 'hemoglobin' in test_lower or test_lower == 'hb':
                std_test = 'hemoglobin_male'
            elif 'wbc' in test_lower:
                std_test = 'wbc'
            elif 'creatinine' in test_lower:
                std_test = 'creatinine_male'
            elif 'sgpt' in test_lower or 'alt' in test_lower:
                std_test = 'sgpt'
            
            if std_test and std_test in kb.normal_ranges:
                nr = kb.normal_ranges[std_test]
                status = 'NORMAL'
                if val < nr['min']:
                    status = 'LOW'
                    abnormal.append(nr['name'])
                elif val > nr['max']:
                    status = 'HIGH'
                    abnormal.append(nr['name'])
                results.append({
                    'test': nr['name'], 'value': val, 'unit': nr['unit'],
                    'range': f"{nr['min']}-{nr['max']}", 'status': status
                })
        except:
            continue
    
    output = "DETECTED VALUES:\n\n" if results else "No values detected.\n"
    for r in results:
        output += f"• {r['test']}: {r['value']} {r['unit']}\n"
        output += f"  Normal: {r['range']} {r['unit']}\n"
        output += f"  Status: {r['status']}\n\n"
    
    abnormal_text = ""
    if abnormal:
        abnormal_text = f"⚠️ Attention: {', '.join(set(abnormal))}\n"
        abnormal_text += "Please consult your doctor.\n"
    else:
        abnormal_text = "✓ All values normal!\n"
    
    risk = 'HIGH' if len(set(abnormal)) > 2 else 'MEDIUM' if abnormal else 'LOW'
    
    suggestions = "📋 General Advice:\n"
    suggestions += "• Balanced diet\n• Regular exercise\n• Stay hydrated\n"
    suggestions += "• Adequate sleep\n"
    if abnormal:
        suggestions += "• Consult healthcare provider\n"
    
    return {
        'medical_values': output,
        'abnormal_findings': abnormal_text,
        'risk_level': risk,
        'suggestions': suggestions
    }
