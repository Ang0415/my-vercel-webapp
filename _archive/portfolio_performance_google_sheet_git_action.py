# portfolio_performance_google_sheet_git_action.py
# (GitHub Actions용) TWR 및 단순손익 계산 및 기록 스크립트
# 환경변수 'GCS_CREDENTIALS' 사용

import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from datetime import datetime
import traceback
import sys
import time
import json

# --- Git Action Utils Module ---
try:
    import telegram_utils_git_action as telegram_utils
except ModuleNotFoundError:
    try:
        import telegram_utils
    except ModuleNotFoundError:
        class MockTelegramUtils:
            def send_telegram_message(self, message):
                print("INFO: 텔레그램 메시지 발송 (Mock):", message[:100])
        telegram_utils = MockTelegramUtils()

# --- 상수 정의 ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
TWR_RAW_SHEET = '성과_TWR_Raw'
GAIN_LOSS_RAW_SHEET = '성과_손익_Raw'
TWR_HEADER = ['날짜', '계좌명', 'TWR']
GAIN_LOSS_HEADER = ['날짜', '계좌명', '단순손익']

ACCOUNT_SHEETS = {
    'ISA': '📈ISA 수익률',
    'IRP': '📈IRP 수익률',
    '연금': '📈연금 수익률',
    '금현물': '📈금현물 수익률'
}
DIVIDEND_SHEET_NAME = '🗓️배당일지'
DATE_COL_IDX = 0; DEPOSIT_COL_IDX = 1; WITHDRAWAL_COL_IDX = 2; VALUE_COL_IDX = 4
DIV_DATE_IDX = 0; DIV_AMOUNT_IDX = 5; DIV_ACCOUNT_IDX = 6
SCRIPT_NAME = os.path.basename(__file__)

# --- 유틸리티 함수 ---
def connect_google_sheets():
    """Google Sheets connection using Environment Variable 'GCS_CREDENTIALS'"""
    try:
        gcs_json_str = os.environ.get('GCS_CREDENTIALS')
        if not gcs_json_str:
            print("❌ 환경변수 'GCS_CREDENTIALS'가 없습니다. (로컬 폴백 시도)")
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
        print("✅ Google Sheets API 인증 성공 (GitAction).")
        return gc
    except Exception as e:
        print(f"❌ 구글 시트 연결 오류: {e}")
        return None

def setup_worksheet(spreadsheet, worksheet_name, header_columns):
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"⚠️ 워크시트 '{worksheet_name}' 생성 중...")
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows="1000", cols=len(header_columns))
        worksheet.append_row(header_columns, value_input_option='USER_ENTERED')
    return worksheet

def clean_numeric_column(series, default=0.0):
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float).fillna(default)
    series_str = series.astype(str).str.replace(',', '', regex=False).str.strip()
    series_str = series_str.replace('', '0')
    return pd.to_numeric(series_str, errors='coerce').fillna(default).astype(float)


