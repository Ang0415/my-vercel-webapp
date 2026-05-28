# portfolio_performance.py (TWR 및 단순손익 과거 데이터 전체 기록 버전)
import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from datetime import datetime
import traceback
import sys
import time
import sys
sys.stdout.reconfigure(encoding='utf-8')
from datetime import datetime, timedelta
try:
    import holidays
    kr_holidays = holidays.KR()
except ImportError:
    kr_holidays = {}

# --- 텔레그램 유틸리티 임포트 ---
try:
    import telegram_utils
except ModuleNotFoundError:
    class MockTelegramUtils:
        def send_telegram_message(self, message):
            print("INFO: 텔레그램 메시지 발송:", message[:100])
    telegram_utils = MockTelegramUtils()

# --- 상수 정의 ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')

TWR_RAW_SHEET = '성과_TWR_Raw'
GAIN_LOSS_RAW_SHEET = '성과_손익_Raw'
DAILY_ASSET_SPREADSHEET_NAME = '성과_자산추이_Raw'
DAILY_ASSET_SHEET_NAME = '성과_자산추이_Raw'
TWR_HEADER = ['날짜', '계좌명', 'TWR']
GAIN_LOSS_HEADER = ['날짜', '계좌명', '단순손익']

ACCOUNT_SHEETS = {
    'ISA': '📈ISA 수익률',
    'IRP': '📈IRP 수익률',
    '연금': '📈연금 수익률',
    '금현물': '📈금현물 수익률'
}
TRADES_SHEET_NAME = '🗓️매매일지'
QUANTITY_RAW_FILE_NAME = '수량_RAW' # 별도 파일
QUANTITY_SHEET_NAME = '수량'

DIVIDEND_SHEET_NAME = '🗓️배당일지'
DATE_COL_IDX = 0; DEPOSIT_COL_IDX = 1; WITHDRAWAL_COL_IDX = 2; VALUE_COL_IDX = 4
DIV_DATE_IDX = 0; DIV_AMOUNT_IDX = 5; DIV_ACCOUNT_IDX = 6
SCRIPT_NAME = os.path.basename(__file__)

# --- 유틸리티 함수 ---
def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        print("✅ Google Sheets API 인증 성공.")
        return gc
    except Exception as e:
        print(f"❌ 구글 시트 연결 오류: {e}")
        return None

def setup_worksheet(spreadsheet, worksheet_name, header_columns):
    try:
        worksheet = safe_execute_with_retry(spreadsheet.worksheet, worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"⚠️ 워크시트 '{worksheet_name}' 생성 중...")
        worksheet = safe_execute_with_retry(spreadsheet.add_worksheet, title=worksheet_name, rows="1000", cols=len(header_columns))
        safe_execute_with_retry(worksheet.append_row, header_columns, value_input_option='USER_ENTERED')
    return worksheet

def clean_numeric_column(series, default=0.0):
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float).fillna(default)
    series_str = series.astype(str).str.replace(',', '', regex=False).str.strip()
    series_str = series_str.replace('', '0')
    return pd.to_numeric(series_str, errors='coerce').fillna(default).astype(float)

def safe_execute_with_retry(func, *args, retries=5, sleep=5, **kwargs):
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

def read_and_aggregate_data(gc, sheet_names, date_col_idx, deposit_col_idx, withdrawal_col_idx, value_col_idx, start_date=None):
    if not gc: return None, None
    spreadsheet = safe_execute_with_retry(gc.open, GOOGLE_SHEET_NAME)
    all_data_list = []
    sheet_dfs = {}
    
    for sheet_name in sheet_names:
        try:
            time.sleep(1)
            worksheet = safe_execute_with_retry(spreadsheet.worksheet, sheet_name)
            data = safe_execute_with_retry(worksheet.get_all_values)
            if len(data) < 2: continue
            df_raw = pd.DataFrame(data[1:], columns=data[0])
            df_cleaned = pd.DataFrame({
                'Date': pd.to_datetime(df_raw.iloc[:, date_col_idx], errors='coerce'),
                'Deposit': clean_numeric_column(df_raw.iloc[:, deposit_col_idx]),
                'Withdrawal': clean_numeric_column(df_raw.iloc[:, withdrawal_col_idx]),
                'Value': clean_numeric_column(df_raw.iloc[:, value_col_idx])
            }).dropna(subset=['Date']).drop_duplicates('Date').set_index('Date')
            
            if start_date:
                df_cleaned = df_cleaned[df_cleaned.index >= pd.to_datetime(start_date)]
            all_data_list.append(df_cleaned)
            sheet_dfs[sheet_name] = df_cleaned
        except Exception as e: print(f"⚠️ {sheet_name} 로딩 실패: {e}")

    if not all_data_list: return None, None
    combined = pd.concat(all_data_list).groupby(level=0).sum()
    combined['NetCashFlow'] = combined['Deposit'] - combined['Withdrawal']
    
    max_dates = [df[df['Value'] > 0].index.max() for df in sheet_dfs.values() if not df[df['Value'] > 0].empty]
    last_common_date = min(max_dates) if max_dates else combined.index.max()
    combined = combined[combined.index <= last_common_date]
    return combined[['Value', 'NetCashFlow']], last_common_date

def calculate_twr(df):
    if df is None or len(df) < 2: return None
    df = df.copy().sort_index()
    df['StartValue'] = df['Value'].shift(1)
    df = df.iloc[1:].copy()
    denom = df['StartValue'] + df['NetCashFlow']
    df['DailyFactor'] = 1.0
    # 보정: StartValue가 0이더라도 NetCashFlow가 존재하면 valid로 판정하여 첫 입금일의 수익률을 계산에 반영
    valid = ((df['StartValue'] > 0) | (df['NetCashFlow'] > 0)) & (denom.abs() > 1e-9)
    df.loc[valid, 'DailyFactor'] = df.loc[valid, 'Value'] / denom.loc[valid]
    df['TWR'] = (df['DailyFactor'].cumprod() - 1) * 100
    return df[['TWR']]

def load_and_process_dividends(gc):
    try:
        ws = gc.open(GOOGLE_SHEET_NAME).worksheet(DIVIDEND_SHEET_NAME)
        data = ws.get_all_values()
        if len(data) < 2: return None
        df = pd.DataFrame(data[1:], columns=data[0])
        df['Date'] = pd.to_datetime(df.iloc[:, DIV_DATE_IDX], errors='coerce')
        df['Amount'] = clean_numeric_column(df.iloc[:, DIV_AMOUNT_IDX])
        df['Account'] = df.iloc[:, DIV_ACCOUNT_IDX].str.strip()
        return df.dropna(subset=['Date', 'Account']).groupby(['Date', 'Account'])['Amount'].sum().reset_index()
    except: return None

