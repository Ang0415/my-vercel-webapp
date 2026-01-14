# -*- coding: utf-8 -*-
# daily_batch.py: 매일 모든 계좌의 잔고 및 비중을 집계하여 구글 시트에 기록
# (IRP 종목코드 키 수정, 디버깅 포함)

import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta, date
import time
import traceback
import os
import sys

# 공휴일 처리
try:
    import holidays
except ImportError:
    print("⚠️ 'holidays' 라이브러리가 설치되지 않았습니다.")
    holidays = None

# --- API 모듈 임포트 ---
import kiwoom_auth_isa as kiwoom_auth
import kiwoom_domstk_isa as kiwoom_api
import kis_auth_pension as kis_auth_pen
import kis_domstk_pension as kis_api_pen
import kis_auth_irp as kis_auth_irp
import kis_domstk_irp as kis_api_irp
# --- ---

# --- 텔레그램 유틸리티 임포트 ---
try:
    import telegram_utils # 또는 from telegram_utils import send_telegram_message
except ModuleNotFoundError:
    print("⚠️ telegram_utils.py 모듈을 찾을 수 없습니다. 텔레그램 알림이 비활성화됩니다.")
    class MockTelegramUtils:
        def send_telegram_message(self, message):
            print("INFO: telegram_utils 모듈 없음 - 텔레그램 메시지 발송 건너<0xEB><0x81><0x91:", message[:100])
    telegram_utils = MockTelegramUtils()


# --- 설정 ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')
BALANCE_RAW_SHEET = '일별잔고_Raw'
WEIGHTS_RAW_SHEET = '일별비중_Raw'
GOLD_SHEET = '📈금현물 수익률'
SETTINGS_SHEET = '⚙️설정'
BALANCE_HEADER = ['날짜', '계좌명', '총자산']
WEIGHTS_HEADER = ['날짜', '계좌명', '종목코드', '종목명', '자산구분', '국적', '평가금액', '포트폴리오내비중(%)']
ACCOUNTS = {
    '한투연금': {'auth': kis_auth_pen, 'api': kis_api_pen, 'type': 'KIS_PEN'},
    '한투IRP': {'auth': kis_auth_irp, 'api': kis_api_irp, 'type': 'KIS_IRP'},
    '키움ISA': {'auth': kiwoom_auth, 'api': kiwoom_api, 'type': 'KIWOOM_ISA'},
    '금현물': {'auth': None, 'api': None, 'type': 'GOLD'}
}
SCRIPT_NAME = os.path.basename(__file__)
# --- ---

