from http.server import BaseHTTPRequestHandler
import json
import os
import re
from datetime import datetime
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
            
            # 1. 성과_자산추이_Raw 에서 각 계좌 시트의 데이터 읽기
            asset_sheet = gc.open('성과_자산추이_Raw')
            accounts = ['ISA', 'IRP', '연금', '금현물']
            
            # 현재 연월초 기준일 구하기 (미래 데이터 방지용)
            now = datetime.now()
            current_month_start = datetime(now.year, now.month, 1)
            
            raw_data = {}
            all_months = set()
            
            for acc in accounts:
                try:
                    ws = asset_sheet.worksheet(acc)
                    records = safe_get_all_records(ws)
                    
                    def parse_date_robust(date_str):
                        cleaned = re.sub(r'[^\d]', '-', date_str.strip())
                        cleaned = re.sub(r'-+', '-', cleaned).strip('-')
                        parts = cleaned.split('-')
                        if len(parts) >= 3:
                            try:
                                year = int(parts[0])
                                month = int(parts[1])
                                day = int(parts[2])
                                return datetime(year, month, day)
                            except ValueError:
                                pass
                        return None

                    # 월별 마지막 데이터 매핑 (Key: YYYY-MM)
                    monthly_last_record = {}
                    for row in records:
                        date_str = str(row.get('Date', row.get('날짜', ''))).strip()
                        if not date_str:
                            continue
                            
                        # 날짜 파싱
                        dt = parse_date_robust(date_str)
                        if not dt:
                            continue
                                
                        # 당월 1일 기준 까지만 허용 (미래 데이터 제거)
                        if dt > current_month_start:
                            continue
                            
                        val = clean_numeric_value(row.get('Value', row.get('평가금액', 0)), float)
                        month_key = dt.strftime("%Y-%m")
                        
                        # 같은 월 내에서는 더 최신 날짜의 레코드로 덮어씀
                        if month_key not in monthly_last_record or date_str > monthly_last_record[month_key]['date']:
                            monthly_last_record[month_key] = {
                                'date': date_str,
                                'val': val
                            }
                            
                    raw_data[acc] = monthly_last_record
                    all_months.update(monthly_last_record.keys())
                except gspread.exceptions.WorksheetNotFound:
                    raw_data[acc] = {}
                except Exception as e:
                    print(f"Error reading monthly asset for {acc}: {e}")
                    raw_data[acc] = {}
            
            # 2. 정렬된 월 리스트 생성 (예: "2026-01-01" 형태로 매핑)
            sorted_months = sorted(list(all_months))
            dates_list = [f"{m}-01" for m in sorted_months]
            
            accounts_series = {acc: [] for acc in accounts}
            for m in sorted_months:
                for acc in accounts:
                    val = raw_data[acc].get(m, {}).get('val', 0.0)
                    accounts_series[acc].append(int(val))
                    
            response_data = {
                "dates": dates_list,
                "accounts": accounts_series
            }
            
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