# --- 결과 저장 로직 (모든 날짜의 손익까지 저장하도록 수정) ---
def save_results_to_sheets(gc, twr_results, gain_loss_results, last_common_date):
    """
    TWR 결과는 '성과_TWR_Raw' 파일의 각 계좌별 시트에(덮어쓰기), 
    단순손익은 'KYI_자산배분' 파일의 '성과_손익_Raw' 시트에(덮어쓰기) 저장합니다.
    """
    try:
        TWR_SPREADSHEET_NAME = '성과_TWR_Raw'
        
        # 1. TWR 데이터 저장 (계좌별 시트 분리)
        print(f"💾 TWR 데이터를 '{TWR_SPREADSHEET_NAME}' 파일에 저장 중...")
        for acc_name, df in twr_results.items():
            time.sleep(2) # API Rate Limit 방지
            if df is not None and not df.empty:
                # 저장할 데이터프레임 구성
                save_df = df.copy()
                save_df['Account'] = acc_name
                # Date는 인덱스에 있거나 컬럼에 있을 수 있음. calculate_twr 리턴값은 ['TWR'] 컬럼만 있고 인덱스가 Date일 가능성 높음
                if 'Date' not in save_df.columns:
                    save_df = save_df.reset_index()
                
                # 컬럼 순서 및 이름 정리 (날짜, 계좌명, TWR)
                if 'Date' in save_df.columns:
                    save_df = save_df.rename(columns={'Date': '날짜', 'Account': '계좌명'})
                elif 'index' in save_df.columns:
                    save_df = save_df.rename(columns={'index': '날짜', 'Account': '계좌명'})
                
                # TWR 반올림
                if 'TWR' in save_df.columns:
                     save_df['TWR'] = save_df['TWR'].apply(lambda x: round(x, 2))

                required_cols = ['날짜', '계좌명', 'TWR']
                if all(col in save_df.columns for col in required_cols):
                    final_df = save_df[required_cols]
                    # write_to_google_sheet 함수 재사용 (overwrite)
                    if write_to_google_sheet(gc, acc_name, final_df, spreadsheet_name=TWR_SPREADSHEET_NAME):
                        print(f"  - '{acc_name}' 시트 저장 완료.")
                    else:
                        print(f"  - ⚠️ '{acc_name}' 시트 저장 실패.")

        # 2. 단순손익 데이터 저장 (별도 파일 '성과_손익_Raw', 계좌별 시트 분리)
        GAIN_LOSS_SPREADSHEET_NAME = '성과_손익_Raw'
        print(f"💾 단순 손익 데이터를 '{GAIN_LOSS_SPREADSHEET_NAME}' 파일에 저장 중... (계좌별 시트)")
        
        for acc_name, series in gain_loss_results.items():
            if series is not None and not series.empty:
                # series index: Date, value: Profit
                temp_df = series.reset_index()
                # 컬럼명 설정: 날짜, 단순손익
                # (TWR과 통일성을 위해 '날짜', '계좌명', '단순손익' 등 포맷 유지하되, 시트가 분리되므로 계좌명 컬럼은 선택사항이나 유지)
                temp_df.columns = ['날짜', '단순손익']
                temp_df['계좌명'] = acc_name
                
                # 날짜 문자열 변환
                temp_df['날짜'] = temp_df['날짜'].dt.strftime('%Y-%m-%d')
                temp_df['단순손익'] = temp_df['단순손익'].fillna(0).astype(int)
                
                save_df = temp_df[['날짜', '계좌명', '단순손익']]
                
                # 저장 (덮어쓰기)
                if write_to_google_sheet(gc, acc_name, save_df, spreadsheet_name=GAIN_LOSS_SPREADSHEET_NAME):
                    print(f"  - '{acc_name}' 시트 저장 완료.")
                else:
                    print(f"  - ⚠️ '{acc_name}' 시트 저장 실패.")

        # [NEW] 일별 비중 데이터 기록 (통합됨)
        try:
            record_daily_weights(gc)
        except Exception as e:
             print(f"⚠️ 일별 비중 기록 중 오류 발생: {e}")

        return True

    except Exception as e:
        print(f"❌ 시트 저장 중 오류: {e}")
        traceback.print_exc()
        return False

def check_holiday_data(gc):
    """
    접근 가능한 raw 파일들에 휴장일(주말/공휴일) 데이터가 있는지 검사합니다.
    대상: 성과_TWR_Raw, 성과_자산추이_Raw, 성과_손익_Raw, 일별비중_RAW, 수량_RAW, 종가_RAW, KYI_자산배분
    """
    print("\n🕵️ [데이터 점검] 휴장일 데이터 포함 여부 검사 시작...")
    
    CHECK_LIST = [
        {'file': '성과_TWR_Raw', 'sheets': 'ALL', 'date_col': 0},
        {'file': '성과_자산추이_Raw', 'sheets': 'ALL', 'date_col': 0},
        {'file': '성과_손익_Raw', 'sheets': 'ALL', 'date_col': 0},
        {'file': '일별비중_RAW', 'sheets': ['일별비중_Raw'], 'date_col': 0},
        {'file': '수량_RAW', 'sheets': ['수량'], 'date_col': 0},
        {'file': '종가_RAW', 'sheets': ['종가관리'], 'date_col': 0},
    ]

    try:
        import holidays
        kr_holidays = holidays.KR()
    except:
        kr_holidays = {}

    found_issues = False
    
    for item in CHECK_LIST:
        file_name = item['file']
        target_sheets = item['sheets']
        date_col_idx = item['date_col']
        
        # Rate Limit 방지
        time.sleep(2)

        try:
            # 파일 열기 (Retry 적용)
            try:
                sh = safe_execute_with_retry(gc.open, file_name)
            except gspread.exceptions.SpreadsheetNotFound:
                print(f"  - (Skip) '{file_name}' 파일을 찾을 수 없습니다.")
                continue
            except Exception as e:
                print(f"  - (Error) '{file_name}' 열기 실패: {e}")
                continue

            # 검사할 시트 목록 결정
            if target_sheets == 'ALL':
                worksheets = sh.worksheets()
            else:
                worksheets = []
                for s_name in target_sheets:
                    try: 
                        worksheets.append(safe_execute_with_retry(sh.worksheet, s_name))
                    except: pass
            
            for ws in worksheets:
                if not ws: continue
                try:
                    # 데이터 가져오기 (A열만 가져오면 빠름)
                    # col_values는 1-based index
                    dates = safe_execute_with_retry(ws.col_values, date_col_idx + 1)
                    if not dates or len(dates) < 2: continue

                    
                    # 헤더 제외하고 검사
                    for r_idx, date_str in enumerate(dates[1:], start=2):
                        if not date_str: continue
                        try:
                            # 날짜 파싱 (YYYY-MM-DD)
                            dt = pd.to_datetime(date_str, format='%Y-%m-%d', errors='coerce')
                            if pd.isna(dt): continue
                            
                            is_weekend = dt.weekday() >= 5 # 5:Sat, 6:Sun
                            is_holiday = dt.strftime('%Y-%m-%d') in kr_holidays
                            
                            if is_weekend or is_holiday:
                                found_issues = True
                                reason = "주말" if is_weekend else "공휴일"
                                print(f"  ⚠️ 발견: [{file_name}] > '{ws.title}' 시트 : {date_str} ({reason})")
                                
                        except: pass
                        
                except Exception as e:
                    print(f"  - '{ws.title}' 검사 중 오류: {e}")

        except Exception as e:
            print(f"  - '{file_name}' 접근 실패: {e}")

    if not found_issues:
        print("✅ 휴장일 데이터가 발견되지 않았습니다. (정상)")
    else:
        print("⚠️ 위 날짜들은 휴장일(주말/공휴일)입니다. 확인이 필요합니다.")

