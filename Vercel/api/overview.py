from http.server import BaseHTTPRequestHandler
import json
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def clean_numeric_value(value, type_func=float):
    """쉼표 및 기타 기호가 섞인 문자열을 숫자로 안전하게 변환합니다."""
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

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        try:
            # 1. 버셀 환경변수에서 GOOGLE_CREDENTIALS 불러오기
            creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
            if not creds_json_str:
                raise Exception("버셀 환경변수에서 GOOGLE_CREDENTIALS를 찾을 수 없습니다.")
            
            creds_dict = json.loads(creds_json_str)
            
            # 2. 구글 시트 인증
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            gc = gspread.authorize(creds)
            
            # --- 1. 총 원금 계산 ---
            # 'KYI_자산배분' 시트 열기
            main_sheet = gc.open('KYI_자산배분')
            account_sheets = ['📈ISA 수익률', '📈IRP 수익률', '📈연금 수익률', '📈금현물 수익률']
            total_principal = 0.0
            
            for sheet_name in account_sheets:
                try:
                    ws = main_sheet.worksheet(sheet_name)
                    # B열(입금) 가져오기 (헤더 제외)
                    deposit_values = ws.col_values(2) # B열 = 2
                    if len(deposit_values) > 1:
                        for val in deposit_values[1:]:
                            total_principal += clean_numeric_value(val, float)
                except gspread.exceptions.WorksheetNotFound:
                    continue
                except Exception as e:
                    print(f"Error calculating principal for {sheet_name}: {e}")
            
            # --- 2. 실시간 순현금 잔액 & 총 평가액 계산 ---
            net_cash_balance = 0.0
            budget_sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
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
                print(f"Error fetching budget cash balance in overview API: {e}")
                
            total_asset = 0.0
            try:
                # '일별비중_RAW' 파일에서 최신일 지출 평가액들을 합산하여 총평가액 산정 (실시간 현금 덮어쓰기)
                weights_sheet = gc.open('일별비중_RAW')
                weights_ws = None
                for ws in weights_sheet.worksheets():
                    if ws.title.strip().lower() == '일별비중_raw':
                        weights_ws = ws
                        break
                
                if weights_ws:
                    # safe_get_all_records logic in allocation is not present in overview, let's parse raw values
                    w_vals = weights_ws.get_all_values()
                    if len(w_vals) > 1:
                        w_header = [str(x).strip() for x in w_vals[0]]
                        
                        # Find latest date
                        latest_date = None
                        for row in w_vals[1:]:
                            if len(row) > 0 and row[0].strip():
                                r_date = row[0].strip()
                                if not latest_date or r_date > latest_date:
                                    latest_date = r_date
                                    
                        if latest_date:
                            for row in w_vals[1:]:
                                if len(row) > 0 and row[0].strip() == latest_date:
                                    # Create record
                                    record = {}
                                    for idx, val in enumerate(row):
                                        if idx < len(w_header):
                                            record[w_header[idx]] = val.strip()
                                            
                                    asset_class = str(record.get('자산구분', '')).strip()
                                    acct_name = str(record.get('계좌명', '')).strip()
                                    
                                    if acct_name == '현금' or asset_class == '기타':
                                        amount = net_cash_balance
                                    else:
                                        amount = clean_numeric_value(record.get('평가금액', 0), float)
                                    total_asset += amount
            except Exception as e:
                print(f"Error calculating total asset with live cash: {e}")
                # Fallback to historical Total sheet if error occurs
                try:
                    asset_sheet = gc.open('성과_자산추이_Raw')
                    ws_total = asset_sheet.worksheet('Total')
                    all_records = ws_total.get_all_records()
                    if all_records:
                        latest_record = all_records[-1]
                        val_key = 'Value' if 'Value' in latest_record else ('평가금액' if '평가금액' in latest_record else None)
                        if not val_key:
                            for k, v in latest_record.items():
                                if k != 'Date' and k != '날짜':
                                    val_key = k
                                    break
                        if val_key:
                            total_asset = clean_numeric_value(latest_record.get(val_key, 0), float)
                except Exception as e_inner:
                    print(f"Inner fallback total asset extraction error: {e_inner}")
            
            # --- 3. 전체 TWR 추출 ---
            latest_twr = 0.0
            try:
                twr_sheet = gc.open('성과_TWR_Raw')
                ws_twr = twr_sheet.worksheet('Total')
                all_records = ws_twr.get_all_records()
                if all_records:
                    latest_record = all_records[-1]
                    twr_key = 'TWR' if 'TWR' in latest_record else ('twr' if 'twr' in latest_record else None)
                    if twr_key:
                        latest_twr = clean_numeric_value(latest_record.get(twr_key, 0), float)
            except Exception as e:
                print(f"Error extracting TWR: {e}")
                
            # --- 4. 단순 손익 및 수익률 계산 ---
            gain_loss_amount = total_asset - total_principal
            gain_loss_rate = 0.0
            if total_principal > 0:
                gain_loss_rate = (gain_loss_amount / total_principal) * 100
                
            # 포맷팅 문자열 생성
            total_asset_str = f"{int(total_asset):,}"
            total_principal_str = f"{int(total_principal):,}"
            twr_str = f"{latest_twr:+.2f}%" if latest_twr != 0 else "0.00%"
            
            gain_loss_amount_str = f"{int(gain_loss_amount):+,}" if gain_loss_amount != 0 else "0"
            gain_loss_rate_str = f"{gain_loss_rate:+.2f}%" if gain_loss_rate != 0 else "0.00%"
            
            data = {
                "total_asset": int(total_asset),
                "total_asset_str": total_asset_str,
                "total_principal": int(total_principal),
                "total_principal_str": total_principal_str,
                "twr": latest_twr,
                "twr_str": twr_str,
                "gain_loss_amount": int(gain_loss_amount),
                "gain_loss_amount_str": gain_loss_amount_str,
                "gain_loss_rate": gain_loss_rate,
                "gain_loss_rate_str": gain_loss_rate_str
            }
            
            self.wfile.write(json.dumps(data).encode('utf-8'))
            
        except Exception as e:
            error_data = {
                "total_asset": 0,
                "total_asset_str": "에러 발생 🚨",
                "total_principal": 0,
                "total_principal_str": str(e),
                "twr": 0.0,
                "twr_str": "-",
                "gain_loss_amount": 0,
                "gain_loss_amount_str": "-",
                "gain_loss_rate": 0.0,
                "gain_loss_rate_str": "-"
            }
            self.wfile.write(json.dumps(error_data).encode('utf-8'))