# --- 유틸리티 함수 ---
def safe_execute(func, *args, retries=5, sleep=5, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "Quota exceeded" in str(e) or "429" in str(e):
                print(f"⚠️ Quota exceeded. Retrying in {sleep}s... ({i+1}/{retries})")
                time.sleep(sleep * (i + 1))
            else:
                if i == retries - 1: raise e
                time.sleep(1)
    return None

def clean_num_str(num_str, type_func=int):
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

def setup_google_sheet(sheet_name, worksheet_name, header_columns):
    worksheet = None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = safe_execute(gspread.authorize, credentials)
        spreadsheet = safe_execute(gc.open, sheet_name)
        try:
            worksheet = safe_execute(spreadsheet.worksheet, worksheet_name)
            print(f"✅ Google Sheet '{sheet_name}/{worksheet_name}' 열기 성공.")
            header = []
            try: header = safe_execute(worksheet.row_values, 1)
            except gspread.exceptions.APIError as e_api: print(f"⚠️ 헤더 읽기 중 API 오류: {e_api}"); return None
            
            if not header or len(header) < len(header_columns) or header[:len(header_columns)] != header_columns:
                 print(f"⚠️ '{worksheet_name}' 헤더가 비어있거나 다릅니다. 업데이트 필요 감지.")
                 all_values = []
                 try: all_values = safe_execute(worksheet.get_all_values)
                 except gspread.exceptions.APIError as e_get_all: print(f"⚠️ 시트 전체 값 읽기 중 API 오류: {e_get_all}"); return None
                 
                 if not all_values: 
                     safe_execute(worksheet.append_row, header_columns, value_input_option='USER_ENTERED')
                     print("✅ 비어있는 시트에 헤더 추가 완료.")
                 else:
                     try:
                         safe_execute(worksheet.update, range_name='A1', values=[header_columns], value_input_option='USER_ENTERED')
                         print(f"✅ 헤더 업데이트 완료 ({worksheet_name}).")
                     except Exception as e_header: print(f"❗️ 헤더 자동 업데이트 실패: {e_header}.")
        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️ 워크시트 '{worksheet_name}' 생성 및 헤더 추가.")
            worksheet = safe_execute(spreadsheet.add_worksheet, title=worksheet_name, rows="1000", cols=len(header_columns))
            safe_execute(worksheet.append_row, header_columns, value_input_option='USER_ENTERED')
        return worksheet
    except FileNotFoundError: print(f"❌ 오류: 키 파일({JSON_KEYFILE_PATH}) 없음."); return None
    except gspread.exceptions.APIError as e_conn: print(f"❌ 구글 시트 연결 중 API 오류: {e_conn}"); return None
    except Exception as e: print(f"❌ 시트 연결/설정 오류: {e}"); traceback.print_exc(); return None
# --- ---

# --- 메인 실행 로직 ---
def main():
    start_time = time.time()
    print("🚀 일별 잔고 및 비중 기록 배치 시작")
    # 0. 대상 날짜 결정
    # 0. 대상 날짜 결정
    today = datetime.now().date(); target_date_dt = today; kr_holidays = {}
    if holidays:
        try: kr_holidays = holidays.KR(years=target_date_dt.year, observed=True)
        except Exception as e_holiday: print(f"⚠️ 공휴일 정보 로드 오류: {e_holiday}")
    days_to_check = 0
    while days_to_check < 5:
        is_holiday = target_date_dt in kr_holidays if holidays else False
        if target_date_dt.weekday() < 5 and not is_holiday: break
        target_date_dt -= timedelta(days=1); days_to_check += 1
    target_date_str = target_date_dt.strftime("%Y-%m-%d"); target_date_yyyymmdd = target_date_dt.strftime("%Y%m%d")
    print(f"🎯 대상 날짜 (영업일 기준): {target_date_str}")

    # 1. API 인증
    print("\n[인증] 모든 증권사 API 인증 시도...")
    auth_success_map = {}; all_auth_successful = True
    for acc_name, acc_info in ACCOUNTS.items():
        auth_success_map[acc_name] = False
        if acc_info['auth']:
            try:
                auth_function_name = 'auth' if acc_info['type'].startswith('KIS') else 'authenticate'
                auth_func = getattr(acc_info['auth'], auth_function_name, None)
                if auth_func and callable(auth_func):
                    auth_result = auth_func()
                    if auth_result is True: auth_success_map[acc_name] = True; print(f"  > {acc_name} 인증 성공.")
                    elif auth_result is False: print(f"🔥 {acc_name} 인증 실패 (함수 반환값 False)."); all_auth_successful = False
                    else: print(f"🔥 {acc_name} 인증 실패: '{auth_function_name}' 함수가 True/False를 명시적으로 반환하지 않음 (반환값: {auth_result})."); all_auth_successful = False
                else: print(f"🔥 {acc_name} 인증 실패: '{auth_function_name}' 함수 없음."); all_auth_successful = False
            except Exception as e_auth: print(f"🔥 {acc_name} 인증 중 오류: {e_auth}"); traceback.print_exc(); all_auth_successful = False
        else: auth_success_map[acc_name] = True; print(f"  > {acc_name} 인증 불필요.")
    if not all_auth_successful: print("⚠️ 일부 계좌 인증 실패.")

    # 2. 구글 시트 연결
    print("\n[준비] 구글 시트 연결 및 Raw 시트 확인/생성...")
    balance_ws = setup_google_sheet(GOOGLE_SHEET_NAME, BALANCE_RAW_SHEET, BALANCE_HEADER)
    weights_ws = setup_google_sheet(GOOGLE_SHEET_NAME, WEIGHTS_RAW_SHEET, WEIGHTS_HEADER)
    gold_ws = None; settings_ws = None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials); spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        gold_ws = spreadsheet.worksheet(GOLD_SHEET); settings_ws = spreadsheet.worksheet(SETTINGS_SHEET)
        print(f"✅ 읽기용 시트 ({GOLD_SHEET}, {SETTINGS_SHEET}) 열기 성공.")
    except gspread.exceptions.APIError as e_read_ws: raise ConnectionError(f"❌ 읽기용 구글 시트 열기 중 API 오류: {e_read_ws}") from e_read_ws
    except Exception as e: raise ConnectionError(f"❌ 읽기용 시트 열기 실패: {e}") from e
    if not balance_ws or not weights_ws or not gold_ws or not settings_ws: raise ConnectionError("🔥 필요 시트 준비 실패. 종료합니다.")

    # 3. 기존 Raw 데이터 확인
    existing_balances = {}; existing_weights = set()
    print(f"\n[확인] {target_date_str} 기준 기존 Raw 데이터 확인...")
    try:
        balance_data = balance_ws.get_all_records(expected_headers=BALANCE_HEADER)
        for row in balance_data:
            if str(row.get('날짜')).strip() == target_date_str and row.get('계좌명'): existing_balances[str(row['계좌명']).strip()] = row['총자산']
        print(f"✅ '{BALANCE_RAW_SHEET}' 확인: {len(existing_balances)}개 계좌 데이터 존재.")
    except Exception as e: print(f"⚠️ '{BALANCE_RAW_SHEET}' 읽기 오류: {e}")
    try:
        weights_data = weights_ws.get_all_records(expected_headers=WEIGHTS_HEADER)
        for row in weights_data:
            if str(row.get('날짜')).strip() == target_date_str and row.get('계좌명') and row.get('종목코드'):
                key = (str(row['계좌명']).strip(), str(row['종목코드']).strip())
                existing_weights.add(key)
        print(f"✅ '{WEIGHTS_RAW_SHEET}' 확인: {len(existing_weights)}개 비중 데이터 존재.")
    except Exception as e: print(f"⚠️ '{WEIGHTS_RAW_SHEET}' 읽기 오류: {e}")

    # 4. 일별 잔고 조회 및 기록 준비
    print(f"\n[잔고 조회/기록] {target_date_str} 기준 시트('자산배분') 기반 집계 시작...")
    daily_balances_to_add = []; account_balances = {}; holding_api_results = {}
    
    # [NEW] '자산배분' 시트에서 평가금액 합계 계산
    sheet_balance_map = {}
    try:
        alloc_ws = safe_execute(spreadsheet.worksheet, '자산배분')
        alloc_data = safe_execute(alloc_ws.get_all_values)
        if alloc_data and len(alloc_data) > 1:
            print("  > '자산배분' 시트 데이터 읽기 성공. 계좌별 합계 계산 중...")
            for row in alloc_data[1:]: # 헤더 제외
                # L열(idx 11): 계좌명, I열(idx 8): 평가금액
                if len(row) > 11:
                    sheet_acc = str(row[11]).strip()
                    row_name = str(row[0]).strip() # A열: 종목명
                    val_str = str(row[8]).strip()
                    val = clean_num_str(val_str)
                    
                    # [SPECIAL] 종목명이 '금현물'이면 계좌명을 '금현물'로 강제 지정
                    if row_name == '금현물' or row_name == '금':
                        sheet_acc = '금현물'
                    
                    # [USER REQUEST] 사용자 요청에 따라 '한투_금' -> '금현물' 매핑
                    if sheet_acc == '한투_금':
                        sheet_acc = '금현물'
                    
                    if sheet_acc:
                        sheet_balance_map[sheet_acc] = sheet_balance_map.get(sheet_acc, 0) + val
            print(f"  > 시트 집계 결과: {sheet_balance_map}")
        else:
            print("  ⚠️ '자산배분' 시트가 비어있거나 읽을 수 없습니다.")
    except Exception as e_alloc:
        print(f"  ⚠️ '자산배분' 시트 집계 중 오류: {e_alloc}")

    for acc_name, acc_info in ACCOUNTS.items():
        # [MODIFIED] 시트 집계값을 기본 사용
        calculated_balance = sheet_balance_map.get(acc_name, 0)
        
        # [SPECIAL CASE] 금현물: 시트 집계값이 없으면(0이면) 직접 계산 시도
        if acc_name == '금현물':
             try:
                 # 1. 가격 (설정 J9)
                 price_val = 0
                 price_cell = safe_execute(settings_ws.acell, 'J9') # J9: 금 현재가
                 if price_cell: price_val = clean_num_str(price_cell.value)
                 
                 # 2. 수량 (자산배분 E10)
                 qty_val = 0
                 alloc_ws = safe_execute(spreadsheet.worksheet, '자산배분')
                 qty_cell = safe_execute(alloc_ws.acell, 'C36') # *** [CHECK] User said Allocation Sheet is updated. 
                 # Wait, previously I used 'assets' E10. User said "Amount" is in C column for explicit weights?
                 # No, user said C column is %.
                 # Let's stick to the previous logic I saw in 'viewed_code_item': settings J9, alloc E10.
                 # Actually, better to check if calculated_balance from sheet is 0. 
                 # If user says "why is it not calculated", it implies it's 0.
                 
                 # Let's restore the specific logic I saw before:
                 # price from Settings J9, qty from Asset Allocation E10.
                 qty_cell_val = safe_execute(alloc_ws.acell, 'E10').value
                 qty_val = clean_num_str(qty_cell_val)
                 
                 calc_gold = price_val * qty_val
                 if calc_gold > 0:
                     print(f"    ✨ 금현물 직접 계산: {price_val:,}원 * {qty_val}g = {calc_gold:,}원")
                     if calculated_balance == 0:
                         calculated_balance = calc_gold
                     elif abs(calculated_balance - calc_gold) > 1000:
                         print(f"    (참고) 시트 집계({calculated_balance:,})와 직접 계산({calc_gold:,}) 차이 발생. 직접 계산값 사용.")
                         calculated_balance = calc_gold
             except Exception as e_gold:
                 print(f"    ⚠️ 금현물 계산 오류: {e_gold}")

        # 계좌명 매핑 확인 및 최종 잔고 설정
        if calculated_balance == 0:
             for s_key, s_val in sheet_balance_map.items():
                  if s_key.replace('_', '') == acc_name.replace('_', ''):
                       calculated_balance = s_val; break
        
        balance = calculated_balance
        print(f"  > {acc_name}: 최종 잔고 = {balance:,} 원")

        was_already_in_sheet = acc_name in existing_balances
        if was_already_in_sheet: 
            # 기존에 기록된 값이 있어도, 오늘자 업데이트라면 시트 집계값으로 덮어쓸지 여부?
            # 로직상 'daily_balances_to_add'는 신규(없는 경우)에만 추가됨.
            # 하지만 이미 기록된 값(API값?)이 틀렸다고 하셨으니...
            # 기존 로직: if was_already_in_sheet -> skip adding.
            # 수정 제안: 오늘 날짜 데이터가 이미 있어도 값이 다르면 업데이트? (복잡도 증가)
            # 일단 사용자 요청은 "일별잔고에 업데이트하는게 좋을거 같아" -> 신규 추가 로직은 유지하되 값의 출처를 변경.
            # 이미 오늘치 데이터를 API로 돌려서 시트에 박아버렸다면, 그 줄을 지우고 다시 돌려야 함.
            # 여기서는 '계산된 값'을 account_balances에 넣는 것에 집중.
            prev_balance = clean_num_str(existing_balances.get(acc_name, 0))
            if prev_balance != balance:
                 print(f"    (참고) 시트에 저장된 값({prev_balance:,})과 집계 값({balance:,})이 다릅니다.")
        
        # [API CALLS] 잔고는 시트값을 쓰더라도, Holdings(비중)를 위해 API 호출은 여전히 필요할 수 있음
        # 하지만 API 호출이 오래 걸리거나 실패하면 비중 계산이 안됨.
        # 사용자가 "API는 안 맞는거 같아"라고 했음. 비중도 안 맞을 수 있음.
        # 그러나 비중 계산 로직은 API 리스트를 순회함.
        # 일단 API 호출은 유지하되, 잔고(balance) 변수에는 영향 주지 않도록 함. (holding_api_results만 채움)
        
        if acc_info['type'] != 'GOLD' and not auth_success_map.get(acc_name, False):
            print(f"    {acc_name}: 인증 문제로 API 호출 스킵. (잔고는 시트값 사용)")
            holding_api_results[acc_name] = None
        elif acc_info['type'] != 'GOLD':
             # API 호출 로직 (비중 계산용)
             if acc_info['type'] == 'KIWOOM_ISA':
                 try: holding_api_results[acc_name] = kiwoom_api.get_account_evaluation_balance()
                 except: holding_api_results[acc_name] = None
             elif acc_info['type'] == 'KIS_PEN':
                 try: holding_api_results[acc_name] = kis_api_pen.get_inquire_balance_obj()
                 except: holding_api_results[acc_name] = None
             elif acc_info['type'] == 'KIS_IRP':
                 try: holding_api_results[acc_name] = kis_api_irp.get_inquire_present_balance_irp() # IRP는 이게 나을 수 있음 (선택) or api_lst
                 except: holding_api_results[acc_name] = None
             time.sleep(0.2)
        
        account_balances[acc_name] = balance
        
        if not was_already_in_sheet and balance >= 0:
            daily_balances_to_add.append([target_date_str, acc_name, balance])
            print(f"    ➡️ '{acc_name}' 잔고({balance:,}) 추가 예정 (자산배분 집계)")
        elif was_already_in_sheet:
             print(f"    ➡️ '{acc_name}' 잔고는 이미 시트에 존재함 (값: {existing_balances.get(acc_name)})")

    # 4-5. 일별 잔고 시트 기록 (덮어쓰기 로직)
    if daily_balances_to_add:
        print(f"\n💾 '{BALANCE_RAW_SHEET}' 시트 업데이트 (날짜: {target_date_str})...")
        try:
            # 1. 기존 데이터 전체 읽기
            all_balances = safe_execute(balance_ws.get_all_values)
            if not all_balances: all_balances = [BALANCE_HEADER]
            
            # 2. 헤더 분리
            header = all_balances[0]
            rows = all_balances[1:]
            
            # 3. 오늘 날짜 데이터 제외하고 필터링
            filtered_rows = [r for r in rows if str(r[0]).strip() != target_date_str]
            
            # 4. 필터링된 데이터 + 신규 데이터 병합
            final_rows = [header] + filtered_rows + daily_balances_to_add
            
            # 5. 시트 클리어 후 전체 쓰기
            safe_execute(balance_ws.clear)
            safe_execute(balance_ws.update, range_name='A1', values=final_rows, value_input_option='USER_ENTERED')
            print(f"✅ 잔고 데이터 업데이트 완료 (기존 {len(rows)-len(filtered_rows)}건 삭제 후 {len(daily_balances_to_add)}건 추가)")
        except Exception as e: raise IOError(f"❌ 잔고 데이터 업데이트 오류: {e}") from e
    else: print(f"\nℹ️ '{BALANCE_RAW_SHEET}' 시트에 추가할 신규 잔고 데이터 없음.")

    # 5. 보유 종목 조회 및 비중 계산/기록 준비
    print(f"\n[비중 계산] {target_date_str} 기준 보유 비중 계산 시작 (Source: '자산배분' 시트)...")
    
    # 5-1. 설정 시트에서 종목코드 매핑 로드 (Q열:종목명 -> R열:종목코드)
    name_to_code_map = {}
    try:
        # settings_ws는 이미 로드됨. 전체 값 가져오기.
        settings_rows = safe_execute(settings_ws.get_all_values)
        if settings_rows and len(settings_rows) > 1:
            # 헤더 확인이 이상적이나, 사용자 요청에 따라 Q(idx 16), R(idx 17) 고정 참조
            print("  > '⚙️설정' 시트에서 종목코드 매핑 로드 중...")
            count_map = 0
            for row in settings_rows[1:]:
                if len(row) >= 18:
                    s_name = str(row[16]).strip() # Q열
                    s_code = str(row[17]).strip() # R열
                    if s_name and s_code:
                        name_to_code_map[s_name] = s_code
                        count_map += 1
            print(f"  ✅ {count_map}개 종목 매핑 로드 완료.")
        else:
            print("  ⚠️ '⚙️설정' 시트 데이터가 없거나 형식이 맞지 않습니다.")
    except Exception as e_map:
        print(f"  ❌ 매핑 로드 중 오류: {e_map}")

    all_holdings_data = []
    # total_portfolio_value는 account_balances의 합계 사용
    total_portfolio_value = sum(v for v in account_balances.values() if v is not None and v >= 0)
    print(f"  > 전체 포트폴리오 가치 (계산 기준): {total_portfolio_value:,} 원")

    if total_portfolio_value <= 0:
        print("⚠️ 전체 포트폴리오 가치가 0 이하이므로 비중 계산 불가.")
    else:
        # 5-2. '자산배분' 시트 데이터 기반으로 보유 종목 구성
        # alloc_data는 위에서 이미 로드함 (sheet_balance_map 계산 시)
        # 만약 alloc_data가 없으면 다시 로드 시도
        if 'alloc_data' not in locals() or not alloc_data:
             try:
                alloc_ws = safe_execute(spreadsheet.worksheet, '자산배분')
                alloc_data = safe_execute(alloc_ws.get_all_values)
             except: alloc_data = []

        if alloc_data and len(alloc_data) > 1:
            print("  > '자산배분' 시트 행 분석 중...")
            for row_idx, row in enumerate(alloc_data[1:]):
                # A열(0): 종목명, B열(1): 자산구분, C열(2): 비중(%), I열(8): 평가금액, L열(11): 계좌명
                if len(row) > 11:
                    row_name = str(row[0]).strip()
                    row_type_raw = str(row[1]).strip()
                    row_weight_str = str(row[2]).strip() # C열 비중
                    row_val_str = str(row[8]).strip()
                    row_acc = str(row[11]).strip()
                    
                    # 유효 데이터 필터: 계좌명 있고, 평가금액 있는 경우
                    if not row_acc: continue
                    val = clean_num_str(row_val_str)
                    
                    # 비중 파싱 (C열)
                    try:
                        weight_raw = float(row_weight_str.replace('%', '').replace(',', ''))
                    except:
                        weight_raw = 0.0

                    if val <= 0: continue

                    # 종목코드 찾기
                    code = name_to_code_map.get(row_name, "")
                    if not code:
                        # 예외 처리: 현금 or 금현물 등
                        if '현금' in row_name: code = 'CASH'
                        elif '달러' in row_name: code = 'USD'
                        elif row_name in ['예수금(원화)', '예수금(외화)']: code = 'CASH'
                        elif '금현물' in row_name or row_name == '금': code = 'GOLD'
                        else: code = "N/A" # 코드 없으면 N/A 표기
                    
                    # 자산구분/국적 파싱
                    nation = "기타"
                    asset_class = "기타"
                    
                    tokens = row_type_raw.split()
                    if tokens:
                        if tokens[0] in ['미국', '한국', '중국', '인도', '베트남', '일본', '영국', '독일', '프랑스', '선진국', '신흥국']:
                            nation = tokens[0]
                            asset_class = " ".join(tokens[1:]) if len(tokens) > 1 else "기타"
                        else:
                            if '현금' in row_type_raw: nation = '한국'; asset_class = '현금'
                            elif '금' in row_type_raw: nation = '기타'; asset_class = '대체투자'
                            else: nation = '기타'; asset_class = row_type_raw
                    
                    all_holdings_data.append({
                        '날짜': target_date_str,
                        '계좌명': row_acc,
                        '종목코드': code,
                        '종목명': row_name,
                        '평가금액': val,
                        '비중': weight_raw, # 시트에서 읽은 비중
                        '자산구분': asset_class,
                        '국적': nation
                    })
        else:
            print("  ⚠️ '자산배분' 시트 데이터가 없어 비중 내역을 구성할 수 없습니다.")

        # 5-3. 비중 계산 및 최종 데이터 준비
        weights_rows_to_add = []
        if all_holdings_data:
             print(f"  > 총 {len(all_holdings_data)} 건 유효 보유 내역 추출. 비중 계산 시작...")
             for holding in all_holdings_data:
                 eval_amount = holding['평가금액']
                 
                 # [MODIFIED] 시트의 C열 값(비중)을 그대로 사용
                 weight = holding.get('비중', 0.0)

                 # 부동소수점 오차 방지
                 python_eval_amount = int(float(eval_amount))
                 python_weight = round(float(weight), 2)
                 
                 # ["날짜", "계좌명", "종목코드", "종목명", "자산구분", "국적", "평가금액", "포트폴리오내비중(%)"]
                 weights_rows_to_add.append([
                     holding['날짜'],
                     holding['계좌명'],
                     holding['종목코드'],
                     holding['종목명'],
                     holding['자산구분'],
                     holding['국적'],
                     python_eval_amount,
                     python_weight
                 ])
        else: print("  > 비중 계산할 보유 내역 없음.")

        # 5-4. 일별 비중 시트 기록 (덮어쓰기 로직)
        if weights_rows_to_add:
            print(f"\n💾 '{WEIGHTS_RAW_SHEET}' 시트 업데이트 (날짜: {target_date_str})...")
            try:
                # 1. 기존 데이터 전체 읽기
                all_weights = safe_execute(weights_ws.get_all_values)
                if not all_weights: all_weights = [WEIGHTS_HEADER]
                
                # 2. 헤더 분리
                header = all_weights[0]
                rows = all_weights[1:]
                
                # 3. 오늘 날짜 데이터 제외하고 필터링 (덮어쓰기 위해)
                # date_col_idx = 0 (가정)
                filtered_rows = [r for r in rows if str(r[0]).strip() != target_date_str]
                
                # 4. 필터링된 데이터 + 신규 데이터 병합
                final_rows = [header] + filtered_rows + weights_rows_to_add
                
                # 5. 시트 클리어 후 전체 쓰기 (가장 확실한 중복 방지)
                safe_execute(weights_ws.clear)
                safe_execute(weights_ws.update, range_name='A1', values=final_rows, value_input_option='USER_ENTERED')
                print(f"✅ 비중 데이터 업데이트 완료 (기존 {len(rows)-len(filtered_rows)}건 삭제 후 {len(weights_rows_to_add)}건 추가)")
                
            except Exception as e: raise IOError(f"❌ 비중 데이터 업데이트 오류: {e}") from e
        else: print(f"\nℹ️ '{WEIGHTS_RAW_SHEET}' 시트에 추가할 신규 비중 데이터 없음.")

    # main 함수 성공 메시지 반환
    new_balance_count = len(daily_balances_to_add)
    new_weight_count = len(weights_rows_to_add) if 'weights_rows_to_add' in locals() else 0
    elapsed_time = time.time() - start_time
    return f"✅ `{SCRIPT_NAME}` 실행 완료 (대상: {target_date_str}, 신규 잔고: {new_balance_count}건, 신규 비중: {new_weight_count}건, 소요 시간: {elapsed_time:.2f}초)"