def write_to_google_sheet(gc, sheet_name, df, spreadsheet_name=GOOGLE_SHEET_NAME):
    """데이터프레임을 구글 시트에 기록합니다. (덮어쓰기)"""
    if not gc: return False
    try:
        # Retry 로직 적용
        spreadsheet = safe_execute_with_retry(gc.open, spreadsheet_name)
        try:
            worksheet = safe_execute_with_retry(spreadsheet.worksheet, sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"  - 시트 '{sheet_name}' 생성 중... (파일: {spreadsheet_name})")
            worksheet = safe_execute_with_retry(spreadsheet.add_worksheet, title=sheet_name, rows=len(df)+50, cols=len(df.columns))
        
        # 데이터 준비 (Date 컬럼 문자열 변환 등)
        df_copy = df.copy()
        if 'Date' in df_copy.columns:
            df_copy['Date'] = pd.to_datetime(df_copy['Date']).dt.strftime('%Y-%m-%d')
        if '날짜' in df_copy.columns:
            df_copy['날짜'] = pd.to_datetime(df_copy['날짜']).dt.strftime('%Y-%m-%d')
        
        # 리스트로 변환
        values = [df_copy.columns.values.tolist()] + df_copy.values.tolist()
        
        # 시트 클리어 및 업데이트
        safe_execute_with_retry(worksheet.clear)
        safe_execute_with_retry(worksheet.update, range_name='A1', values=values)
        return True
    except Exception as e:
        print(f"❌ 구글 시트 업로드 실패 ('{sheet_name}' at '{spreadsheet_name}'): {e}")
        return False
    
def remove_holiday_data(gc):
    """
    모든 데이터 파일에서 휴장일(주말/공휴일) 데이터를 찾아 자동으로 삭제합니다.
    (덮어쓰기 방식으로 삭제)
    """
    print("\n🧹 [청소] 휴장일 데이터 자동 삭제 시작...")
    
    try:
        import holidays
        kr_holidays = holidays.KR()
    except:
        kr_holidays = {}
        print("  ⚠️ holidays 라이브러리 로드 실패. 주말만 제거합니다.")

    # 점검 및 삭제 대상 목록
    # 점검 및 삭제 대상 목록 (최적화: 원본 소스 파일만 점검)
    # 파생 결과 파일(TWR, 자산추이 등)은 스크립트 실행 시 어차피 덮어씌워지므로 점검 제외 (API Quota 절약)
    TARGETS = [
        # 1. 원본 데이터 (가장 중요)
        {'file': '수량_RAW', 'sheets': ['수량'], 'date_col': 0},
        
        # 2. 결과 시트들 in KYI_자산배분 (이 값들은 읽혀서 파생 데이터에 쓰이므로 청소 필요)
        {'file': GOOGLE_SHEET_NAME, 'sheets': ['📈ISA 수익률', '📈IRP 수익률', '📈연금 수익률', '📈금현물 수익률'], 'date_col': 0},
        
        # 3. 일별 비중 (수동 기록 등을 고려하여 유지)
        {'file': '일별비중_RAW', 'sheets': ['일별비중_Raw'], 'date_col': 0},
    ]

    count_deleted_files = 0
    
    for item in TARGETS:
        file_name = item['file']
        target_sheets = item['sheets']
        date_col_idx = item['date_col']
        
        try:
            # 파일 열기
            try:
                sh = safe_execute_with_retry(gc.open, file_name)
            except gspread.exceptions.SpreadsheetNotFound:
                continue
                
            # 시트 목록 확보
            if target_sheets == 'ALL':
                worksheets = sh.worksheets()
            else:
                worksheets = []
                for s_name in target_sheets:
                    try: 
                        worksheets.append(safe_execute_with_retry(sh.worksheet, s_name))
                    except: pass
            
            for ws in worksheets:
                if not ws: continue
                time.sleep(2) # Rate Limit

                try:
                    # 전체 데이터 읽기
                    all_values = safe_execute_with_retry(ws.get_all_values)
                    if not all_values or len(all_values) < 2: continue
                    
                    header = all_values[0]
                    rows = all_values[1:]
                    
                    new_rows = []
                    deleted_count = 0
                    
                    for row in rows:
                        if len(row) <= date_col_idx: 
                            new_rows.append(row) # 데이터 불완전하면 유지
                            continue
                            
                        date_str = str(row[date_col_idx]).strip()
                        if not date_str: 
                            new_rows.append(row)
                            continue
                            
                        # 날짜 파싱
                        try:
                            dt = pd.to_datetime(date_str, format='%Y-%m-%d', errors='coerce')
                            if pd.isna(dt):
                                # 날짜 형식이 아니면(예: 빈칸, 이상한 값) 유지할지 삭제할지? 
                                # 일단 유지 (안전을 위해)
                                new_rows.append(row)
                                continue
                                
                            is_weekend = dt.weekday() >= 5
                            is_holiday = (dt.strftime('%Y-%m-%d') in kr_holidays)
                            
                            if is_weekend or is_holiday:
                                deleted_count += 1
                                # Skip adding this row (Delete)
                            else:
                                new_rows.append(row)
                                
                        except:
                            new_rows.append(row)
                    
                    if deleted_count > 0:
                        # 업데이트 수행
                        print(f"  Start Cleaning '{file_name}' > '{ws.title}' ... ({deleted_count} rows found)")
                        safe_execute_with_retry(ws.clear)
                        
                        # 데이터 복원
                        update_data = [header] + new_rows
                        safe_execute_with_retry(ws.update, range_name='A1', values=update_data, value_input_option='USER_ENTERED')
                        print(f"  ✅ 삭제 완료: {deleted_count}건 제거됨.")
                        count_deleted_files += 1
                    
                except Exception as e_ws:
                    print(f"  ⚠️ 시트 처리 오류({ws.title}): {e_ws}")
                    
        except Exception as e_file:
            print(f"  ❌ 파일 접근 오류({file_name}): {e_file}")
            
    print("🧹 휴장일 데이터 정리 완료.\n")
     
