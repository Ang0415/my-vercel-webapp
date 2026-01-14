# -*- coding: utf-8 -*-
# sheet_updater.py: 구글 시트 자동 업데이트 (날짜 추가, 금현물 가격/평가액, IRP 종가)
# (Version 2.2: Yahoo Finance 종가 조회 오류 수정 - .item() 사용)

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from datetime import datetime, date, timedelta
import time
import traceback # 오류 추적 정보 출력을 위해 임포트
import os
import sys
import re

# 공휴일 처리
try:
    import holidays
except ImportError:
    print("오류: 'holidays' 라이브러리가 설치되지 않았습니다. (pip install holidays)")
    print("-> 공휴일 제외 없이 주말만 확인합니다.")
    holidays = None

# Yahoo Finance 임포트
try:
    import yfinance as yf
except ImportError:
    print("오류: 'yfinance' 라이브러리가 설치되지 않았습니다. (pip install yfinance)")
    print("-> IRP 시트 종가 업데이트를 수행할 수 없습니다.")
    yf = None

# 텔레그램 유틸리티 임포트
try:
    import telegram_utils
except ModuleNotFoundError:
    print("⚠️ telegram_utils.py 모듈을 찾을 수 없습니다. 텔레그램 알림이 비활성화됩니다.")
    class MockTelegramUtils:
        def send_telegram_message(self, message):
            print("INFO: telegram_utils 모듈 없음 - 텔레그램 메시지 발송 건너뜀:", message[:100])
    telegram_utils = MockTelegramUtils()

# --- 설정 ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')

# 대상 시트 목록
RATE_SHEET_NAMES = ['📈ISA 수익률', '📈IRP 수익률', '📈연금 수익률', '📈금현물 수익률']
GOLD_RATE_SHEET = '📈금현물 수익률'
IRP_RATE_SHEET = '📈IRP 수익률' # IRP 시트 이름 명시
SETTINGS_SHEET = '⚙️설정'
TRADES_SHEET = '🗓️매매일지'

DATE_COLUMN_INDEX = 1  # A열
GOLD_VALUE_COLUMN_LETTER = 'E' # 금현물 수익률 시트의 평가액 컬럼 (E열)
GOLD_PRICE_COLUMN_LETTER = 'J' # 금현물 수익률 시트의 금 가격(per gram) 컬럼 (J열)
IRP_SP500_PRICE_COLUMN_LETTER = 'O' # IRP 수익률 시트의 S&P500 TR 종가 컬럼 (O열)
IRP_NASDAQ_PRICE_COLUMN_LETTER = 'P' # IRP 수익률 시트의 Nasdaq100 TR 종가 컬럼 (P열)

GOLD_PRICE_CELL = 'J9' # 설정 시트 금 가격 셀
GOLD_TRADE_CODE = 'GOLD' # 매매일지 시트에서 금현물을 식별할 코드 또는 이름

# IRP 시트에 업데이트할 종목 티커 (Yahoo Finance용)
IRP_TICKER_SP500 = '379810.KS'
IRP_TICKER_NASDAQ = '453850.KS'

SCRIPT_NAME = os.path.basename(__file__)
# --- ---

# --- 유틸리티 함수 ---
def clean_num_str(num_str, type_func=int):
    """숫자 문자열 정리 및 변환"""
    if isinstance(num_str, (int, float)): return num_str
    if not num_str: return type_func(0)
    try:
        cleaned_str = re.sub(r'[^\d.-]', '', str(num_str))
        if not cleaned_str or cleaned_str == '.': return type_func(0)
        is_negative = cleaned_str.startswith('-')
        numeric_part = cleaned_str.lstrip('-')
        if not numeric_part: return type_func(0)
        value = float(numeric_part)
        value_final = type_func(value)
        return -value_final if is_negative else value_final
    except (ValueError, TypeError):
        return type_func(0)

