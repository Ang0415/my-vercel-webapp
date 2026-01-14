# portfolio_utils.py
import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import sys
import time
import traceback
from datetime import datetime, timedelta

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

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

def safe_execute_with_retry(func, *args, retries=10, sleep=5, **kwargs):
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