# --- 메인 실행 함수 ---
# TWR 시트 계좌명 vs 일별잔고 시트 계좌명 매핑 (사용 안 함)
ACCOUNT_MAPPING = {} 




def calculate_and_update_account_values(gc):
    """
    수량_RAW, 종가_RAW, 자산배분 시트를 읽어서
    최근 2일치 기준 전 계좌(ISA, IRP, 연금, 금현물) 평가액을 계산하고
    각 수익률 시트(📈... 수익률)의 E열(평가금액)을 업데이트합니다.
    """
    
    print("\n🚀 [전계좌] 평가액 계산 및 업데이트 시작 (최근 2일)")
    
    # 1. 수량_RAW 로드
    QTY_FILE = '수량_RAW'
    QTY_SHEET = '수량'
    
    try:
        qty_ws = safe_execute_with_retry(gc.open(QTY_FILE).worksheet, QTY_SHEET)
        qty_data = safe_execute_with_retry(qty_ws.get_all_values)
        if not qty_data or len(qty_data) < 2:
            print("❌ 수량 데이터 부족")
            return
            
        qty_header = qty_data[0]
        # 최근 2일치만 필터링
        rows_to_process = qty_data[1:]
        if len(rows_to_process) > 2:
            rows_to_process = rows_to_process[-2:]
            
        print(f"ℹ️ 처리 대상 날짜: {[r[0] for r in rows_to_process]}")
        
    except Exception as e:
        print(f"❌ 수량 로딩 실패: {e}")
        return

    # 2. 종가_RAW 로드
    PRICE_FILE = '종가_RAW'
    PRICE_SHEET = '종가관리'
    
    try:
        price_ws = safe_execute_with_retry(gc.open(PRICE_FILE).worksheet, PRICE_SHEET)
        price_data = safe_execute_with_retry(price_ws.get_all_values)
        if not price_data or len(price_data) < 3:
            print("❌ 종가 데이터 부족")
            return
            
        # 헤더 감지 (Row 1 or 2)
        price_header_row = price_data[0]
        if len(price_data) > 1:
            row_1_str = " ".join([str(x) for x in price_data[1]])
            if 'KRX:' in row_1_str or 'Code' in row_1_str:
                price_header_row = price_data[1]
                print("ℹ️ 종가 데이터 헤더로 2행(종목코드) 사용")
        
        price_headers = price_header_row
        
        # 날짜별 가격 매핑
        price_map = {}
        for row in price_data[2:]:
            if not row or not row[0].strip(): continue
            d = row[0].strip()
            p_dict = {}
            for idx, val in enumerate(row):
                if idx == 0: continue
                if idx < len(price_headers):
                    key = price_headers[idx].strip()
                    try: p_dict[key] = float(str(val).replace(',',''))
                    except: p_dict[key] = 0.0
            price_map[d] = p_dict
            
    except Exception as e:
        print(f"❌ 종가 로딩 실패: {e}")
        return

    # 3. 자산배분 & 설정 로드 (매핑용)
    name_to_code = {}
    ticker_to_account = {} # Name -> Account Key (ISA, IRP, 연금, 금현물)
    
    try:
        # 3.1 설정 (Name -> Code)
        spreadsheet = safe_execute_with_retry(gc.open, GOOGLE_SHEET_NAME)
        setting_ws = safe_execute_with_retry(spreadsheet.worksheet, '⚙️설정')
        setting_data = safe_execute_with_retry(setting_ws.get_all_values)
        if setting_data:
            for row in setting_data[1:]:
                if len(row) > 17:
                    s_name = row[16].strip()
                    s_code = row[17].strip()
                    if s_name and s_code: name_to_code[s_name] = s_code
        
        # 3.2 자산배분 (Name -> Account)
        alloc_ws = safe_execute_with_retry(spreadsheet.worksheet, '자산배분')
        alloc_data = safe_execute_with_retry(alloc_ws.get_all_values)
        
        if alloc_data:
            # Header check
            h = alloc_data[0]
            # Column L (Index 11) is Account typically
            acc_idx = 11
            name_idx = 0
            
            for row in alloc_data[1:]:
                if len(row) > max(acc_idx, name_idx):
                    t_name = row[name_idx].strip()
                    acc_raw = row[acc_idx].strip()
                    
                    if not t_name or not acc_raw: continue
                    
                    # 매핑 규칙
                    target_key = None
                    if 'ISA' in acc_raw: target_key = 'ISA'
                    elif 'IRP' in acc_raw: target_key = 'IRP'
                    elif '연금' in acc_raw: target_key = '연금'
                    # 금현물은 별도 처리 logic 필요할 수 있으나, 일단 자산배분에 있으면 처리
                    
                    if target_key:
                        ticker_to_account[t_name] = target_key
        
        # 금현물 하드코딩 (자산배분에 없을 경우 대비)
        ticker_to_account['금현물'] = '금현물'
        name_to_code['금현물'] = '금현물' # Price Key Matching용
                        
    except Exception as e:
        print(f"❌ 매핑 데이터 로딩 실패: {e}")
        return

    # 4. 루프 실행
    # 사용할 타겟 시트들 미리 로드
    target_sheets = {} # {'ISA': ws, 'IRP': ws, ...}
    target_dates_cache = {} # {'ISA': [dates...], ...}
    
    for acc_key, sheet_name in ACCOUNT_SHEETS.items():
        try:
             ws = safe_execute_with_retry(spreadsheet.worksheet, sheet_name)
             target_sheets[acc_key] = ws
             target_dates_cache[acc_key] = safe_execute_with_retry(ws.col_values, 1)
        except:
             print(f"⚠️ {sheet_name} 시트 접근 불가")

    for q_row in rows_to_process:
        target_date_str = q_row[0]
        if not target_date_str: continue
        
        day_prices = price_map.get(target_date_str)
        if not day_prices:
             # print(f"⚠️ {target_date_str} 가격 데이터 없음")
             pass
        
        # 계좌별 합계 초기화
        account_totals = {k: 0 for k in ACCOUNT_SHEETS.keys()}
        
        # 보유 수량 파싱 및 계산
        for idx, val in enumerate(q_row):
            if idx == 0: continue
            if idx >= len(qty_header): break
            
            s_name = qty_header[idx]
            try: qty = float(str(val).replace(',',''))
            except: qty = 0
            
            if qty <= 0: continue
            
            # 계좌 확인
            if s_name in ticker_to_account:
                acc_key = ticker_to_account[s_name]
            else:
                # Fallback: 이름에 포함되어 있는지?
                if '금현물' in s_name: acc_key = '금현물'
                else: acc_key = '기타' # Skip
            
            if acc_key not in account_totals: continue
            
            # 가격 찾기
            price = 0
            if day_prices:
                code = name_to_code.get(s_name)
                if code:
                    price = day_prices.get(code)
                    if price is None:
                        clean = code.replace('KRX:','').strip()
                        price = day_prices.get(clean)
                        if price is None:
                            # Suffix check
                            for pk, pv in day_prices.items():
                                if clean in pk:
                                    price = pv; break
                else:
                    # Code 매핑 없으면 Name으로 직접 시도 (금현물 등)
                    price = day_prices.get(s_name)
            
            if price:
                account_totals[acc_key] += qty * price
                
        # 시트 업데이트
        for acc_key, total_val in account_totals.items():
            if total_val <= 0: continue
            if acc_key not in target_sheets: continue
            
            ws = target_sheets[acc_key]
            dates = target_dates_cache[acc_key]
            
            try:
                if target_date_str in dates:
                    # Update
                    r_idx = dates.index(target_date_str) + 1
                    safe_execute_with_retry(ws.update_cell, r_idx, 5, total_val)
                    print(f"✅ [{acc_key}] {target_date_str} 업데이트: {total_val:,.0f}")
                else:
                    # Find the first empty row in Column A to write the date and value
                    r_idx = len(dates) + 1
                    safe_execute_with_retry(ws.update_cell, r_idx, 1, target_date_str)
                    safe_execute_with_retry(ws.update_cell, r_idx, 5, total_val)
                    dates.append(target_date_str) # Cache update
                    print(f"✅ [{acc_key}] {target_date_str} 추가 (Row {r_idx}): {total_val:,.0f}")
            except Exception as e:
                print(f"❌ {acc_key} 업데이트 실패: {e}") 


    return

