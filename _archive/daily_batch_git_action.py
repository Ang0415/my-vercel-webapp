# daily_batch_git_action.py
# GitHub Actions용 일별 데이터 업데이트 스크립트
# (환경변수 'GCS_CREDENTIALS' 사용, Telegram 알림 포함)

import time
import traceback
import os
import sys
import json
from datetime import datetime
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Git Action Modules ---
# --- Git Action Modules (Flexible Import) ---
def safe_import(module_name_git, module_name_local, as_name):
    try:
        return __import__(module_name_git)
    except ModuleNotFoundError:
        try:
            return __import__(module_name_local)
        except ImportError as e:
            print(f"❌ 모듈 로드 실패 ({module_name_local}): {e}")
            # traceback.print_exc() # 상세 정보 필요시 주석 해제
            return None
        except Exception as e:
            print(f"❌ 모듈 로드 중 치명적 오류 ({module_name_local}): {e}")
            traceback.print_exc()
            return None

try:
    kiwoom_auth = safe_import('kiwoom_auth_isa_git_action', 'kiwoom_auth_isa', 'kiwoom_auth') or sys.exit(1)
    kiwoom_api = safe_import('kiwoom_domstk_isa_git_action', 'kiwoom_domstk_isa', 'kiwoom_api') or sys.exit(1)
    kis_auth_pen = safe_import('kis_auth_pension_git_action', 'kis_auth_pension', 'kis_auth_pen') or sys.exit(1)
    kis_api_pen = safe_import('kis_domstk_pension_git_action', 'kis_domstk_pension', 'kis_api_pen') or sys.exit(1)
    kis_auth_irp = safe_import('kis_auth_irp_git_action', 'kis_auth_irp', 'kis_auth_irp') or sys.exit(1)
    kis_api_irp = safe_import('kis_domstk_irp_git_action', 'kis_domstk_irp', 'kis_api_irp') or sys.exit(1)
    telegram_utils = safe_import('telegram_utils_git_action', 'telegram_utils', 'telegram_utils') or sys.exit(1)
except SystemExit:
    sys.exit(1)
except Exception as e:
    print(f"❌ 치명적 오류: 모듈 임포트 중 예외 발생 - {e}")
    sys.exit(1)

# --- Configuration ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
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


def safe_execute(func, *args, retries=3, sleep=5, **kwargs):
    """Execute function with retry logic for API stability"""
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "Quota exceeded" in str(e):
                print(f"⚠️ Quota exceeded. Retrying in {sleep}s... ({i+1}/{retries})")
                time.sleep(sleep * (i + 1))  # Exponential backoff
            else:
                print(f"⚠️ Error in {func.__name__}: {e}")
                if i == retries - 1: raise e
                time.sleep(1)
    return None

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
    """Google Sheets connection using Environment Variable 'GCS_CREDENTIALS'"""
    try:
        # GitHub Secrets에서 JSON 문자열 가져오기
        gcs_json_str = os.environ.get('GCS_CREDENTIALS')
        if not gcs_json_str:
            print("❌ 환경변수 'GCS_CREDENTIALS'가 없습니다. (로컬 폴백 시도)")
            # 로컬 JSON 파일 폴백 (필요 시)
            local_key_path = 'stock-auto-writer-44eaa06c140c.json'
            if os.path.exists(local_key_path):
                 print(f"ℹ️ 로컬 키 파일({local_key_path})을 사용합니다.")
                 scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                 credentials = ServiceAccountCredentials.from_json_keyfile_name(local_key_path, scope)
                 return gspread.authorize(credentials)
            else:
                 print("❌ 로컬 키 파일도 없습니다.")
                 return None

        key_dict = json.loads(gcs_json_str)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
        gc = gspread.authorize(credentials)
        return gc
    except Exception as e:
        print(f"❌ 구글 시트 연결 오류: {e}")
        return None