def safe_execute_with_retry(func, *args, retries=5, sleep=10, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "Quota exceeded" in str(e) or "429" in str(e):
                wait_time = sleep * (2 ** i) # Exponential backoff
                print(f"⚠️ Quota exceeded (429). Retrying in {wait_time}s... ({i+1}/{retries})")
                time.sleep(wait_time)
            else:
                 if i == retries - 1: raise e
                 print(f"⚠️ API Error: {e}. Retrying...")
                 time.sleep(1)
    return None

def read_and_aggregate_data(gc, sheet_names, date_col_idx, deposit_col_idx, withdrawal_col_idx, value_col_idx, start_date=None):
    if not gc: return None, None
    
    # Safe Open
    spreadsheet = safe_execute_with_retry(gc.open, GOOGLE_SHEET_NAME)
    
    all_data_list = []
    sheet_dfs = {}
    
    for sheet_name in sheet_names:
        try:
            time.sleep(1) # Rate limiting
            # Safe Worksheet
            worksheet = safe_execute_with_retry(spreadsheet.worksheet, sheet_name)
            # Safe Get Values
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
    valid = (df['StartValue'] > 0) & (denom.abs() > 1e-9)
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
    try:
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        twr_ws = setup_worksheet(spreadsheet, TWR_RAW_SHEET, TWR_HEADER)
        gl_ws = setup_worksheet(spreadsheet, GAIN_LOSS_RAW_SHEET, GAIN_LOSS_HEADER)
        
        # 1. TWR 데이터 리스트화
        all_twr_rows = []
        for acc, df in twr_results.items():
            if df is not None and not df.empty:
                for date, row in df.iterrows():
                    all_twr_rows.append([date.strftime('%Y-%m-%d'), acc, round(float(row['TWR']), 2)])
        
        # 2. 단순손익 데이터 리스트화 (이제 Series 형태임)
        all_gl_rows = []
        for acc, series in gain_loss_results.items():
            if series is not None and not series.empty:
                for date, val in series.items():
                    all_gl_rows.append([date.strftime('%Y-%m-%d'), acc, int(val)])

        # 3. 시트 기록 로직
        existing_twr = twr_ws.get_all_values()
        target_date_str = last_common_date.strftime('%Y-%m-%d')
        
        # 시트가 비어있으면 전체 기록
        if len(existing_twr) <= 1:
            if all_twr_rows: twr_ws.append_rows(all_twr_rows, value_input_option='USER_ENTERED')
            if all_gl_rows: gl_ws.append_rows(all_gl_rows, value_input_option='USER_ENTERED')
            print(f"✅ 과거 데이터 전체 기록 완료 (TWR {len(all_twr_rows)}건, 손익 {len(all_gl_rows)}건)")
        else:
            # 기존 데이터가 있으면 마지막 날짜 중복 체크 후 추가
            is_duplicate = any(len(row) > 0 and row[0] == target_date_str for row in existing_twr)
            if not is_duplicate:
                last_day_twr = [row for row in all_twr_rows if row[0] == target_date_str]
                last_day_gl = [row for row in all_gl_rows if row[0] == target_date_str]
                if last_day_twr: twr_ws.append_rows(last_day_twr, value_input_option='USER_ENTERED')
                if last_day_gl: gl_ws.append_rows(last_day_gl, value_input_option='USER_ENTERED')
                print(f"✅ 최신 날짜({target_date_str}) 데이터 기록 완료")
            else:
                print(f"ℹ️ {target_date_str} 데이터가 이미 존재합니다.")
        return True
    except Exception as e:
        print(f"❌ 시트 저장 중 오류: {e}"); traceback.print_exc(); return False

# --- 메인 실행 함수 ---
def main():
    gc = connect_google_sheets()
    if not gc: return False
    
    # [과거 데이터 채우기용 설정] 
    test_start_date = None # 자동화 모드에서는 None 권장 (또는 필요시 설정)
    
    twr_results = {}; gain_loss_results = {}
    div_df = load_and_process_dividends(gc)
    
    # 1. 전체 포트폴리오
    all_names = list(ACCOUNT_SHEETS.values())
    total_data, last_date = read_and_aggregate_data(gc, all_names, DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX, start_date=test_start_date)
    
    if total_data is not None:
        if div_df is not None:
            total_div = div_df.groupby('Date')['Amount'].sum()
            total_data = total_data.join(total_div.rename('Div'), how='left').fillna(0)
            total_data['Value'] += total_data['Div']
        
        twr_results['Total'] = calculate_twr(total_data)
        
        # --- 단순손익 일별 누적 계산 추가 ---
        total_data['CumNetFlow'] = total_data['NetCashFlow'].cumsum()
        initial_val = total_data['Value'].iloc[0]
        initial_flow = total_data['NetCashFlow'].iloc[0]
        # 누적손익 = 현재평가액 - (시작평가액 + 투입금합계 - 시작일투입금)
        total_data['Profit'] = total_data['Value'] - (initial_val + total_data['CumNetFlow'] - initial_flow)
        gain_loss_results['Total'] = total_data['Profit']

    # 2. 개별 계좌
    for acc, s_name in ACCOUNT_SHEETS.items():
        acc_data, _ = read_and_aggregate_data(gc, [s_name], DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX, start_date=test_start_date)
        if acc_data is not None:
            if div_df is not None:
                acc_div = div_df[div_df['Account'] == acc].set_index('Date')['Amount']
                acc_data = acc_data.join(acc_div.rename('Div'), how='left').fillna(0)
                acc_data['Value'] += acc_data['Div']
            
            twr_results[acc] = calculate_twr(acc_data)
            
            # --- 개별 계좌 일별 누적 손익 계산 추가 ---
            acc_data['CumNetFlow'] = acc_data['NetCashFlow'].cumsum()
            i_val = acc_data['Value'].iloc[0]
            i_flow = acc_data['NetCashFlow'].iloc[0]
            acc_data['Profit'] = acc_data['Value'] - (i_val + acc_data['CumNetFlow'] - i_flow)
            gain_loss_results[acc] = acc_data['Profit']

    if last_date:
        return save_results_to_sheets(gc, twr_results, gain_loss_results, last_date)
    return False

if __name__ == '__main__':
    start_time = time.time()
    try:
        if main(): 
            msg = f"✅ `portfolio_performance_git_action.py` 실행 완료 ({time.time()-start_time:.1f}s)"
            print(msg)
            telegram_utils.send_telegram_message(msg)
        else: 
            msg = f"⚠️ `portfolio_performance_git_action.py` 실행 실패/데이터 없음"
            print(msg)
            telegram_utils.send_telegram_message(msg)
    except Exception as e: 
        err_msg = f"🔥 오류:\n{traceback.format_exc()[:500]}"
        print(err_msg)
        telegram_utils.send_telegram_message(err_msg)