def connect_google_sheets():
    """구글 시트 연결 객체 반환"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if not os.path.exists(JSON_KEYFILE_PATH):
             raise FileNotFoundError(f"서비스 계정 키 파일을 찾을 수 없습니다: {JSON_KEYFILE_PATH}")
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        print("✅ Google Sheets API 인증 성공.")
        return gc
    except FileNotFoundError as e: print(f"❌ 오류: {e}"); return None
    except Exception as e: print(f"❌ 구글 시트 연결 오류: {e}"); traceback.print_exc(); return None

def is_market_open(check_date):
    """주어진 날짜가 대한민국 주식 시장 개장일인지 확인 (주말/공휴일 제외)"""
    if not isinstance(check_date, date):
        print(f"⚠️ is_market_open: 유효하지 않은 날짜 입력 ({check_date}). False 반환.")
        return False
    if check_date.weekday() >= 5: return False # 주말
    if holidays:
        try:
            kr_holidays = holidays.KR(years=check_date.year, observed=True)
            if check_date in kr_holidays: return False # 공휴일
        except Exception as e: print(f"⚠️ 공휴일 정보 확인 중 오류 발생: {e}.")
    return True # 개장일
# --- ---

# --- 시트 작업 함수 ---
def append_date_if_market_open(worksheet, target_date):
    """워크시트 A열에 개장일인 경우 오늘 날짜를 추가 (중복 제외)하고 행 번호 반환"""
    target_date_str = target_date.strftime('%Y-%m-%d')
    print(f"  > '{worksheet.title}' 시트 확인 (대상 날짜: {target_date_str})...")

    if not is_market_open(target_date):
        print(f"    - 정보: {target_date_str}은(는) 휴장일입니다. 날짜를 추가하지 않습니다.")
        return False, -1

    try:
        date_col_values = worksheet.col_values(DATE_COLUMN_INDEX)
        row_number = -1
        date_exists = False
        for i, val in enumerate(date_col_values):
            if val == target_date_str:
                row_number = i + 1
                date_exists = True
                break

        if date_exists:
            print(f"    - 정보: {target_date_str} 날짜가 이미 '{worksheet.title}' 시트 {row_number}행에 존재합니다.")
            return True, row_number

        next_row = len(date_col_values) + 1
        worksheet.update_acell(f'A{next_row}', target_date_str)
        print(f"    ✅ {target_date_str} 날짜를 '{worksheet.title}' 시트 A{next_row}에 추가했습니다.")
        return True, next_row
    except gspread.exceptions.APIError as e_api:
         print(f"    ❌ API 오류 ('{worksheet.title}' 시트 날짜 추가 중): {e_api}")
         return False, -1
    except Exception as e:
        print(f"    ❌ 오류 ('{worksheet.title}' 시트 날짜 추가 중): {e}")
        traceback.print_exc()
        return False, -1

def get_gold_price_from_settings(settings_ws):
    """설정 시트 J9 셀에서 금 가격을 읽어 숫자로 반환"""
    print(f"  > '{SETTINGS_SHEET}' 시트 {GOLD_PRICE_CELL} 셀에서 금 가격 읽기 시도...")
    try:
        price_str_raw = settings_ws.acell(GOLD_PRICE_CELL).value
        print(f"    - {GOLD_PRICE_CELL} 원본 값: '{price_str_raw}'")
        if not price_str_raw: print(f"    ⚠️ {GOLD_PRICE_CELL} 셀 값이 비어있습니다."); return 0.0
        if isinstance(price_str_raw, str) and price_str_raw.startswith('#'): print(f"    ⚠️ {GOLD_PRICE_CELL} 셀 값 오류: '{price_str_raw}'."); return 0.0
        price_float = clean_num_str(price_str_raw, float)
        if price_float > 0: print(f"    ✅ 금 1g당 가격 읽기 성공: {price_float:,.2f}"); return price_float
        else: print(f"    ⚠️ 금 가격을 유효한 숫자로 변환하지 못했습니다: {price_str_raw}"); return 0.0
    except gspread.exceptions.APIError as e_api: print(f"    ❌ API 오류 ('{SETTINGS_SHEET}' 시트 {GOLD_PRICE_CELL} 읽기 중): {e_api}"); return 0.0
    except Exception as e: print(f"    ❌ 오류 ('{SETTINGS_SHEET}' 시트 {GOLD_PRICE_CELL} 읽기 중): {e}"); traceback.print_exc(); return 0.0

def calculate_current_gold_quantity(trades_ws):
    """매매일지 시트에서 현재 보유 금 수량(g) 계산"""
    print(f"  > '{TRADES_SHEET}' 시트에서 금 보유 수량 계산 시도...")
    total_quantity = 0.0
    TRADE_CODE_HEADER = '종목코드'; TRADE_TYPE_HEADER = '매매구분'; TRADE_QTY_HEADER = '수량'
    try:
        all_trades_records = trades_ws.get_all_records()
        if not all_trades_records: print("    - 정보: 매매일지 데이터가 없습니다."); return 0.0
        trades_df = pd.DataFrame(all_trades_records)
        required_headers = [TRADE_CODE_HEADER, TRADE_TYPE_HEADER, TRADE_QTY_HEADER]
        if not all(h in trades_df.columns for h in required_headers): missing = [h for h in required_headers if h not in trades_df.columns]; print(f"    ❌ 오류: '{TRADES_SHEET}' 시트에 필수 헤더 누락: {missing}"); return 0.0
        gold_trades = trades_df[trades_df[TRADE_CODE_HEADER].astype(str).str.strip().str.upper() == GOLD_TRADE_CODE].copy()
        if gold_trades.empty: print(f"    - 정보: '{GOLD_TRADE_CODE}' 관련 거래 내역 없음."); return 0.0
        gold_trades['Quantity'] = gold_trades[TRADE_QTY_HEADER].apply(lambda x: clean_num_str(x, float))
        buy_qty = gold_trades.loc[gold_trades[TRADE_TYPE_HEADER] == '매수', 'Quantity'].sum()
        sell_qty = gold_trades.loc[gold_trades[TRADE_TYPE_HEADER] == '매도', 'Quantity'].sum()
        total_quantity = buy_qty - sell_qty
        print(f"    ✅ 금 보유 수량 계산 완료: {total_quantity:.2f} g (매수: {buy_qty:.2f}, 매도: {sell_qty:.2f})")
        return total_quantity
    except gspread.exceptions.APIError as e_api: print(f"    ❌ API 오류 ('{TRADES_SHEET}' 시트 읽기 중): {e_api}"); return 0.0
    except KeyError as e: print(f"    ❌ 오류: '{TRADES_SHEET}' 시트 처리 중 컬럼 '{e}' 없음."); return 0.0
    except Exception as e: print(f"    ❌ 오류 ('{TRADES_SHEET}' 시트 금 수량 계산 중): {e}"); traceback.print_exc(); return 0.0

def update_gold_sheet_columns(gold_ws, row_number, gold_price, gold_qty):
    """금현물 수익률 시트의 지정 행 E열(평가액)과 J열(금가격) 업데이트"""
    print(f"  > '{GOLD_RATE_SHEET}' 시트 업데이트 시도 (행: {row_number})...")
    update_success = True
    # E열: 총 평가액 업데이트
    if gold_price > 0 and gold_qty is not None:
        total_gold_value = gold_price * gold_qty
        print(f"    - 계산된 총 평가액: {total_gold_value:,.0f} 원 (가격: {gold_price:.2f}, 수량: {gold_qty:.2f})")
        try:
            gold_ws.update_acell(f'{GOLD_VALUE_COLUMN_LETTER}{row_number}', total_gold_value)
            print(f"    ✅ {row_number}행 {GOLD_VALUE_COLUMN_LETTER}열(평가액) 업데이트 완료.")
        except Exception as e:
            print(f"    ❌ 오류 ({GOLD_VALUE_COLUMN_LETTER}열 업데이트 중): {e}")
            traceback.print_exc(); update_success = False
    else:
        print(f"    - 정보: 유효한 금 가격({gold_price}) 또는 수량({gold_qty})이 없어 {GOLD_VALUE_COLUMN_LETTER}열 업데이트 불가.")
        update_success = False

    # J열: 금 1g당 가격 업데이트
    if gold_price > 0:
        try:
            gold_price_to_write = round(gold_price, 2)
            gold_ws.update_acell(f'{GOLD_PRICE_COLUMN_LETTER}{row_number}', gold_price_to_write)
            print(f"    ✅ {row_number}행 {GOLD_PRICE_COLUMN_LETTER}열(금가격) 업데이트 완료 ({gold_price_to_write}).")
        except Exception as e:
            print(f"    ❌ 오류 ({GOLD_PRICE_COLUMN_LETTER}열 업데이트 중): {e}")
            traceback.print_exc(); update_success = False
    else:
        print(f"    - 정보: 유효한 금 가격({gold_price})이 없어 {GOLD_PRICE_COLUMN_LETTER}열 업데이트 불가.")
        update_success = False

    return update_success

def get_yahoo_finance_closing_price(ticker, target_date):
    """Yahoo Finance에서 특정 티커의 target_date 종가 가져오기"""
    if not yf: return 0.0

    print(f"    > Yahoo Finance에서 {ticker} 종가 조회 (기준일: {target_date})...")
    try:
        start_dt = target_date - timedelta(days=3)
        end_dt = target_date + timedelta(days=1)
        data = yf.download(ticker, start=start_dt, end=end_dt, progress=False, auto_adjust=True)

        if data.empty:
            print(f"      - 정보: {ticker} 데이터 없음 ({start_dt} ~ {end_dt}).")
            return 0.0

        data = data.sort_index()
        filtered_data = data[data.index.date <= target_date]

        if filtered_data.empty:
             print(f"      - 정보: {ticker} 데이터 중 {target_date} 이전 데이터 없음.")
             return 0.0

        last_valid_close_series = filtered_data['Close'].dropna()
        if last_valid_close_series.empty:
            print(f"      - 정보: {ticker}의 {target_date} 이전 유효한 종가 없음.")
            return 0.0

        # --- 수정된 부분: .item() 사용 ---
        last_close_series_element = last_valid_close_series.iloc[-1]
        # 스칼라 값 추출
        last_close_scalar = last_close_series_element.item()
        # ------------------------------

        last_close_date = last_valid_close_series.index[-1].date()

        # 수정된 스칼라 값으로 출력 및 반환
        print(f"      ✅ {ticker} 종가 확인: {last_close_scalar:,.2f} ({last_close_date})")
        return float(last_close_scalar)

    except Exception as e:
        print(f"      ❌ 오류 (Yahoo Finance {ticker} 조회 중): {e}")
        import traceback
        traceback.print_exc() # 전체 스택 트레이스 출력
        return 0.0

def update_irp_stock_prices(irp_ws, row_number, sp500_price, nasdaq_price):
    """IRP 수익률 시트의 지정 행 O, P열에 종가 업데이트"""
    print(f"  > '{IRP_RATE_SHEET}' 시트 업데이트 시도 (행: {row_number})...")
    update_success = True
    try:
        sp500_to_write = float(sp500_price) if sp500_price is not None else 0.0
        irp_ws.update_acell(f'{IRP_SP500_PRICE_COLUMN_LETTER}{row_number}', sp500_to_write)
        print(f"    ✅ {row_number}행 {IRP_SP500_PRICE_COLUMN_LETTER}열(S&P500 TR) 업데이트 완료 ({sp500_to_write:.2f}).")
    except Exception as e:
        print(f"    ❌ 오류 ({IRP_SP500_PRICE_COLUMN_LETTER}열 업데이트 중): {e}")
        traceback.print_exc(); update_success = False

    try:
        nasdaq_to_write = float(nasdaq_price) if nasdaq_price is not None else 0.0
        irp_ws.update_acell(f'{IRP_NASDAQ_PRICE_COLUMN_LETTER}{row_number}', nasdaq_to_write)
        print(f"    ✅ {row_number}행 {IRP_NASDAQ_PRICE_COLUMN_LETTER}열(Nasdaq TR) 업데이트 완료 ({nasdaq_to_write:.2f}).")
    except Exception as e:
        print(f"    ❌ 오류 ({IRP_NASDAQ_PRICE_COLUMN_LETTER}열 업데이트 중): {e}")
        traceback.print_exc(); update_success = False

    return update_success

# --- 메인 실행 로직 ---
def main():
    print(f"🚀 구글 시트 자동 업데이트 시작 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    start_time = time.time()
    tasks_attempted = 0
    tasks_succeeded = 0

    gc = connect_google_sheets()
    if not gc: raise ConnectionError("🔥 구글 시트 연결 실패! 프로그램을 종료합니다.")

    try:
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        rate_worksheets = {name: spreadsheet.worksheet(name) for name in RATE_SHEET_NAMES}
        settings_ws = spreadsheet.worksheet(SETTINGS_SHEET)
        trades_ws = spreadsheet.worksheet(TRADES_SHEET)
        print(f"✅ 필요한 워크시트 ({', '.join(RATE_SHEET_NAMES)}, {SETTINGS_SHEET}, {TRADES_SHEET}) 열기 성공.")
    except gspread.exceptions.WorksheetNotFound as e: raise ValueError(f"🔥 필수 워크시트 '{e.args[0]}'를 찾을 수 없습니다!") from e
    except Exception as e: raise ConnectionError(f"🔥 워크시트 열기 중 오류 발생: {e}") from e

    today_date = datetime.now().date()
    is_today_open = is_market_open(today_date)
    target_row_numbers = {}

    if is_today_open:
        print(f"\n[날짜 추가] 오늘은 개장일({today_date.strftime('%Y-%m-%d')})입니다. 수익률 시트에 날짜 추가 시도...")
        for name, ws in rate_worksheets.items():
            success, row_num = append_date_if_market_open(ws, today_date)
            if success and row_num > 0:
                target_row_numbers[name] = row_num
            # 날짜 추가 실패는 후속 작업에서 처리됨 (target_row_numbers에 없으므로)
    else:
        print(f"\n[날짜 추가] 오늘은 휴장일({today_date.strftime('%Y-%m-%d')})입니다. 날짜 추가 작업을 건너<0xEB><0x81><0x91니다.")

    # 금 현물 처리
    print(f"\n[금 현물 처리] {today_date.strftime('%Y-%m-%d')} 기준 처리 시도...")
    tasks_attempted += 1 # 금 현물 처리는 항상 시도 (가격 읽기 포함)
    gold_price = get_gold_price_from_settings(settings_ws)
    if gold_price > 0 and is_today_open and GOLD_RATE_SHEET in target_row_numbers:
        gold_row_num = target_row_numbers[GOLD_RATE_SHEET]
        gold_quantity = calculate_current_gold_quantity(trades_ws)
        if update_gold_sheet_columns(rate_worksheets[GOLD_RATE_SHEET], gold_row_num, gold_price, gold_quantity):
            tasks_succeeded += 1
    else:
         if gold_price <= 0: print(f"  - 실패: 유효한 금 가격 읽지 못함.")
         elif not is_today_open: print(f"  - 정보: 휴장일이므로 업데이트 건너<0xEB><0x81><0x91.")
         elif GOLD_RATE_SHEET not in target_row_numbers: print(f"  - 실패: '{GOLD_RATE_SHEET}' 시트에 오늘 날짜 행 번호 없음.")

    # IRP 종가 처리
    print(f"\n[IRP 종가 처리] {today_date.strftime('%Y-%m-%d')} 기준 처리 시도...")
    if yf is None:
         print("  - 실패: yfinance 라이브러리 없음.")
         if is_today_open and IRP_RATE_SHEET in target_row_numbers: tasks_attempted += 1 # 시도는 한 것으로 간주
    elif is_today_open and IRP_RATE_SHEET in target_row_numbers:
        tasks_attempted += 1 # IRP 종가 업데이트 시도
        irp_row_num = target_row_numbers[IRP_RATE_SHEET]
        sp500_close = get_yahoo_finance_closing_price(IRP_TICKER_SP500, today_date)
        time.sleep(0.5)
        nasdaq_close = get_yahoo_finance_closing_price(IRP_TICKER_NASDAQ, today_date)
        time.sleep(0.5)

        if sp500_close > 0 and nasdaq_close > 0:
            if update_irp_stock_prices(rate_worksheets[IRP_RATE_SHEET], irp_row_num, sp500_close, nasdaq_close):
                 tasks_succeeded += 1
        else:
             print(f"  - 실패: S&P500({sp500_close:.2f}) 또는 Nasdaq({nasdaq_close:.2f}) 종가 조회 실패.")
    else:
         if not is_today_open: print(f"  - 정보: 휴장일이므로 업데이트 건너<0xEB><0x81><0x91.")
         elif IRP_RATE_SHEET not in target_row_numbers: print(f"  - 실패: '{IRP_RATE_SHEET}' 시트에 오늘 날짜 행 번호 없음.")


    # 최종 결과 요약
    elapsed_time = time.time() - start_time
    fail_count = tasks_attempted - tasks_succeeded
    result_summary = f"총 {tasks_attempted}개 작업 시도, 성공: {tasks_succeeded}건, 실패: {fail_count}건"
    final_message = f"✅ `{SCRIPT_NAME}` 실행 완료 ({result_summary}, 소요 시간: {elapsed_time:.2f}초)"
    if fail_count > 0:
        final_message = f"⚠️ `{SCRIPT_NAME}` 실행 완료 (일부 실패 포함: {result_summary}, 소요 시간: {elapsed_time:.2f}초)"

    print(f"\n🏁 구글 시트 자동 업데이트 완료 ({elapsed_time:.2f}초)")
    print(f"   - {result_summary}")
    return final_message, fail_count > 0

# --- 스크립트 실행 및 텔레그램 알림 ---
if __name__ == '__main__':
    run_start_time = time.time()
    final_status_message = ""
    error_details = ""
    main_failed = False

    try:
        if yf is None:
            print("🔥 필수 라이브러리 'yfinance'가 설치되지 않았습니다. 일부 기능이 제한됩니다.")
        final_status_message, main_failed = main()
    except ConnectionError as e:
        main_failed = True; error_details = traceback.format_exc()
        final_status_message = f"🔥 `{SCRIPT_NAME}` 실행 실패: 구글 시트 연결 오류 (시작 불가)"
    except ValueError as e:
        main_failed = True; error_details = traceback.format_exc()
        final_status_message = f"🔥 `{SCRIPT_NAME}` 실행 실패: 설정 또는 데이터 오류 (시작 불가)"
    except Exception as e:
        main_failed = True; error_details = traceback.format_exc()
        final_status_message = f"🔥 `{SCRIPT_NAME}` 실행 실패: 예상치 못한 오류 발생"
    finally:
        run_elapsed_time = time.time() - run_start_time
        if main_failed and not final_status_message.startswith("🔥"):
            final_status_message = f"🔥 `{SCRIPT_NAME}` 실행 실패 (소요 시간: {run_elapsed_time:.2f}초)\n```\n{error_details[-1000:]}\n```"
        elif main_failed and final_status_message.startswith("🔥"):
             final_status_message += f" (소요 시간: {run_elapsed_time:.2f}초)\n```\n{error_details[-1000:]}\n```"
        elif not final_status_message:
             final_status_message = f"✅ `{SCRIPT_NAME}` 실행 완료 (소요 시간: {run_elapsed_time:.2f}초)"

        if final_status_message:
            telegram_utils.send_telegram_message(final_status_message)
        else:
            default_msg = f"ℹ️ `{SCRIPT_NAME}` 실행 완료되었으나 최종 상태 메시지 없음."
            print(default_msg)
            telegram_utils.send_telegram_message(default_msg)

        print(f"\n스크립트 총 실행 시간: {run_elapsed_time:.2f}초")