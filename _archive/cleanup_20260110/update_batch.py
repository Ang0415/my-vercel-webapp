
import time
import traceback
import os
import sys
from datetime import datetime
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- API/Auth Modules ---
# Ensure these are in the same directory or PYTHONPATH
try:
    import kiwoom_auth_isa as kiwoom_auth
    import kiwoom_domstk_isa as kiwoom_api
    import kis_auth_pension as kis_auth_pen
    import kis_domstk_pension as kis_api_pen
    import kis_auth_irp as kis_auth_irp
    import kis_domstk_irp as kis_api_irp
except ModuleNotFoundError as e:
    print(f"오류: 필요한 API 모듈을 찾을 수 없습니다 - {e}")
    sys.exit(1)

# --- Configuration ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')

BALANCE_RAW_SHEET = '일별잔고_Raw'
WEIGHTS_RAW_SHEET = '일별비중_Raw'
GOLD_SHEET = '📈금현물 수익률'
SETTINGS_SHEET = '⚙️설정'

# Accounts Configuration
ACCOUNTS = {
    '한투연금': {'auth': kis_auth_pen, 'api': kis_api_pen, 'type': 'KIS_PEN'},
    '한투IRP': {'auth': kis_auth_irp, 'api': kis_api_irp, 'type': 'KIS_IRP'},
    '키움ISA': {'auth': kiwoom_auth, 'api': kiwoom_api, 'type': 'KIWOOM_ISA'},
    '금현물': {'auth': None, 'api': None, 'type': 'GOLD'}
}

def clean_num_str(num_str, type_func=int):
    """Clean numeric strings (remove commas, handle errors)"""
    if isinstance(num_str, (int, float)): return num_str
    if not num_str: return type_func(0)
    try:
        cleaned_str = str(num_str).replace(',', '')
        is_negative = cleaned_str.startswith('-')
        cleaned = cleaned_str.lstrip('-').lstrip('0')
        if not cleaned: return type_func(0)
        value = type_func(cleaned)
        return -value if is_negative else value
    except (ValueError, TypeError): return type_func(0)

def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        return gc
    except Exception as e:
        print(f"❌ 구글 시트 연결 오류: {e}")
        return None

