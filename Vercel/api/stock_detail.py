from http.server import BaseHTTPRequestHandler
import json
import os
import re
from urllib.parse import urlparse, parse_qs
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

def fetch_yahoo_prices(ticker, start_date_str):
    """Yahoo Finance API를 통해 주가 이력을 가져옵니다."""
    try:
        p1 = int(datetime.strptime(start_date_str, "%Y-%m-%d").timestamp())
        p2 = int(datetime.now().timestamp()) + 86400
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={p1}&period2={p2}&interval=1d"
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urlopen(req, timeout=8) as response:
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
            parsed_path = urlparse(self.path)
            query = parse_qs(parsed_path.query)
            
            code = query.get('code', [None])[0]
            name = query.get('name', [None])[0]
            
            if not code:
                raise Exception("Missing query parameter 'code'")
                
            creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
            if not creds_json_str:
                raise Exception("GOOGLE_CREDENTIALS not found in environment.")
                
            creds_dict = json.loads(creds_json_str)
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            gc = gspread.authorize(creds)
            
            # --- 1. '🗓️매매일지' 에서 이동평균법 평단가 및 최초매수일 계산 ---
            main_sheet = gc.open('KYI_자산배분')
            trades_ws = main_sheet.worksheet('🗓️매매일지')
            trades_records = safe_get_all_records(trades_ws)
            
            stock_code_str = str(code).strip().upper().replace('KRX:', '').replace('A','')
            is_gold = (stock_code_str == 'GOLD')
            
            # 매매일지 필터링 및 날짜 기준 정렬
            filtered_trades = []
            for row in trades_records:
                code_in_row = str(row.get('종목코드', '')).strip().upper().replace('KRX:', '').replace('A','')
                name_in_row = str(row.get('종목명', '')).strip()
                
                match = False
                if is_gold:
                    if code_in_row == 'GOLD' or '금현물' in name_in_row or '금99' in name_in_row:
                        match = True
                else:
                    if code_in_row == stock_code_str:
                        match = True
                    elif name and name_in_row.replace(' ', '') == name.replace(' ', ''):
                        match = True
                        
                if match:
                    t_date = str(row.get('날짜', '')).strip()
                    if t_date:
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

                        dt_parsed = parse_date_robust(t_date)
                        if dt_parsed:
                            row['parsed_date'] = dt_parsed
                            filtered_trades.append(row)
            
            filtered_trades.sort(key=lambda x: x['parsed_date'])
            
            current_qty = 0.0
            current_total_cost = 0.0
            first_purchase_date = None
            
            for row in filtered_trades:
                trade_type = str(row.get('매매구분', '')).strip()
                qty = clean_numeric_value(row.get('수량', 0), float)
                price = clean_numeric_value(row.get('단가', 0), float)
                trade_date = row['parsed_date'].strftime("%Y-%m-%d")
                
                if qty <= 0:
                    continue
                    
                if '매수' in trade_type or 'BUY' in trade_type.upper():
                    if first_purchase_date is None:
                        first_purchase_date = trade_date
                    current_total_cost += qty * price
                    current_qty += qty
                elif '매도' in trade_type or 'SELL' in trade_type.upper():
                    if current_qty > 0:
                        avg_price = current_total_cost / current_qty
                        current_total_cost -= qty * avg_price
                        current_qty -= qty
                        if current_qty < 0:
                            current_qty = 0.0
                            current_total_cost = 0.0
            
            avg_cost = current_total_cost / current_qty if current_qty > 0 else 0.0
            
            # --- 2. 주가 이력 가져오기 ---
            price_history = []
            if first_purchase_date:
                if is_gold:
                    # 금현물은 구글 시트에서 직접 가져옴
                    try:
                        gold_ws = main_sheet.worksheet('📈금현물 수익률')
                        gold_data = gold_ws.get_all_values()
                        if len(gold_data) >= 2:
                            # A열: 날짜, J열: 금가격 (10번째 열)
                            for row in gold_data[1:]:
                                if len(row) >= 10:
                                    g_date = str(row[0]).strip()
                                    g_price_str = str(row[9]).strip()
                                    # 종가(금가격)가 실제로 채워져 있고 유효한 날짜만 필터링 (미래 데이터 원천 제거)
                                    if g_date >= first_purchase_date and g_price_str and g_price_str != '0' and g_price_str != '-':
                                        g_price = clean_numeric_value(g_price_str, float)
                                        if g_price > 0:
                                            price_history.append({
                                                "Date": g_date,
                                                "Close": g_price
                                            })
                            price_history.sort(key=lambda x: x['Date'])
                    except Exception as e:
                        print(f"Error reading gold rate sheet: {e}")
                else:
                    # 일반 주식/ETF는 Yahoo Finance API 활용
                    ticker = stock_code_str
                    if ticker.isdigit() and len(ticker) == 6:
                        price_history = fetch_yahoo_prices(f"{ticker}.KS", first_purchase_date)
                        if not price_history:
                            price_history = fetch_yahoo_prices(f"{ticker}.KQ", first_purchase_date)
                    else:
                        price_history = fetch_yahoo_prices(ticker, first_purchase_date)
            
            response_data = {
                "code": code,
                "name": name,
                "first_purchase_date": first_purchase_date,
                "avg_cost": round(avg_cost, 2),
                "history": price_history
            }
            
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