def save_daily_asset_trend(gc, account_dfs):
    """
    각 계좌별 평가액(Value) 추이를 '성과_자산추이_Raw' 파일에 저장합니다.
    또한 모든 계좌 합산(Total) 추이도 저장합니다.
    """
    try:
        SPREADSHEET_NAME = DAILY_ASSET_SPREADSHEET_NAME
        print(f"\n📤 데일리 자산 추이 업로드 중... (파일: '{SPREADSHEET_NAME}')")
        
        # 1. Total 합산
        total_df = None
        if account_dfs:
            # pd.concat automatically aligns index (Date)
            valid_dfs = [df for df in account_dfs.values() if df is not None and not df.empty]
            if valid_dfs:
                total_df = pd.concat(valid_dfs).groupby(level=0).sum()
        
        # 2. Total 업로드
        if total_df is not None and not total_df.empty:
            d_df = total_df[['Value']].copy().reset_index()
            # Date 컬럼명 통일
            if 'index' in d_df.columns: d_df = d_df.rename(columns={'index':'Date'})
            
            print(f"  - 'Total' 시트 업로드...")
            write_to_google_sheet(gc, 'Total', d_df, spreadsheet_name=SPREADSHEET_NAME)

        # 3. 개별 계좌 업로드
        for acc_k, acc_d in account_dfs.items():
             if acc_d is not None and not acc_d.empty:
                 d_df = acc_d[['Value']].copy().reset_index()
                 if 'index' in d_df.columns: d_df = d_df.rename(columns={'index':'Date'})
                 
                 print(f"  - '{acc_k}' 시트 업로드...")
                 write_to_google_sheet(gc, acc_k, d_df, spreadsheet_name=SPREADSHEET_NAME)
        return True
    
    except Exception as e:
        print(f"❌ 자산 추이 저장 실패: {e}")
        return False

def calculate_simple_profit(df, dividend_df, account_name):
    """
    단순 손익(평가액 - 원금)을 계산합니다.
    선택적으로 배당금(dividend_df)을 포함하여 평가액을 보정할 수 있습니다.
    """
    if df is None or df.empty: return None
    df = df.copy()
    
    # 1. 배당금 반영 (Value에 합산)
    if dividend_df is not None and not dividend_df.empty:
        # 'Account' 컬럼으로 필터링
        if 'Account' in dividend_df.columns:
            target_div = dividend_df[dividend_df['Account'] == account_name]
        else:
            target_div = pd.DataFrame() # 없으면 빈 DF
            
        if not target_div.empty:
             # 날짜 인덱스 설정
             target_div = target_div.set_index('Date')
             # Series로 변환 (중복 날짜 합산 등은 load_dividend에서 처리됨)
             div_series = target_div['Amount']
             
             # 원본 df에 조인
             df = df.join(div_series.rename('Div'), how='left')
             df['Div'] = df['Div'].fillna(0)
             
             # 평가액에 배당 추가 (배당을 수취하여 자산에 포함된 것으로 간주)
             df['Value'] += df['Div']
    
    # 2. 누적 순입금(원금 변화) 계산
    if 'NetCashFlow' not in df.columns:
        df['NetCashFlow'] = 0
        
    df['CumNetFlow'] = df['NetCashFlow'].cumsum()
    
    # 3. 손익 계산
    # Profit = Value - (Initial_Value + Added_Capital)
    # Added_Capital includes Initial Flow if it was part of Initial Value?
    # 보통 첫 행의 Value가 시작점이고, 첫 행의 Flow가 그 시작점을 만든 입금이라면:
    # Invested = Initial_Value + (CumNetFlow - Initial_Flow) ??
    # Case 1: Day 1 Deposit 100, Value 100.
    # CumFlow = 100.
    # Formula: 100 - (100 + 100 - 100) = 0. Correct.
    # Case 2: Day 2 Value 110. (Profit 10).
    # CumFlow = 100.
    # Formula: 110 - (100 + 100 - 100) = 10. Correct.
    # Case 3: Day 2 Deposit 50. Value 150.
    # CumFlow = 150.
    # Formula: 150 - (100 + 150 - 100) = 0. Correct.
    
    if len(df) > 0:
        i_val = df['Value'].iloc[0]
        i_flow = df['NetCashFlow'].iloc[0]
        df['Profit'] = df['Value'] - (i_val + df['CumNetFlow'] - i_flow)
        return df['Profit']
    else:
        return None