def main():
    start_time = time.time()
    print(f"--- 일별 데이터(잔고/비중) 업데이트 시작 (GitAction) ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    # 1. API Authentication
    print("[1] API 인증...")
    auth_success_map = {'금현물': True}
    for acc_name, acc_info in ACCOUNTS.items():
        if acc_info['type'] == 'GOLD': continue
        if acc_info['auth']:
            try:
                auth_func_name = 'auth' if acc_info['type'].startswith('KIS') else 'authenticate'
                
                # [GitAction Fix] Skip Kiwoom Auth (Device Token Issue)
                if acc_info['type'].startswith('KIWOOM'):
                     print(f"  ⏭️ {acc_name} 인증 건너뜀 (GitHub Environment)")
                     auth_success_map[acc_name] = False
                     continue

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
    if not gc: 
        telegram_utils.send_telegram_message("❌ [GitAction] 구글 시트 연결 실패")
        return

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
            if acc_info['type'] == 'GOLD': continue
            
            # Skip if auth failed or Kiwoom in GitAction (Device Token Issue)
            if not auth_success_map.get(acc_name):
                 print(f"  ⏭️ {acc_name} 인증 실패로 건너뜀 (Skip)")
                 account_balances[acc_name] = 0
                 continue

            balance = 0
            holdings = []
            try:

                if acc_info['type'] == 'KIWOOM_ISA':
                    # Balance
                    res = acc_info['api'].get_account_evaluation_balance() # kt00018
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
                    res = acc_info['api'].get_inquire_balance_obj() # TTTC8434R
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
                    res = acc_info['api'].get_inquire_present_balance_irp() # TTTC2202R
                    if isinstance(res, pd.DataFrame) and not res.empty:
                        res['evlu_amt_num'] = res['evlu_amt'].apply(lambda x: clean_num_str(x, int))
                        # Balance Sum from holdings (Approx)
                        balance = res['evlu_amt_num'].sum()
                        
                        for _, row in res.iterrows():
                            # IRP uses 'pdno' or 'prdt_cd'
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
        msg = f"❌ [GitAction] 시트 초기화/조회 오류: {e}"
        print(msg)
        telegram_utils.send_telegram_message(msg)
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
            # Simple fixed index assumptions or header search
            header = settings_data[0]
            # Try to find indices dynamically or fallback
            idx_code = 17; idx_class = 18; idx_nation = 19; idx_name = 16
            
            # Safe header finding (optional improvement)
            try:
                idx_code = header.index('종목코드')
                idx_class = header.index('구분')
                idx_nation = header.index('국적')
            except ValueError: pass # Fallback to hard-coded

            for row in settings_data[1:]:
                if len(row) > max(idx_code, idx_class, idx_nation):
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
        a_type, nation = '미분류', '미분류'
        
        # Apply Mapping
        if code == 'GOLD': a_type, nation = '대체투자', '기타'
        elif code in asset_map:
             a_type = asset_map[code]['자산구분']
             nation = asset_map[code]['국적구분']
        else:
             alt_code = code[1:] if code.startswith('A') else 'A' + code
             if alt_code in asset_map:
                 a_type = asset_map[alt_code]['자산구분']
                 nation = asset_map[alt_code]['국적구분']

        eval_amt = item['평가금액']
        if eval_amt > 0:
            weight_pct = (eval_amt / total_asset * 100) if total_asset > 0 else 0
            # [Date, Account, Code, Name, Class, Nation, Amount, Weight]
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
    update_msg = []
    
    # 5-1. Update Balance Sheet
    try:
        balance_ws = spreadsheet.worksheet(BALANCE_RAW_SHEET)
        dates = balance_ws.col_values(1)
        if today_str in dates:
             msg = f"ℹ️ {BALANCE_RAW_SHEET}: 오늘 날짜({today_str}) 데이터 있음 (Skip)"
             print(f"  {msg}")
             update_msg.append(msg)
        else:
             balance_rows = []
             for acc_name, val in account_balances.items():
                 balance_rows.append([today_str, acc_name, val])
             if balance_rows:
                 balance_ws.append_rows(balance_rows)
                 msg = f"✅ {BALANCE_RAW_SHEET}: {len(balance_rows)}개 계좌 잔고 추가"
                 print(f"  {msg}")
                 update_msg.append(msg)
    except Exception as e:
        err = f"❌ {BALANCE_RAW_SHEET} 오류: {e}"
        print(f"  {err}")
        update_msg.append(err)

    # 5-2. Update Weights Sheet
    try:
        weights_ws = spreadsheet.worksheet(WEIGHTS_RAW_SHEET)
        w_dates = weights_ws.col_values(1)
        if today_str in w_dates:
             msg = f"ℹ️ {WEIGHTS_RAW_SHEET}: 오늘 날짜({today_str}) 데이터 있음 (Skip)"
             print(f"  {msg}")
             update_msg.append(msg)
        else:
             if processed_rows:
                 weights_ws.append_rows(processed_rows)
                 msg = f"✅ {WEIGHTS_RAW_SHEET}: {len(processed_rows)}개 행 추가"
                 print(f"  {msg}")
                 update_msg.append(msg)
    except Exception as e:
        err = f"❌ {WEIGHTS_RAW_SHEET} 오류: {e}"
        print(f"  {err}")
        update_msg.append(err)

    print("\n--- 업데이트 완료 ---")
    
    # Telegram Notification
    elapsed = time.time() - start_time
    final_msg = f"✅ [GitAction] 배치 완료 ({elapsed:.1f}초)\n" + "\n".join(update_msg)
    telegram_utils.send_telegram_message(final_msg)

if __name__ == '__main__':
    main()
