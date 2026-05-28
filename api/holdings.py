from http.server import BaseHTTPRequestHandler
import json
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def clean_numeric_value(value, type_func=float):
    if isinstance(value, (int, float)):
        try: return type_func(value)
        except (ValueError, TypeError): return type_func(0)
    if not value: return type_func(0)
    try:
        cleaned_str = re.sub(r'[^\d.-]+', '', str(value))
        if not cleaned_str or cleaned_str in ['-', '.']: return type_func(0)
        num_val = float(cleaned_str)
        return type_func(num_val)
    except (ValueError, TypeError):
        return type_func(0)

def safe_get_all_records(ws):
    """중복 헤더나 빈 열이 있는 구글 시트를 안전하게 딕셔너리 레코드로 파싱합니다."""
    values = ws.get_all_values()
    if not values:
        return []
    header = values[0]
    records = []
    for row in values[1:]:
        record = {}
        for i, val in enumerate(row):
            if i < len(header):
                key = str(header[i]).strip()
                if key == '':
                    key = f"col_{i}"
                if key in record:
                    key = f"{key}_{i}"
                record[key] = val
            else:
                record[f"col_{i}"] = val
        records.append(record)
    return records

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        try:
            creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
            if not creds_json_str:
                raise Exception("GOOGLE_CREDENTIALS not found in environment.")
                
            creds_dict = json.loads(creds_json_str)
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            gc = gspread.authorize(creds)
            
            # 1. 일별비중_RAW 파일에서 일별비중_Raw 시트 열기
            weights_sheet = gc.open('일별비중_RAW')
            worksheets_list = weights_sheet.worksheets()
            weights_ws = None
            for ws in worksheets_list:
                if ws.title.strip().lower() == '일별비중_raw':
                    weights_ws = ws
                    break
            
            if not weights_ws:
                raise Exception("Worksheet '일별비중_Raw' not found.")
                
            records = safe_get_all_records(weights_ws)
            
            # 가장 최신 날짜 구하기
            latest_date = None
            for row in records:
                row_date = str(row.get('날짜', '')).strip()
                if row_date:
                    if not latest_date or row_date > latest_date:
                        latest_date = row_date
            
            holdings = []
            seen = set()
            if latest_date:
                for row in records:
                    if str(row.get('날짜', '')).strip() == latest_date:
                        amount = clean_numeric_value(row.get('평가금액', 0), float)
                        if amount > 0:
                            code = str(row.get('종목코드', '')).strip()
                            name = str(row.get('종목명', '')).strip().replace(' ', '')
                            
                            if not code and (name == '금현물' or name == '금'):
                                code = 'GOLD'
                                
                            if name and code and name not in seen:
                                seen.add(name)
                                holdings.append({
                                    "code": code,
                                    "name": name
                                })
            
            # 가나다순 정렬
            holdings.sort(key=lambda x: x['name'])
            
            self.wfile.write(json.dumps(holdings).encode('utf-8'))
            
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