def main():
    print(f"--- 일별 데이터(잔고/비중) 업데이트 시작 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    # 1. API Authentication
    print("[1] API 인증...")
    auth_success_map = {'금현물': True}
    for acc_name, acc_info in ACCOUNTS.items():
        if acc_info['type'] == 'GOLD': continue
        if acc_info['auth']:
            try:
                auth_func_name = 'auth' if acc_info['type'].startswith('KIS') else 'authenticate'
                auth_func = getattr(acc_info['auth'], auth_func_name, None)
                if auth_func and auth_func():
                    print(f"  ✅ {acc_name} 인증 성공")
                    auth_success_map[acc_name] = True
                else:
                    print(f"  ❌ {acc_name} 인증 실패")
                    auth_success_map[acc_name] = False
            except Exception as e:
                print(f"  ❌ {acc_name} 인증 에러: {e}")
                auth_success_map[acc_name] = False

    # 2. Fetch Current Data (Balances & Holdings)
    print("\n[2] 데이터 조회...")
    account_balances = {}
    all_holdings = []
    
    gc = connect_google_sheets()
    if not gc: return

    try:
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        
        # 2-1. Fetch Gold Balance (from Sheet)
        gold_balance = 0
        try:
            gold_ws = spreadsheet.worksheet(GOLD_SHEET)
            gold_data = gold_ws.get_all_records()
            if gold_data:
                df_gold = pd.DataFrame(gold_data)
                df_gold['날짜_dt'] = pd.to_datetime(df_gold['날짜'], errors='coerce')
                latest_gold = df_gold.loc[df_gold['날짜_dt'].idxmax()]
                gold_balance = clean_num_str(latest_gold['평가액'])
                print(f"  ✅ 금현물: {gold_balance:,.0f} 원")
                all_holdings.append({
                    '계좌명': '금현물', '종목코드': 'GOLD', '종목명': '금현물', 
                    '평가금액': gold_balance, '자산구분': '대체투자', '국적구분': '기타'
                })
        except Exception as e: print(f"  ⚠️ 금현물 조회 오류: {e}")
        account_balances['금현물'] = gold_balance

        # 2-2. Fetch API Accounts
        for acc_name, acc_info in ACCOUNTS.items():
            if acc_info['type'] == 'GOLD' or not auth_success_map.get(acc_name): continue
            
            balance = 0
            holdings = []
            try:
                if acc_info['type'] == 'KIWOOM_ISA':
                    # Balance
                    res = kiwoom_api.get_account_evaluation_balance() # kt00018
                    if res and res.get('success'):
                        balance = clean_num_str(res['data'].get('tot_evlt_amt', '0'))
                        # Holdings
                        for item in res['data'].get('acnt_evlt_remn_indv_tot', []):
                            holdings.append({
                                '계좌명': acc_name,
                                '종목코드': item.get('stk_cd', ''),
                                '종목명': item.get('stk_nm', ''),
                                '평가금액': clean_num_str(item.get('evlt_amt', '0'))
                            })
                
                elif acc_info['type'] == 'KIS_PEN':
                    # Balance (Total)
                    res = kis_api_pen.get_inquire_balance_obj() # TTTC8434R
                    if res and res.get("rt_cd") == "0":
                         balance = clean_num_str(res.get('output2', [{}])[0].get('tot_evlu_amt', '0'))
                         # Holdings
                         for item in res.get('output1', []):
                             holdings.append({
                                 '계좌명': acc_name,
                                 '종목코드': item.get('pdno', ''),
                                 '종목명': item.get('prdt_name', ''),
                                 '평가금액': clean_num_str(item.get('evlu_amt', '0'))
                             })
                
                elif acc_info['type'] == 'KIS_IRP':
                    # Holdings & Balance Sum
                    res = kis_api_irp.get_inquire_present_balance_irp() # TTTC2202R
                    if isinstance(res, pd.DataFrame) and not res.empty:
                        res['evlu_amt_num'] = res['evlu_amt'].apply(lambda x: clean_num_str(x, int))
                        for _, row in res.iterrows():
                            # IRP uses 'pdno' or 'prdt_cd' (try pdno first)
                            code = row.get('pdno', row.get('prdt_cd', ''))
                            holdings.append({
                                '계좌명': acc_name,
                                '종목코드': code,
                                '종목명': row.get('prdt_name', ''),
                                '평가금액': int(row.get('evlu_amt_num', 0))
                            })
                
                print(f"  ✅ {acc_name}: {balance:,.0f} 원")
                account_balances[acc_name] = balance
                all_holdings.extend(holdings)
            except Exception as e:
                print(f"  ❌ {acc_name} 조회 오류: {e}")
                account_balances[acc_name] = 0

    except Exception as e:
        print(f"❌ 시트 초기화/조회 오류: {e}")
        return

    total_asset = int(sum(account_balances.values()))
    print(f"\n📊 총 자산: {total_asset:,.0f} 원")

    # 3. Load Mappings
    print("\n[3] 매핑 정보 로드...")
    asset_map = {}
    try:
        settings_ws = spreadsheet.worksheet(SETTINGS_SHEET)
        settings_data = settings_ws.get_all_values()
        if len(settings_data) > 1:
            header = settings_data[0]
            # Assuming fixed indices or finding them (simplified here based on previous file)
            # Try to find indices dynamically or fallback
            idx_code = 17; idx_class = 18; idx_nation = 19; idx_name = 16
            
            for row in settings_data[1:]:
                if len(row) > idx_nation:
                    code = str(row[idx_code]).strip().split(':')[-1].strip()
                    if code:
                        asset_map[code] = {
                            '자산구분': str(row[idx_class]).strip(),
                            '국적구분': str(row[idx_nation]).strip()
                        }
                        if code.isdigit(): asset_map['A' + code] = asset_map[code]
        print(f"  ✅ 매핑 정보 {len(asset_map)}개 로드")
    except Exception as e: print(f"  ⚠️ 매핑 로드 오류: {e}")

    # 4. Process Holdings (Map & Calculate Weights)
    today_str = datetime.now().strftime('%Y-%m-%d')
    processed_rows = []
    
    for item in all_holdings:
        code = str(item['종목코드']).strip()
        # Default Mapping
        a_type, nation = '미분류', '미분류'
        
        # Apply Mapping
        if code == 'GOLD': a_type, nation = '대체투자', '기타'
        elif code in asset_map:
             a_type = asset_map[code]['자산구분']
             nation = asset_map[code]['국적구분']
        else:
             # Try fallback 
             alt_code = code[1:] if code.startswith('A') else 'A' + code
             if alt_code in asset_map:
                 a_type = asset_map[alt_code]['자산구분']
                 nation = asset_map[alt_code]['국적구분']

        eval_amt = item['평가금액']
        if eval_amt > 0:
            weight_pct = (eval_amt / total_asset * 100) if total_asset > 0 else 0
            # Correct Column Order: Date, Account, Code, Name, Class, Nation, Amount, Weight
            processed_rows.append([
                today_str,
                item['계좌명'],
                item['종목코드'],
                item['종목명'],
                a_type,
                nation,
                eval_amt,
                round(weight_pct, 2)
            ])

    # 5. Update Google Sheets
    print("\n[4] 구글 시트 업데이트...")
    
    # 5-1. Update Balance Sheet (일별잔고_Raw)
    try:
        balance_ws = spreadsheet.worksheet(BALANCE_RAW_SHEET)
        # Check if today already exists
        dates = balance_ws.col_values(1)
        if today_str in dates:
             print(f"  ℹ️ {BALANCE_RAW_SHEET}: 오늘 날짜({today_str}) 데이터가 이미 존재합니다. 추가하지 않습니다.")
        else:
             # Append one row per account: [Date, AccountName, Balance]
             balance_rows = []
             for acc_name, val in account_balances.items():
                 balance_rows.append([today_str, acc_name, val])
             
             if balance_rows:
                 balance_ws.append_rows(balance_rows)
                 print(f"  ✅ {BALANCE_RAW_SHEET}: {len(balance_rows)}개 계좌 잔고 추가 완료.")
    except Exception as e: print(f"  ❌ {BALANCE_RAW_SHEET} 업데이트 오류: {e}")

    # 5-2. Update Weights Sheet (일별비중_Raw)
    try:
        weights_ws = spreadsheet.worksheet(WEIGHTS_RAW_SHEET)
        # Check for existing data for today (might want to overwrite or skip)
        # Here we skip if ANY row has today's date to avoid duplicates
        w_dates = weights_ws.col_values(1)
        if today_str in w_dates:
             print(f"  ℹ️ {WEIGHTS_RAW_SHEET}: 오늘 날짜({today_str}) 데이터가 이미 존재합니다. 추가하지 않습니다.")
        else:
             # Append all rows
             weights_ws.append_rows(processed_rows)
             print(f"  ✅ {WEIGHTS_RAW_SHEET}: {len(processed_rows)}개 행 추가 완료.")
    except Exception as e: print(f"  ❌ {WEIGHTS_RAW_SHEET} 업데이트 오류: {e}")

    print("\n--- 업데이트 완료 ---")

if __name__ == '__main__':
    main()
