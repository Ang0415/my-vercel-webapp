from http.server import BaseHTTPRequestHandler
import json
import os
import re
from urllib.request import Request, urlopen
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

def fetch_yahoo_prices(ticker, start_date_str, end_date_str):
    """Yahoo Finance API를 사용하여 주가 데이터를 직접 가져옵니다."""
    try:
        p1 = int(datetime.strptime(start_date_str, "%Y-%m-%d").timestamp())
        p2 = int(datetime.strptime(end_date_str, "%Y-%m-%d").timestamp()) + 86400
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={p1}&period2={p2}&interval=1d"
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            
        chart = res_data.get('chart', {}).get('result', [None])[0]
        if not chart:
            return []
            
        timestamps = chart.get('timestamp', [])
        close_prices = chart.get('indicators', {}).get('quote', [{}])[0].get('close', [])
        
        history = []
        for ts, close in zip(timestamps, close_prices):
            if ts and close is not None:
                date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                history.append({"Date": date_str, "Close": float(close)})
        
        history.sort(key=lambda x: x['Date'])
        return history
    except Exception as e:
        print(f"Error fetching Yahoo prices for {ticker}: {e}")
        return []

def calculate_index_twr(prices):
    """단순 종가를 기반으로 누적 TWR(%)을 계산합니다."""
    if not prices:
        return []
        
    start_close = prices[0]["Close"]
    if start_close <= 0:
        return []
        
    twr_history = []
    for item in prices:
        close = item["Close"]
        twr = ((close / start_close) - 1.0) * 100.0
        twr_history.append({
            "Date": item["Date"],
            "TWR": round(twr, 2)
        })
    return twr_history

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
            
            # 1. 구글 시트에서 계좌별 TWR 데이터 로드
            twr_sheet = gc.open('성과_TWR_Raw')
            accounts = ['Total', 'ISA', 'IRP', '연금', '금현물']
            
            def parse_date_robust(date_str):
                cleaned = re.sub(r'[^\d]', '-', date_str.strip())
                cleaned = re.sub(r'-+', '-', cleaned).strip('-')
                parts = cleaned.split('-')
                if len(parts) >= 3:
                    try:
                        year = int(parts[0])
                        month = int(parts[1])
                        day = int(parts[2])
                        return f"{year:04d}-{month:02d}-{day:02d}"
                    except ValueError:
                        pass
                return ""

            accounts_data = {}
            first_date = None
            last_date = None
            
            for acc in accounts:
                try:
                    ws = twr_sheet.worksheet(acc)
                    records = safe_get_all_records(ws)
                    
                    history = []
                    for row in records:
                        raw_date = str(row.get('날짜', row.get('Date', ''))).strip()
                        date_val = parse_date_robust(raw_date)
                        twr_val = clean_numeric_value(row.get('TWR', row.get('twr', 0)), float)
                        
                        if date_val:
                            history.append({
                                "Date": date_val,
                                "TWR": round(twr_val, 2)
                            })
                            
                            if acc == 'Total':
                                if not first_date or date_val < first_date:
                                    first_date = date_val
                                if not last_date or date_val > last_date:
                                    last_date = date_val
                                    
                    history.sort(key=lambda x: x['Date'])
                    accounts_data[acc] = history
                except gspread.exceptions.WorksheetNotFound:
                    accounts_data[acc] = []
                except Exception as e:
                    print(f"Error loading TWR for {acc}: {e}")
                    accounts_data[acc] = []
            
            # 2. 시장 지수 TWR 계산 (KOSPI 200, S&P 500)
            indices_data = {}
            if first_date and last_date:
                kospi_prices = fetch_yahoo_prices("^KS200", first_date, last_date)
                indices_data["KOSPI200"] = calculate_index_twr(kospi_prices)
                
                sp500_prices = fetch_yahoo_prices("^GSPC", first_date, last_date)
                indices_data["SP500"] = calculate_index_twr(sp500_prices)
            else:
                indices_data["KOSPI200"] = []
                indices_data["SP500"] = []
                
            response_data = {
                "accounts": accounts_data,
                "indices": indices_data
            }
            
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