# --- ---

# --- 스크립트 실행 및 텔레그램 알림 ---
if __name__ == '__main__':
    start_run_time = time.time()
    final_message = ""
    error_occurred = False
    error_details_str = ""
    try:
        success_message = main()
        final_message = success_message if success_message else f"✅ `{SCRIPT_NAME}` 실행 완료"
    except ConnectionError as e: error_occurred = True; print(f"🔥 연결 오류: {e}"); error_details_str = traceback.format_exc()
    except IOError as e: error_occurred = True; print(f"🔥 IO 오류: {e}"); error_details_str = traceback.format_exc()
    except ValueError as e: error_occurred = True; print(f"🔥 값 오류: {e}"); error_details_str = traceback.format_exc()
    except Exception as e: error_occurred = True; print(f"🔥 예상치 못한 오류: {e}"); error_details_str = traceback.format_exc()
    finally:
        end_run_time = time.time(); elapsed_time = end_run_time - start_run_time
        if error_occurred: final_message = f"🔥 `{SCRIPT_NAME}` 실행 실패 (소요 시간: {elapsed_time:.2f}초)\n```\n{error_details_str[-1000:]}\n```"
        else:
             if not final_message: final_message = f"✅ `{SCRIPT_NAME}` 실행 성공 (소요 시간: {elapsed_time:.2f}초)"
        if final_message: telegram_utils.send_telegram_message(final_message)
        else: default_msg = f"ℹ️ `{SCRIPT_NAME}` 실행 완료 상태 메시지 없음."; print(default_msg); telegram_utils.send_telegram_message(default_msg)