def update_daily_quantities(gc):
    """
    매매일지(🗓️매매일지)를 읽어서 수량_RAW(별도 파일)의 '수량' 시트를 업데이트합니다.
    1. 오늘 날짜가 없으면 추가합니다.
    2. 모든 날짜에 대해 매매일지를 처음부터 다시 계산(Replay)하여 누적 수량을 정확히 맞춥니다.
    """
    print("\n📦 [수량] 일별 보유 수량 업데이트 시작 (매매일지 기반)")
    try:
        # 1. 매매일지 로드
        spreadsheet = safe_execute_with_retry(gc.open, GOOGLE_SHEET_NAME)
        ws_trades = safe_execute_with_retry(spreadsheet.worksheet, TRADES_SHEET_NAME)
        trade_data = safe_execute_with_retry(ws_trades.get_all_values)
        if len(trade_data) < 2:
            print("  ⚠️ 매매일지 데이터가 없습니다.")
            return

        # Header: 날짜(0), 종목명(1), 매매구분(3), 수량(5)
        trades_df = pd.DataFrame(trade_data[1:], columns=trade_data[0])
        trades_df['날짜'] = pd.to_datetime(trades_df['날짜'], errors='coerce')
        trades_df['수량'] = pd.to_numeric(trades_df['수량'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        trades_df = trades_df.dropna(subset=['날짜']).sort_values('날짜')
        
        # 2. 타겟 시트(수량_RAW) 로드
        try:
            q_sh = safe_execute_with_retry(gc.open, QUANTITY_RAW_FILE_NAME)
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"❌ 수량 파일('{QUANTITY_RAW_FILE_NAME}')을 찾을 수 없습니다.")
            return

        # Find sheet
        ws_q = None
        worksheets = safe_execute_with_retry(q_sh.worksheets)
        for s in worksheets:
            if QUANTITY_SHEET_NAME in s.title: ws_q = s; break
        if not ws_q: ws_q = q_sh.sheet1
        
        q_data = safe_execute_with_retry(ws_q.get_all_values)
        if not q_data:
            print("❌ 수량 시트 데이터가 비어있습니다.")
            return

        headers = q_data[0]
        target_stocks = headers[1:]
        
        # 날짜 확인 및 오늘 날짜 추가
        dates = [row[0] for row in q_data[1:]]
        
        # 휴장일 체크 로직 (오늘이 주말/휴일이면 직전 평일로)
        today = datetime.now().date()
        while today.weekday() >= 5 or today in kr_holidays:
            today -= timedelta(days=1)
        today_str = today.strftime('%Y-%m-%d')
        
        if today_str not in dates:
            print(f"  ➕ 오늘 날짜({today_str}) 행 추가 중...")
            new_row = [today_str] + [0]*len(target_stocks)
            safe_execute_with_retry(ws_q.append_row, new_row, value_input_option='USER_ENTERED')
            dates.append(today_str)
            
        # 3. 재계산 (Replay)
        date_row_map = {}
        for i, d_str in enumerate(dates):
            date_row_map[d_str] = i + 2 # Header is row 1
            
        sorted_dates = sorted(dates)
        
        current_holdings = {stock: 0.0 for stock in target_stocks}
        trades_q = trades_df.copy()
        trade_idx = 0
        num_trades = len(trades_q)
        
        updates = []
        
        for d_str in sorted_dates:
            curr_dt = pd.to_datetime(d_str, errors='coerce')
            if pd.isna(curr_dt): continue
            
            # Apply trades <= curr_dt
            while trade_idx < num_trades:
                row = trades_q.iloc[trade_idx]
                t_dt = row['날짜']
                if t_dt > curr_dt: break
                
                t_name = str(row['종목명']).strip().replace(' ', '')
                t_type = row['매매구분']
                t_qty = row['수량']
                
                # Match Header
                matched_header = None
                for h in target_stocks:
                    h_clean = h.strip().replace(' ', '')
                    if t_name == h_clean:
                        matched_header = h; break
                    if t_name in h_clean or h_clean in t_name: # Fuzzy
                        matched_header = h
                        if t_name == h_clean: break
                
                if matched_header:
                    if '매수' in t_type: current_holdings[matched_header] += t_qty
                    elif '매도' in t_type:
                        current_holdings[matched_header] -= t_qty
                        if current_holdings[matched_header] < 0: current_holdings[matched_header] = 0
                
                trade_idx += 1
                
            row_vals = [current_holdings.get(h, 0) for h in target_stocks]
            r_idx = date_row_map[d_str]
            updates.append({'r_idx': r_idx, 'vals': row_vals})

        # 4. Batch Update
        is_sorted = all(dates[i] <= dates[i+1] for i in range(len(dates)-1) if dates[i].strip() and dates[i+1].strip())
        
        if is_sorted:
            # 원본 행 구조를 유지한 matrix 초기화 (빈 날짜 등으로 인한 행 쏠림/밀림 방지)
            matrix = [row[1:1+len(target_stocks)] for row in q_data[1:]]
            for up in updates:
                r_idx = up['r_idx']
                # r_idx는 1-based 시트 행 번호이므로, matrix 인덱스는 r_idx - 2
                if 0 <= r_idx - 2 < len(matrix):
                    matrix[r_idx - 2] = up['vals']
            
            start_row = 2
            end_row = start_row + len(matrix) - 1
            col_count = len(target_stocks)
            
            def get_col_letter(col_idx_1based):
                res = ""
                while col_idx_1based > 0:
                    col_idx_1based, remainder = divmod(col_idx_1based - 1, 26)
                    res = chr(65 + remainder) + res
                return res
            
            end_col_letter = get_col_letter(1 + col_count)
            range_str = f"B{start_row}:{end_col_letter}{end_row}"
            
            try:
                ws_q.update(range_name=range_str, values=matrix, value_input_option='USER_ENTERED')
                print(f"  ✅ {len(matrix)}일치 수량 데이터 업데이트 완료 (밀림 방지 구조 적용)")
            except Exception as e:
                print(f"  ❌ 일괄 업데이트 실패: {e}")
                
        else:
             print("  ⚠️ 시트 날짜가 정렬되지 않아 업데이트를 건너뜁니다 (정렬 권장).")
                 
    except Exception as e:
        print(f"❌ 수량 업데이트 실패: {e}")
        traceback.print_exc()

# --- 메인 실행 함수 ---
def main():
    gc = connect_google_sheets()
    if not gc: return False
    
    # [NEW] 0. 휴장일 데이터 자동 삭제 (가장 먼저 실행)
    remove_holiday_data(gc)

    # 1. 수량 데이터 업데이트 (매매일지 기반, 선행 작업)
    update_daily_quantities(gc)

    # 2. 일별 평가액 계산 및 업데이트 (ISA, IRP, 연금, 금현물)
    calculate_and_update_account_values(gc)

    # 2. 데이터 읽기 & TWR, 단순손익 계산
    # (주의: 1번 단계에서 시트가 업데이트 되어야 최신 평가액이 반영됨)
    # ISA는 '📈ISA 수익률', IRP는 '📈IRP 수익률' 시트를 읽음.
    isa_sheets = [ACCOUNT_SHEETS['ISA']]
    irp_sheets = [ACCOUNT_SHEETS['IRP']]
    pension_sheets = [ACCOUNT_SHEETS['연금']]
    gold_sheets = [ACCOUNT_SHEETS['금현물']] # 금현물 추가
    
    dividend_df = load_and_process_dividends(gc)
    
    # 4. 각 계좌별 성과 계산
    # 4. 각 계좌별 성과 계산
    # read_and_aggregate_data returns (DataFrame, last_common_date)
    isa_df, _ = read_and_aggregate_data(gc, isa_sheets, DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX)
    irp_df, _ = read_and_aggregate_data(gc, irp_sheets, DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX)
    pension_df, _ = read_and_aggregate_data(gc, pension_sheets, DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX)
    gold_df, _ = read_and_aggregate_data(gc, gold_sheets, DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX)

    isa_twr = None; isa_gl = None
    if isa_df is not None:
         isa_twr = calculate_twr(isa_df)
         isa_gl = calculate_simple_profit(isa_df, dividend_df, 'ISA')
    
    irp_twr = None; irp_gl = None
    if irp_df is not None:
         irp_twr = calculate_twr(irp_df)
         irp_gl = calculate_simple_profit(irp_df, dividend_df, 'IRP')

    pension_twr = None; pension_gl = None
    if pension_df is not None:
         pension_twr = calculate_twr(pension_df)
         pension_gl = calculate_simple_profit(pension_df, dividend_df, '연금')
         
    gold_twr = None; gold_gl = None
    if gold_df is not None:
         gold_twr = calculate_twr(gold_df)
         gold_gl = calculate_simple_profit(gold_df, dividend_df, '금현물')

    # [NEW] 데일리 자산 추이 저장 (누락된 로직 복구)
    account_dfs = {
        'ISA': isa_df,
        'IRP': irp_df,
        '연금': pension_df,
        '금현물': gold_df
    }
    save_daily_asset_trend(gc, account_dfs)
    
    # [NEW] 전체 합산 TWR 및 손익 계산
    total_twr = None; total_gl = None
    if account_dfs:
        valid_dfs = [df for df in account_dfs.values() if df is not None and not df.empty]
        if valid_dfs:
            # 1. 날짜별 합산 (Value, NetCashFlow 등 모든 수치 컬럼 합산)
            # 인덱스가 Date여야 함. read_and_aggregate_data에서 Index=Date로 리턴됨.
            total_df = pd.concat(valid_dfs).groupby(level=0).sum()
            
            # 보정: 각 계좌별 데이터의 공통된 마지막 날짜(last_common_date)까지만 슬라이싱하여 
            # 특정 계좌(예: 금현물)만 다음날 데이터가 먼저 반영되어 전체 합산 평가액이 급락하는 현상 방지
            last_common_date = min([df.index.max() for df in valid_dfs if not df.empty])
            total_df = total_df[total_df.index <= last_common_date]
            
            # 2. TWR 및 손익 계산
            if not total_df.empty:
                total_twr = calculate_twr(total_df)
                total_gl = calculate_simple_profit(total_df, dividend_df, 'Total') # 배당은 Account='Total'로 매핑된게 없으므로 개별 배당 합산 필요하지만, calculate_simple_profit 내에서 필터링함. Total용 배당 처리는 복잡하므로 일단 Skip or Sum? 
                # 배당 데이터(dividend_df)에 'Total' 계좌는 없음. 
                # 정확한 Total Profit을 위해서는 개별 계좌 배당을 모두 합친 가상의 Total 배당이 필요할 수 있음.
                # 하지만 일단 배당 제외하고 (Value - Principal)로 계산해도 됨 (Value에 배당재투자가 반영되어 있다면).
                
    # 5. 결과 저장 준비 (TWR, Profit)
    twr_results = {
        'Total': total_twr,
        'ISA': isa_twr,
        'IRP': irp_twr,
        '연금': pension_twr,
        '금현물': gold_twr
    }
    gain_loss_results = {
        'Total': total_gl,
        'ISA': isa_gl,
        'IRP': irp_gl,
        '연금': pension_gl,
        '금현물': gold_gl
    }
    
    # 마지막 계산 날짜 (전체 중 Max)
    # 각 df의 index.max() 중 가장 최신
    last_calc_date = None
    all_indices = []
    for df in [isa_twr, irp_twr, pension_twr, gold_twr]:
        if df is not None and not df.empty:
            all_indices.append(df.index.max())
    
    if all_indices:
        last_calc_date = max(all_indices)
    else:
        last_calc_date = datetime.now() # Fallback

    # 5. 결과 저장
    save_results_to_sheets(gc, twr_results, gain_loss_results, last_calc_date)
    
    # 6. 일별 비중 기록 (자산배분 -> 일별비중_Raw)
    record_daily_weights(gc)
    
    # 7. 휴일 데이터 검사 (마지막에 리포트)
    check_holiday_data(gc)
    
    print("\n🎉 모든 작업 완료.")
    return True


def record_daily_weights(gc):
    """
    KYI_자산배분 > 자산배분 시트를 읽어서, 
    일별비중_Raw 파일 > 일별비중_Raw 시트에 오늘 날짜 기준 비중 데이터를 기록합니다.
    (기존 daily_batch.py의 로직을 이식)
    """
    
    print("\n🚀 [비중 기록] 일별 비중 데이터 기록 시작 (by Portfolio Script)")
    
    WEIGHTS_FILE_NAME = '일별비중_RAW'
    WEIGHTS_SHEET_NAME = '일별비중_Raw'
    WEIGHTS_HEADER = ['날짜', '계좌명', '종목코드', '종목명', '자산구분', '국적', '평가금액', '포트폴리오내비중(%)']
    
    try:
        # 0. 날짜 결정 (오늘 -> 휴장일이면 직전 영업일)
        # update_closing_prices.py 로직 차용
        target_date = datetime.now().date()
        while target_date.weekday() >= 5 or target_date.strftime('%Y-%m-%d') in kr_holidays:
            print(f"  🛑 {target_date}은 휴장일입니다. 하루 전으로 이동합니다.")
            target_date -= timedelta(days=1)
            
        target_date_str = target_date.strftime("%Y-%m-%d")
        
        # 1. 소스 시트 로드 (KYI_자산배분 > 자산배분, 설정)
        spreadsheet = safe_execute_with_retry(gc.open, GOOGLE_SHEET_NAME)
        alloc_ws = safe_execute_with_retry(spreadsheet.worksheet, '자산배분')
        settings_ws = safe_execute_with_retry(spreadsheet.worksheet, '⚙️설정')
        
        alloc_data = safe_execute_with_retry(alloc_ws.get_all_values)
        settings_data = safe_execute_with_retry(settings_ws.get_all_values)
        
        if not alloc_data or len(alloc_data) < 2:
            print("❌ 자산배분 시트 데이터를 읽을 수 없습니다.")
            return

        # 2. 종목코드 매핑 로드
        name_to_code_map = {}
        if settings_data and len(settings_data) > 1:
            for row in settings_data[1:]:
                if len(row) >= 18:
                    s_name = str(row[16]).strip() # Q열
                    s_code = str(row[17]).strip() # R열
                    if s_name and s_code:
                        name_to_code_map[s_name] = s_code
                        
        # 3. 자산배분 데이터 파싱
        all_holdings_data = []
        
        for row in alloc_data[1:]: # 헤더 제외
            # A열(0): 종목명, B열(1): 자산구분, C열(2): 비중(%), I열(8): 평가금액, L열(11): 계좌명
            if len(row) > 11:
                row_name = str(row[0]).strip()
                row_type_raw = str(row[1]).strip()
                row_weight_str = str(row[2]).strip()
                row_val_str = str(row[8]).strip()
                row_acc = str(row[11]).strip()
                
                if not row_acc: continue
                
                # 값 클리닝
                try: val = float(row_val_str.replace(',',''))
                except: val = 0
                
                try: weight_raw = float(row_weight_str.replace('%', '').replace(',', ''))
                except: weight_raw = 0.0
                
                if val <= 0: continue
                
                # 종목코드 찾기
                code = name_to_code_map.get(row_name, "")
                if not code:
                    if '현금' in row_name: code = 'CASH'
                    elif '달러' in row_name: code = 'USD'
                    elif row_name in ['예수금(원화)', '예수금(외화)']: code = 'CASH'
                    elif '금현물' in row_name or row_name == '금': code = 'GOLD'
                    else: code = "N/A"
                
                # 자산구분/국적 파싱
                nation = "기타"; asset_class = "기타"
                tokens = row_type_raw.split()
                if tokens:
                    if tokens[0] in ['미국', '한국', '중국', '인도', '베트남', '일본', '영국', '독일', '프랑스', '선진국', '신흥국']:
                        nation = tokens[0]
                        asset_class = " ".join(tokens[1:]) if len(tokens) > 1 else "기타"
                    else:
                        if '현금' in row_type_raw: nation = '한국'; asset_class = '현금'
                        elif '금' in row_type_raw: nation = '기타'; asset_class = '대체투자'
                        else: nation = '기타'; asset_class = row_type_raw
                
                all_holdings_data.append([
                    target_date_str,
                    row_acc,
                    code,
                    row_name,
                    asset_class,
                    nation,
                    int(val),
                    round(weight_raw, 2)
                ])
                
        if not all_holdings_data:
            print("⚠️ 저장할 비중 데이터가 없습니다.")
            return

        # 4. 대상 파일(일별비중_Raw)에 기록
        try:
             w_spreadsheet = safe_execute_with_retry(gc.open, WEIGHTS_FILE_NAME)
        except gspread.exceptions.SpreadsheetNotFound:
             print(f"❌ '{WEIGHTS_FILE_NAME}' 파일을 찾을 수 없습니다.")
             try:
                 import json
                 with open(JSON_KEYFILE_PATH, 'r', encoding='utf-8') as f:
                     key_data = json.load(f)
                     client_email = key_data.get('client_email', '알 수 없음')
                 print(f"👉 팁: '{WEIGHTS_FILE_NAME}' 파일을 구글 드라이브에서 생성한 뒤, 다음 이메일로 '편집자' 권한을 공유해주세요:")
                 print(f"📧 서비스 계정 이메일: {client_email}")
             except:
                 print("👉 팁: 구글 드라이브에서 해당 파일을 서비스 계정 이메일로 공유했는지 확인해주세요.")
             
             # [DEBUG] 접근 가능한 파일 목록 출력
             try:
                 print("\n🔎 [디버그] 현재 서비스 계정이 접근 가능한 스프레드시트 목록:")
                 file_list = [s.title for s in safe_execute_with_retry(gc.openall)]
                 for title in file_list:
                     print(f"  - {title}")
                     if '일별비중' in title:
                         print(f"    ✨ (유사한 이름 발견! 정확한 파일명은 '{title}' 입니다. 코드의 '{WEIGHTS_FILE_NAME}'과 일치하는지 확인하세요.)")
             except Exception as e_list:
                 print(f"  (목록 조회 실패: {e_list})")
                 
             return

        try:
            # [MODIFIED] 시트 이름 매칭 강화 (공백/대소문자 이슈 방지)
            found_ws = None
            all_worksheets = w_spreadsheet.worksheets()
            for ws in all_worksheets:
                # 1. 정확히 일치
                if ws.title == WEIGHTS_SHEET_NAME:
                    found_ws = ws; break
                # 2. 공백 제거 후 일치
                if ws.title.strip() == WEIGHTS_SHEET_NAME:
                    found_ws = ws; break
                # 3. 대소문자 무시하고 일치 (Raw vs RAW)
                if ws.title.strip().lower() == WEIGHTS_SHEET_NAME.lower():
                    found_ws = ws; break
            
            if found_ws:
                weights_ws = found_ws
                print(f"✅ 기존 시트 확인: '{weights_ws.title}' (설정된 이름: {WEIGHTS_SHEET_NAME})")
            else:
                raise gspread.exceptions.WorksheetNotFound(WEIGHTS_SHEET_NAME)

        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️ '{WEIGHTS_SHEET_NAME}' 시트가 없어 생성 중...")
            weights_ws = safe_execute_with_retry(w_spreadsheet.add_worksheet, title=WEIGHTS_SHEET_NAME, rows="1000", cols=len(WEIGHTS_HEADER))
            safe_execute_with_retry(weights_ws.append_row, WEIGHTS_HEADER, value_input_option='USER_ENTERED')
            
        # 기존 데이터 로드 (덮어쓰기 위해)
        all_weights = safe_execute_with_retry(weights_ws.get_all_values)
        if not all_weights: all_weights = [WEIGHTS_HEADER]
        
        header = all_weights[0]
        rows = all_weights[1:]
        
        # 오늘 날짜 제외 필터링
        filtered_rows = [r for r in rows if str(r[0]).strip() != target_date_str]
        
        # 병합
        final_rows = [header] + filtered_rows + all_holdings_data
        
        # 업데이트
        safe_execute_with_retry(weights_ws.clear)
        safe_execute_with_retry(weights_ws.update, range_name='A1', values=final_rows, value_input_option='USER_ENTERED')
        
        print(f"✅ 일별 비중 데이터 업데이트 완료 ({len(all_holdings_data)}건 기록)")

    except Exception as e:
        print(f"❌ 일별 비중 기록 실패: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    start_time = time.time()
    try:
        if main(): 
            # [NEW] 비중 기록 실행 (main 성공 시)
            pass
            
            # [NEW] 휴장일 데이터 점검
            gc = connect_google_sheets()
            if gc:
                check_holiday_data(gc)
            # gc가 main 내부에 있어서 여기서 다시 연결하거나 main이 gc를 리턴하게 변경해야 함.
            # 하지만 구조상 main 안에서 호출하는게 깔끔.
            # main함수 수정하여 내부에서 호출하도록 변경했으므로 여기서는 메시지만 출력.
            print(f"✅ 성공 ({time.time()-start_time:.1f}s)")
        else: print(f"⚠️ 실패")
    except Exception as e: print(f"🔥 오류:\n{traceback.format_exc()[:500]}")
