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

def get_expected_yield(asset_class, nationality):
    """자산 구분과 국적을 기반으로 표준 기대 수익률을 반환합니다."""
    asset_class = str(asset_class).strip()
    nationality = str(nationality).strip()
    
    if '현금' in asset_class or '현금' in nationality or asset_class == '기타':
        return 2.0  # 현금성 자산
    if '채권' in asset_class:
        return 3.5  # 채권
    if '금' in asset_class or '대체투자' in asset_class:
        return 4.0  # 금/대체투자
    if '주식' in asset_class:
        if '미국' in nationality:
            return 9.0  # 미국 주식
        if '한국' in nationality:
            return 6.0  # 한국 주식
        return 8.0  # 기타 주식
    return 5.0  # 기본 기대수익률


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
            expected_annual_return = 5.50  # 기본 기대수익률 초기화
            try:
                # '일별비중_RAW' 파일에서 최신일 지출 평가액들을 합산하여 총평가액 산정 (실시간 현금 덮어쓰기)
                weights_sheet = gc.open('일별비중_RAW')
                weights_ws = None
                for ws in weights_sheet.worksheets():
                    if ws.title.strip().lower() == '일별비중_raw':
                        weights_ws = ws
                        break
                
                if weights_ws:
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
                            weighted_yield_sum = 0.0
                            for row in w_vals[1:]:
                                if len(row) > 0 and row[0].strip() == latest_date:
                                    # Create record
                                    record = {}
                                    for idx, val in enumerate(row):
                                        if idx < len(w_header):
                                            record[w_header[idx]] = val.strip()
                                            
                                    asset_class = str(record.get('자산구분', '')).strip()
                                    nationality = str(record.get('국적', '')).strip()
                                    acct_name = str(record.get('계좌명', '')).strip()
                                    
                                    if acct_name == '현금' or asset_class == '기타':
                                        amount = net_cash_balance
                                    else:
                                        amount = clean_numeric_value(record.get('평가금액', 0), float)
                                    total_asset += amount
                                    
                                    if amount > 0:
                                        y_rate = get_expected_yield(asset_class, nationality)
                                        weighted_yield_sum += amount * y_rate
                                        
                            if total_asset > 0:
                                expected_annual_return = weighted_yield_sum / total_asset
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
            
            # --- 3. 전체 TWR 추출 및 과거 연간 수익률 계산 ---
            latest_twr = 0.0
            annual_returns = {"2024": 2.58, "2025": 18.84, "2026_YTD": 15.63}
            try:
                twr_sheet = gc.open('성과_TWR_Raw')
                ws_twr = twr_sheet.worksheet('Total')
                all_records = ws_twr.get_all_records()
                if all_records:
                    # 1) 실시간 최신 TWR 추출
                    latest_record = all_records[-1]
                    twr_key = 'TWR' if 'TWR' in latest_record else ('twr' if 'twr' in latest_record else None)
                    if twr_key:
                        latest_twr = clean_numeric_value(latest_record.get(twr_key, 0), float)
                    
                    # 2) 과거 연말 TWR 추출 및 연간 수익률 복리 연산
                    date_key = None
                    for k in all_records[0].keys():
                        if '날짜' in k or 'Date' in k:
                            date_key = k
                            break
                    
                    if date_key and twr_key:
                        # 연도별 레코드 분류
                        records_by_year = {}
                        for r in all_records:
                            d_val = str(r.get(date_key, '')).strip()
                            t_val = clean_numeric_value(r.get(twr_key, 0), float)
                            if d_val and '-' in d_val:
                                try:
                                    yr = d_val.split('-')[0]
                                    records_by_year.setdefault(yr, []).append({'date': d_val, 'twr': t_val})
                                except Exception:
                                    pass
                        
                        twr_2024_end = 0.0
                        twr_2025_end = 0.0
                        twr_2026_current = latest_twr
                        
                        if '2024' in records_by_year:
                            records_2024 = sorted(records_by_year['2024'], key=lambda x: x['date'])
                            twr_2024_end = records_2024[-1]['twr']
                        if '2025' in records_by_year:
                            records_2025 = sorted(records_by_year['2025'], key=lambda x: x['date'])
                            twr_2025_end = records_2025[-1]['twr']
                            
                        # 복리 공식 기반 연간 수익률 계산
                        # 2024년 (시작 시점 0% 대비 성장)
                        ret_2024 = twr_2024_end
                        
                        # 2025년
                        ret_2025 = 0.0
                        denom_2025 = 1.0 + (twr_2024_end / 100.0)
                        if denom_2025 > 0:
                            ret_2025 = (((1.0 + (twr_2025_end / 100.0)) / denom_2025) - 1.0) * 100.0
                            
                        # 2026년 YTD
                        ret_2026 = 0.0
                        denom_2026 = 1.0 + (twr_2025_end / 100.0)
                        if denom_2026 > 0:
                            ret_2026 = (((1.0 + (twr_2026_current / 100.0)) / denom_2026) - 1.0) * 100.0
                            
                        annual_returns["2024"] = round(ret_2024, 2)
                        annual_returns["2025"] = round(ret_2025, 2)
                        annual_returns["2026_YTD"] = round(ret_2026, 2)
            except Exception as e:
                print(f"Error extracting TWR or historical annual returns: {e}")
                
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
                "gain_loss_rate_str": gain_loss_rate_str,
                "expected_annual_return": round(expected_annual_return, 2),
                "annual_returns": annual_returns
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
                "gain_loss_rate_str": "-",
                "expected_annual_return": 5.50,
                "annual_returns": {"2024": 2.58, "2025": 18.84, "2026_YTD": 15.63}
            }
            self.wfile.write(json.dumps(error_data).encode('utf-8'))