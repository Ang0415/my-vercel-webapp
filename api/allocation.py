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

def parse_date_robust(date_str):
    cleaned = re.sub(r'[^\d]', '-', date_str.strip())
    cleaned = re.sub(r'-+', '-', cleaned).strip('-')
    parts = cleaned.split('-')
    if len(parts) >= 3:
        try:
            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""

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
            
            # --- 0. 가계부 순현금 잔액 실시간 조회 ---
            budget_sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
            net_cash_balance = 0.0
            try:
                budget_sh = gc.open_by_key(budget_sheet_key)
                budget_ws = budget_sh.worksheet('예산 및 설정')
                budget_rows = budget_ws.get_all_values()
                
                total_cash_accounts = 0
                total_card_outstanding = 0
                
                for row in budget_rows[1:]:
                    if len(row) > 3:
                        asset_name = row[0].strip()
                        curr_balance = clean_numeric_value(row[2].strip(), float)
                        asset_note = row[3].strip()
                        
                        if asset_name and asset_name != '합계':
                            if asset_note == '카드':
                                total_card_outstanding += abs(curr_balance)
                            elif asset_note != '정소현':
                                total_cash_accounts += curr_balance
                                
                net_cash_balance = total_cash_accounts - total_card_outstanding
            except Exception as e:
                print(f"Error fetching budget cash balance in allocation API: {e}")
            
            # 1. 설정 정보 읽기 ('⚙️설정' 시트)
            main_sheet = gc.open('KYI_자산배분')
            settings_ws = main_sheet.worksheet('⚙️설정')
            settings_records = safe_get_all_records(settings_ws)
            
            target_allocations = {}
            for row in settings_records:
                goal_type = str(row.get('목표구분', '')).strip()
                goal_nat = str(row.get('목표국적', '')).strip()
                goal_weight_str = str(row.get('목표비중', '')).replace('%', '').strip()
                
                if not goal_type or not goal_weight_str:
                    continue
                
                try:
                    goal_weight = float(goal_weight_str)
                except ValueError:
                    continue
                
                if goal_weight <= 0:
                    continue
                    
                if goal_type == '대체투자':
                    combined_name = '금'
                else:
                    combined_name = f"{goal_nat} {goal_type}" if goal_nat else goal_type
                
                if combined_name == "한국 채권":
                    combined_name = "한국 채권 30"
                    
                target_allocations[combined_name] = target_allocations.get(combined_name, 0.0) + goal_weight
            
            # 2. 실시간 비중 읽기 ('일별비중_Raw' 파일의 '일별비중_Raw' 시트)
            weights_sheet = gc.open('일별비중_RAW')
            worksheets_list = weights_sheet.worksheets()
            weights_ws = None
            for ws in worksheets_list:
                if ws.title.strip().lower() == '일별비중_raw':
                    weights_ws = ws
                    break
            
            if not weights_ws:
                raise Exception("Worksheet '일별비중_Raw' not found.")
                
            weights_records = safe_get_all_records(weights_ws)
            
            # 가장 최신 날짜 찾기
            latest_date = None
            for row in weights_records:
                row_date = parse_date_robust(str(row.get('날짜', '')))
                if row_date:
                    if not latest_date or row_date > latest_date:
                        latest_date = row_date
            
            current_allocations = {}
            if latest_date:
                total_live_asset = 0.0
                raw_amounts = {}
                
                # 최신 날짜 레코드만 필터링하여 종합분류 계산 (실시간 가계부 현금으로 덮어쓰기)
                for row in weights_records:
                    row_date_std = parse_date_robust(str(row.get('날짜', '')))
                    if row_date_std == latest_date:
                        asset_class = str(row.get('자산구분', '')).strip()
                        nationality = str(row.get('국적', '')).strip()
                        acct_name = str(row.get('계좌명', '')).strip()
                        
                        # 계좌명이 '현금'이거나 자산구분이 '기타'인 경우 -> '한국 현금성'으로 매핑하고 가계부 잔고 반영
                        if acct_name == '현금' or asset_class == '기타':
                            combined_name = '한국 현금성'
                            amount = net_cash_balance
                        else:
                            if not asset_class:
                                combined_name = '미분류'
                            elif asset_class == '대체투자':
                                combined_name = '금'
                            elif not nationality:
                                combined_name = asset_class
                            else:
                                combined_name = f"{nationality} {asset_class}"
                                if combined_name == "한국 채권":
                                    combined_name = "한국 채권 30"
                            amount = clean_numeric_value(row.get('평가금액', 0), float)
                        
                        raw_amounts[combined_name] = raw_amounts.get(combined_name, 0.0) + amount
                        total_live_asset += amount
                
                # 실시간 자산 총액을 기준으로 비중(%) 동적 재연산
                for combined_name, amount in raw_amounts.items():
                    live_weight = (amount / total_live_asset * 100.0) if total_live_asset > 0 else 0.0
                    current_allocations[combined_name] = {
                        'weight': live_weight,
                        'amount': amount
                    }
            
            # 3. 데이터 병합 및 비교 분석 (실시간 총자산 연동 방식)
            BASE_TOTAL_ASSET = total_live_asset
            
            # 모든 카테고리 추출
            all_categories = set(target_allocations.keys()) | set(current_allocations.keys())
            
            comparison = []
            for cat in all_categories:
                target_w = target_allocations.get(cat, 0.0)
                current_w = current_allocations.get(cat, {}).get('weight', 0.0)
                current_a = current_allocations.get(cat, {}).get('amount', 0.0)
                
                target_a = BASE_TOTAL_ASSET * (target_w / 100.0)
                diff_w = current_w - target_w
                diff_a = current_a - target_a
                
                comparison.append({
                    "category": cat,
                    "current_weight": round(current_w, 2),
                    "target_weight": round(target_w, 2),
                    "diff_weight": round(diff_w, 2),
                    "current_amount": int(current_a),
                    "target_amount": int(target_a),
                    "diff_amount": int(diff_a)
                })
            
            # 현재 비중 내림차순 정렬
            comparison.sort(key=lambda x: x['current_weight'], reverse=True)
            
            response_data = {
                "latest_date": latest_date,
                "comparison": comparison
            }
            
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
