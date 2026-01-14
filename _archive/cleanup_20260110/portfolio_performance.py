# portfolio_performance.py (최종 버전: 배당반영 TWR, 단순손익, 그래프 팝업, 결과 파일 저장, 단순 알림)
# (Version 5.1: Total TWR 그래프 3일 이동평균선 제거, 평가액>0 마지막 날짜 기준 유지)

import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from datetime import datetime, timedelta
import traceback
import warnings
import sys
import time
import json

# --- 시각화 라이브러리 임포트 및 폰트 설정 ---
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    if os.name == 'nt': plt.rcParams['font.family'] = 'Malgun Gothic'
    elif os.name == 'posix':
        try: plt.rcParams['font.family'] = 'AppleGothic'
        except: print("AppleGothic 폰트 없음. 시스템 기본 또는 다른 지정 폰트 사용.")
    plt.rcParams['axes.unicode_minus'] = False
except ImportError:
    print("오류: 'matplotlib' 라이브러리가 필요합니다. (pip install matplotlib)")
    plt = None
# --- ---

# --- 텔레그램 유틸리티 임포트 ---
try:
    import telegram_utils
except ModuleNotFoundError:
    print("⚠️ telegram_utils.py 모듈을 찾을 수 없습니다. 텔레그램 알림이 비활성화됩니다.")
    class MockTelegramUtils:
        def send_telegram_message(self, message):
            print("INFO: telegram_utils 모듈 없음 - 텔레그램 메시지 발송 건너<0xEB><0x81><0x91:", message[:100])
    telegram_utils = MockTelegramUtils()
# --- ---

# --- 상수 정의 ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')
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
TWR_CSV_PATH = os.path.join(CURRENT_DIR, 'twr_results.csv')
GAIN_LOSS_JSON_PATH = os.path.join(CURRENT_DIR, 'gain_loss.json')
DAILY_ASSET_SPREADSHEET_NAME = '성과_자산추이_Raw' # New Spreadsheet File Name
DAILY_ASSET_SHEET_NAME = '성과_자산추이_Raw' # Tab Name (Same as file name used by user request)
# --- ---

# --- 유틸리티 함수 ---
def connect_google_sheets():
    """구글 시트에 연결하고 인증된 클라이언트 객체를 반환합니다."""
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

def clean_numeric_column(series, default=0.0):
    """쉼표 제거 등 숫자 컬럼을 정리하고 float 타입으로 변환합니다."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float).fillna(default)
    series_str = series.astype(str).str.replace(',', '', regex=False).str.strip()
    series_str.replace('', '0', inplace=True)
    series_num = pd.to_numeric(series_str, errors='coerce')
    return series_num.fillna(default).astype(float)

def write_to_google_sheet(gc, sheet_name, df, spreadsheet_name=GOOGLE_SHEET_NAME):
    """데이터프레임을 구글 시트에 기록합니다. (덮어쓰기)"""
    if not gc: return False
    try:
        spreadsheet = gc.open(spreadsheet_name)
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"  - 시트 '{sheet_name}' 생성 중... (파일: {spreadsheet_name})")
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=len(df)+50, cols=len(df.columns))
        
        # 데이터 준비 (Date 컬럼 문자열 변환 등)
        df_copy = df.copy()
        if 'Date' in df_copy.columns:
            df_copy['Date'] = df_copy['Date'].dt.strftime('%Y-%m-%d')
        
        # 리스트로 변환
        values = [df_copy.columns.values.tolist()] + df_copy.values.tolist()
        
        # 시트 클리어 및 업데이트
        worksheet.clear()
        worksheet.update(values)
        return True
    except Exception as e:
        print(f"❌ 구글 시트 업로드 실패 ('{sheet_name}' at '{spreadsheet_name}'): {e}")
        return False

# --- ---

# --- 데이터 로딩 함수 (수익률 시트) ---
def read_and_aggregate_data(gc, sheet_names, date_col_idx, deposit_col_idx, withdrawal_col_idx, value_col_idx, start_date=None, end_date=None):
    """
    지정된 수익률 시트들에서 데이터를 읽어 집계하되,
    최종 결과는 모든 시트의 '평가액 > 0' 데이터가 존재하는 마지막 날짜까지만 포함하여 반환합니다.
    또한 계산된 최종 공통 마감일도 반환합니다.
    """
    if not gc: print("❌ 데이터 로딩 오류: 구글 시트 클라이언트(gc)가 없습니다."); return None, None
    try: spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    except Exception as e: print(f"❌ 스프레드시트 '{GOOGLE_SHEET_NAME}' 열기 오류: {e}"); return None, None

    sheet_dfs = {}
    all_data_list = []
    print(f"\n--- 데이터 로딩 시작 (시트: {sheet_names}) ---")
    for sheet_name in sheet_names:
        try:
            print(f"  ▶️ 시트 '{sheet_name}' 읽는 중...")
            worksheet = spreadsheet.worksheet(sheet_name); data = worksheet.get_all_values()
            if len(data) < 2: print(f"    - 정보: '{sheet_name}' 데이터 없음."); sheet_dfs[sheet_name] = pd.DataFrame(); continue
            header = data[0]; data_rows = data[1:]
            required_indices = [date_col_idx, deposit_col_idx, withdrawal_col_idx, value_col_idx]; max_idx = max(required_indices)
            if max_idx >= len(header): print(f"    - ❌ 오류: '{sheet_name}' 컬럼 수 부족."); sheet_dfs[sheet_name] = pd.DataFrame(); continue

            extracted_data = []
            for row in data_rows:
                if len(row) > max_idx:
                     extracted_data.append([row[date_col_idx], row[deposit_col_idx], row[withdrawal_col_idx], row[value_col_idx]])
            if not extracted_data: print(f"    - 정보: '{sheet_name}' 유효 데이터 행 없음."); sheet_dfs[sheet_name] = pd.DataFrame(); continue

            df = pd.DataFrame(extracted_data, columns=['Date_Str', 'Deposit_Str', 'Withdrawal_Str', 'Value_Str'])
            df['Date'] = pd.to_datetime(df['Date_Str'], errors='coerce'); df = df.dropna(subset=['Date'])
            if df.empty: print(f"    - 정보: '{sheet_name}' 유효 날짜 데이터 없음."); sheet_dfs[sheet_name] = pd.DataFrame(); continue

            df['Deposit'] = clean_numeric_column(df['Deposit_Str'], default=0.0)
            df['Withdrawal'] = clean_numeric_column(df['Withdrawal_Str'], default=0.0)
            df['Value'] = clean_numeric_column(df['Value_Str'], default=0.0)
            df = df.drop_duplicates(subset=['Date'], keep='last')
            df = df.set_index('Date')[['Value', 'Deposit', 'Withdrawal']]
            df = df.sort_index()

            sheet_dfs[sheet_name] = df
            all_data_list.append(df)
            print(f"    - '{sheet_name}' 처리 완료 ({len(df)} 행, 마지막 날짜: {df.index.max().strftime('%Y-%m-%d') if not df.empty else 'N/A'}).")
        except gspread.exceptions.WorksheetNotFound: print(f"    - ⚠️ 경고: 시트 '{sheet_name}' 없음."); sheet_dfs[sheet_name] = pd.DataFrame()
        except gspread.exceptions.APIError as e_api: print(f"    - ❌ API 오류 ('{sheet_name}' 읽기 중): {e_api}"); sheet_dfs[sheet_name] = pd.DataFrame()
        except Exception as e: print(f"    - ❌ 오류: '{sheet_name}' 처리 중: {e}"); traceback.print_exc(); sheet_dfs[sheet_name] = pd.DataFrame()

    if not all_data_list: print("❌ 최종 오류: 유효 데이터 시트 없음."); return None, None

    print("\n--- 데이터 집계 (concat + groupby) ---")
    combined_df = pd.concat(all_data_list)
    aggregated_df = combined_df.groupby(combined_df.index)[['Value', 'Deposit', 'Withdrawal']].sum(numeric_only=True)
    aggregated_df['NetCashFlow'] = aggregated_df['Deposit'] - aggregated_df['Withdrawal']
    aggregated_df = aggregated_df.sort_index()
    print(f"  - 집계 완료 (총 {len(aggregated_df)}일 데이터, 날짜 범위: {aggregated_df.index.min().strftime('%Y-%m-%d')} ~ {aggregated_df.index.max().strftime('%Y-%m-%d')})")

    last_common_date = None
    expected_sheet_count = len(ACCOUNT_SHEETS) if sheet_names == list(ACCOUNT_SHEETS.values()) else len(sheet_names)

    if len(sheet_dfs) == expected_sheet_count:
        max_value_dates = []
        for name, df in sheet_dfs.items():
            df_filtered = df[df['Value'] > 1e-9]
            if not df_filtered.empty:
                last_valid_date = df_filtered.index.max()
                max_value_dates.append(last_valid_date)
                print(f"    - '{name}' 평가액>0 마지막 날짜: {last_valid_date.strftime('%Y-%m-%d')}")
            else:
                print(f"    - '{name}' 평가액>0 데이터 없음 (마감일 계산 제외)")

        if len(max_value_dates) == expected_sheet_count:
            last_common_date = min(max_value_dates)
            print(f"  - 최종 공통 마감일 결정 (평가액>0 기준): {last_common_date.strftime('%Y-%m-%d')}")
            original_agg_rows = len(aggregated_df)
            aggregated_df = aggregated_df[aggregated_df.index <= last_common_date]
            filtered_agg_rows = len(aggregated_df)
            if original_agg_rows != filtered_agg_rows: print(f"  - 최종 공통 마감일 기준으로 데이터 필터링 완료 ({filtered_agg_rows}/{original_agg_rows} 행).")
            elif filtered_agg_rows > 0 : print(f"  - 최종 공통 마감일({last_common_date.strftime('%Y-%m-%d')})이 이미 마지막 날짜임. 필터링 불필요.")
        elif not max_value_dates: print("⚠️ 경고: 모든 시트에 평가액>0 데이터가 없어 마감일 제한 불가.")
        else: print(f"⚠️ 경고: 일부 시트({len(max_value_dates)}/{expected_sheet_count})에만 평가액>0 데이터가 있어 마감일 제한 불가.")
    else: print(f"⚠️ 경고: 모든 대상 시트({expected_sheet_count}개)를 읽지 못해({len(sheet_dfs)}개) 마감일 제한 적용 안 함.")

    if start_date: aggregated_df = aggregated_df[aggregated_df.index >= pd.to_datetime(start_date)]
    if end_date: aggregated_df = aggregated_df[aggregated_df.index <= pd.to_datetime(end_date)]

    print(f"--- 데이터 로딩 및 집계 완료 (총 {len(aggregated_df)}일 데이터 사용) ---")
    if aggregated_df.empty: print("⚠️ 경고: 최종 데이터 없음."); return None, None

    return aggregated_df[['Value', 'NetCashFlow']], last_common_date
# --- ---

# --- TWR 계산 함수 ---
def calculate_twr(aggregated_data_adj):
    """TWR(%) 계산 (입력은 배당 조정된 데이터, 시작점 0 처리 포함)"""
    required_cols = ['Value', 'NetCashFlow']
    if aggregated_data_adj is None or not isinstance(aggregated_data_adj, pd.DataFrame) or aggregated_data_adj.empty \
       or not all(col in aggregated_data_adj.columns for col in required_cols): print(f"❌ TWR 계산 오류: 유효 입력 아님."); return None
    if len(aggregated_data_adj) < 2: print("❌ TWR 계산 오류: 데이터 부족 (최소 2일)."); return None
    print("\n--- TWR(시간가중수익률) 계산 시작 ---")
    df = aggregated_data_adj.copy().sort_index(); df['Value'] = pd.to_numeric(df['Value'], errors='coerce').fillna(0.0).astype('float64'); df['NetCashFlow'] = pd.to_numeric(df['NetCashFlow'], errors='coerce').fillna(0.0).astype('float64')
    df['StartValue'] = df['Value'].shift(1); df = df.iloc[1:].copy()
    if df.empty: print("❌ TWR 계산 오류: 첫 날 제외 후 데이터 없음."); return None
    denominator = df['StartValue'] + df['NetCashFlow']; df['DailyFactor'] = 1.0
    mask_start_zero_flow_positive = (df['StartValue'].abs() < 1e-9) & (df['NetCashFlow'] > 1e-9)
    mask_start_positive_denom_valid = (df['StartValue'] > 1e-9) & (denominator.abs() > 1e-9)
    df.loc[mask_start_zero_flow_positive, 'DailyFactor'] = df.loc[mask_start_zero_flow_positive, 'Value'] / df.loc[mask_start_zero_flow_positive, 'NetCashFlow']
    df.loc[mask_start_positive_denom_valid, 'DailyFactor'] = df.loc[mask_start_positive_denom_valid, 'Value'] / denominator.loc[mask_start_positive_denom_valid]
    df['DailyFactor'] = df['DailyFactor'].replace([np.inf, -np.inf], np.nan).fillna(1.0); df['DailyFactor'] = df['DailyFactor'].clip(lower=0.1, upper=10.0)
    df['CumulativeFactor'] = df['DailyFactor'].cumprod()
    df['TWR'] = (df['CumulativeFactor'] - 1) * 100; print("--- TWR 계산 완료 ---"); return df[['TWR']]

# --- 배당 데이터 로드 및 처리 함수 --- (이전과 동일)
def load_and_process_dividends(gc):
    print(f"\n--- 배당 데이터 로딩 시작 ({DIVIDEND_SHEET_NAME}) ---")
    try:
        spreadsheet = gc.open(GOOGLE_SHEET_NAME); dividend_ws = spreadsheet.worksheet(DIVIDEND_SHEET_NAME)
        dividend_values = dividend_ws.get_all_values()
        if not dividend_values or len(dividend_values) < 2: print(f"ℹ️ '{DIVIDEND_SHEET_NAME}' 데이터 없음."); return None
        header = dividend_values[0]; data_rows = dividend_values[1:]
        if DIV_DATE_IDX >= len(header) or DIV_AMOUNT_IDX >= len(header) or DIV_ACCOUNT_IDX >= len(header): print(f"❌ 배당 시트 컬럼 부족"); return None
        max_needed_idx = max(DIV_DATE_IDX, DIV_AMOUNT_IDX, DIV_ACCOUNT_IDX)
        processed_data = []
        for row in data_rows:
             if len(row) > max_needed_idx: processed_data.append({'Date_Str': row[DIV_DATE_IDX], 'DividendAmount_Str': row[DIV_AMOUNT_IDX], 'AccountName_Raw': row[DIV_ACCOUNT_IDX]})
        if not processed_data: print(f"ℹ️ '{DIVIDEND_SHEET_NAME}' 유효 데이터 행 없음."); return None
        df_dividends = pd.DataFrame(processed_data); df_dividends['Date'] = pd.to_datetime(df_dividends['Date_Str'], errors='coerce'); df_dividends['DividendAmount'] = clean_numeric_column(df_dividends['DividendAmount_Str']); df_dividends['AccountName'] = df_dividends['AccountName_Raw'].astype(str).str.strip()
        df_dividends = df_dividends.dropna(subset=['Date', 'AccountName', 'DividendAmount']); df_dividends = df_dividends[df_dividends['DividendAmount'] != 0]
        if df_dividends.empty: print(f"ℹ️ '{DIVIDEND_SHEET_NAME}' 처리 후 유효 배당 데이터 없음."); return None
        dividends_grouped = df_dividends.groupby(['Date', 'AccountName'])['DividendAmount'].sum().reset_index()
        print(f"✅ 배당 데이터 {len(df_dividends)}건 로드 및 {len(dividends_grouped)}건 그룹화 완료.")
        return dividends_grouped
    except gspread.exceptions.WorksheetNotFound: print(f"⚠️ 경고: 배당 시트 '{DIVIDEND_SHEET_NAME}' 없음."); return None
    except Exception as e: print(f"❌ 오류: 배당 데이터 처리 중: {e}"); traceback.print_exc(); return None

# --- 메인 실행 함수 ---
def main():
    print("--- 전체 및 개별 계좌 TWR / 단순 손익 계산 (배당 반영) 및 시각화 시작 ---")
    test_start_date = None; test_end_date = None
    twr_results = {}; gain_loss_results = {}; daily_values_results = {}
    calculation_success = True
    graph_displayed = False
    data_saved = False
    last_common_date_used = None

    gc = connect_google_sheets()
    if not gc: raise ConnectionError("🔥 구글 시트 연결 실패! 종료합니다.")
    all_dividends_grouped = load_and_process_dividends(gc)

    # --- 1. 전체 포트폴리오 계산 ---
    print("\n>>> 전체 포트폴리오 계산 시작 <<<")
    all_sheet_names = list(ACCOUNT_SHEETS.values())
    total_aggregated_data_unadj, last_common_date_used = read_and_aggregate_data(
        gc, all_sheet_names, DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX,
        test_start_date, test_end_date
    )
    if isinstance(total_aggregated_data_unadj, pd.DataFrame):
        total_aggregated_data = total_aggregated_data_unadj.copy()
        # 배당 조정
        if all_dividends_grouped is not None:
            total_daily_dividends = all_dividends_grouped.groupby('Date')['DividendAmount'].sum()
            total_aggregated_data = total_aggregated_data.join(total_daily_dividends.rename('DividendAmount'), how='left', on='Date')
        else: total_aggregated_data['DividendAmount'] = 0
        total_aggregated_data['DividendAmount'] = total_aggregated_data['DividendAmount'].fillna(0)
        total_aggregated_data['Value'] = total_aggregated_data['Value'] + total_aggregated_data['DividendAmount']
        total_dividend_sum = total_aggregated_data['DividendAmount'].sum()
        if total_dividend_sum > 0: print(f"  - Value 배당 조정 완료 (총: {total_dividend_sum:,.0f})")

        # 계산에 사용할 데이터 (평가액 > 0 필터 제거됨)
        total_aggregated_data_for_calc = total_aggregated_data

        if not total_aggregated_data_for_calc.empty:
            total_twr_df = calculate_twr(total_aggregated_data_for_calc[['Value', 'NetCashFlow']])
            twr_results['Total'] = total_twr_df
            if total_twr_df is None: calculation_success = False
        else: print("❌ 전체 유효 데이터 없음(TWR 계산 불가)."); twr_results['Total'] = None; calculation_success = False

        if not total_aggregated_data_for_calc.empty:
             try:
                 start_value_period = total_aggregated_data_for_calc['Value'].iloc[0]
                 end_value_period = total_aggregated_data_for_calc['Value'].iloc[-1]
                 net_cash_flow_period = total_aggregated_data_for_calc['NetCashFlow'].sum()
                 dollar_gain_loss = end_value_period - start_value_period - net_cash_flow_period
                 gain_loss_results['Total'] = dollar_gain_loss; print(f"💰 전체 단순 손익: {dollar_gain_loss:,.0f} 원")
             except IndexError: print("❌ 전체 단순 손익 계산 오류: 데이터 기간 부족"); gain_loss_results['Total'] = None; calculation_success = False
             except Exception as e_gl: print(f"❌ 전체 단순 손익 계산 오류: {e_gl}"); gain_loss_results['Total'] = None; calculation_success = False
        else: gain_loss_results['Total'] = None
        
        # 데일리 값 저장 (Total)
        if not total_aggregated_data_for_calc.empty:
            daily_values_results['Total'] = total_aggregated_data_for_calc[['Value']].copy()

        if 'Total' in twr_results and twr_results['Total'] is not None:
             final_total_twr = twr_results['Total']['TWR'].dropna().iloc[-1] if not twr_results['Total']['TWR'].dropna().empty else 'N/A'
             if isinstance(final_total_twr, (float, np.number)): print(f"📈 전체 최종 TWR: {final_total_twr:.2f}%")
             else: print(f"📈 전체 최종 TWR: {final_total_twr}")
        else: print("  (전체 TWR 계산 실패)")
    else: print("❌ 전체 데이터 로딩/집계 실패."); twr_results['Total'] = None; gain_loss_results['Total'] = None; calculation_success = False

    # --- 2. 개별 계좌 계산 ---
    for acc_name, sheet_name in ACCOUNT_SHEETS.items():
        print(f"\n>>> {acc_name} ({sheet_name}) 계산 시작 <<<")
        aggregated_data_unadj, _ = read_and_aggregate_data(
            gc, [sheet_name], DATE_COL_IDX, DEPOSIT_COL_IDX, WITHDRAWAL_COL_IDX, VALUE_COL_IDX,
            test_start_date, test_end_date
        )
        if isinstance(aggregated_data_unadj, pd.DataFrame):
            aggregated_data = aggregated_data_unadj.copy()
            # 배당 조정
            account_dividends = None
            if all_dividends_grouped is not None:
                account_dividends_filtered = all_dividends_grouped[all_dividends_grouped['AccountName'] == acc_name]
                if not account_dividends_filtered.empty: account_dividends = account_dividends_filtered.set_index('Date')['DividendAmount']; aggregated_data = aggregated_data.join(account_dividends.rename('DividendAmount'), how='left', on='Date')
                else: aggregated_data['DividendAmount'] = 0
            else: aggregated_data['DividendAmount'] = 0
            aggregated_data['DividendAmount'] = aggregated_data['DividendAmount'].fillna(0)
            aggregated_data['Value'] = aggregated_data['Value'] + aggregated_data['DividendAmount']
            account_dividend_sum = aggregated_data['DividendAmount'].sum()
            if account_dividend_sum > 0: print(f"  - Value 배당 조정 완료 ({acc_name} 총: {account_dividend_sum:,.0f})")

            # 계산용 데이터 (평가액 > 0 필터 제거됨)
            aggregated_data_for_calc = aggregated_data

            if not aggregated_data_for_calc.empty:
                twr_df = calculate_twr(aggregated_data_for_calc[['Value', 'NetCashFlow']])
                twr_results[acc_name] = twr_df
                if twr_df is None: calculation_success = False
            else: print(f"❌ {acc_name} 유효 데이터 없음(TWR 계산 불가)."); twr_results[acc_name] = None; calculation_success = False

            if not aggregated_data_for_calc.empty:
                 try:
                     start_value_period = aggregated_data_for_calc['Value'].iloc[0]
                     end_value_period = aggregated_data_for_calc['Value'].iloc[-1]
                     net_cash_flow_period = aggregated_data_for_calc['NetCashFlow'].sum()
                     dollar_gain_loss = end_value_period - start_value_period - net_cash_flow_period

                     # --- 디버깅 출력 (유지) ---
                     if acc_name == '금현물':
                         print(f"  DEBUG 금현물: Start Value = {start_value_period:,.0f} (날짜: {aggregated_data_for_calc.index[0].strftime('%Y-%m-%d')})")
                         print(f"  DEBUG 금현물: End Value = {end_value_period:,.0f} (날짜: {aggregated_data_for_calc.index[-1].strftime('%Y-%m-%d')})")
                         print(f"  DEBUG 금현물: Net Cash Flow (Sum B - Sum C) = {net_cash_flow_period:,.0f}")
                         print(f"  DEBUG 금현물: Calculated Gain/Loss = {dollar_gain_loss:,.0f}")
                     # --- ---

                     gain_loss_results[acc_name] = dollar_gain_loss; print(f"💰 {acc_name} 단순 손익: {dollar_gain_loss:,.0f} 원")
                 except IndexError: print(f"❌ {acc_name} 단순 손익 계산 오류: 데이터 기간 부족"); gain_loss_results[acc_name] = None; calculation_success = False
                 except Exception as e_gl: print(f"❌ {acc_name} 단순 손익 계산 오류: {e_gl}"); gain_loss_results[acc_name] = None; calculation_success = False
            else: gain_loss_results[acc_name] = None
            
            # 데일리 값 저장 (개별)
            if not aggregated_data_for_calc.empty:
                daily_values_results[acc_name] = aggregated_data_for_calc[['Value']].copy()

            if acc_name in twr_results and twr_results[acc_name] is not None:
                 final_twr = twr_results[acc_name]['TWR'].dropna().iloc[-1] if not twr_results[acc_name]['TWR'].dropna().empty else 'N/A'
                 if isinstance(final_twr, (float, np.number)): print(f"📈 {acc_name} 최종 TWR: {final_twr:.2f}%")
                 else: print(f"📈 {acc_name} 최종 TWR: {final_twr}")
            else: print(f"  ({acc_name} TWR 계산 실패)")
        else: print(f"❌ {acc_name} 데이터 로딩/집계 실패."); twr_results[acc_name] = None; gain_loss_results[acc_name] = None; calculation_success = False

    # --- 3. 계산 결과 파일 저장 ---
    if calculation_success and twr_results:
        print("\n--- 계산 결과 파일 저장 중 ---")
        try:
            all_twr_dfs = []
            for acc_name, twr_df in twr_results.items():
                if twr_df is not None and not twr_df.empty: temp_df = twr_df.copy(); temp_df['Account'] = acc_name; all_twr_dfs.append(temp_df.reset_index())
            if all_twr_dfs:
                combined_twr_df = pd.concat(all_twr_dfs, ignore_index=True)
                if last_common_date_used:
                    combined_twr_df['Date'] = pd.to_datetime(combined_twr_df['Date'])
                    combined_twr_df = combined_twr_df[combined_twr_df['Date'] <= last_common_date_used]
                    print(f"  - TWR 결과 파일 저장 시 최종 공통 마감일({last_common_date_used.strftime('%Y-%m-%d')}) 이전 데이터만 포함합니다.")
                combined_twr_df.to_csv(TWR_CSV_PATH, index=False, encoding='utf-8-sig'); print(f"✅ TWR 결과 저장 완료: {TWR_CSV_PATH}"); data_saved = True
            else: print("⚠️ 저장할 유효 TWR 결과 없음.")
            serializable_gain_loss = {k: (None if pd.isna(v) else v) for k, v in gain_loss_results.items()}
            with open(GAIN_LOSS_JSON_PATH, 'w', encoding='utf-8') as f: json.dump(serializable_gain_loss, f, ensure_ascii=False, indent=4)
            print(f"✅ 수 손익 결과 저장 완료: {GAIN_LOSS_JSON_PATH}"); data_saved = True

            # --- 데일리 평가금액(Value) 저장 (Streamlit 월별 자산 추이용) ---
            DAILY_VALUES_CSV_PATH = os.path.join(CURRENT_DIR, 'daily_values.csv')
            if daily_values_results:
                print(f"\n--- 데일리 평가액 데이터 저장 중 ({DAILY_VALUES_CSV_PATH}) ---")
                all_values_dfs = []
                for acc_name, val_df in daily_values_results.items():
                    if val_df is not None and not val_df.empty:
                         temp_df = val_df.copy(); temp_df['Account'] = acc_name
                         all_values_dfs.append(temp_df.reset_index())
                
                if all_values_dfs:
                    combined_values_df = pd.concat(all_values_dfs, ignore_index=True)
                    if last_common_date_used:
                         combined_values_df['Date'] = pd.to_datetime(combined_values_df['Date'])
                         combined_values_df = combined_values_df[combined_values_df['Date'] <= last_common_date_used]
                    combined_values_df.to_csv(DAILY_VALUES_CSV_PATH, index=False, encoding='utf-8-sig')
                    combined_values_df.to_csv(DAILY_VALUES_CSV_PATH, index=False, encoding='utf-8-sig')
                    print(f"✅ 데일리 평가액 저장 완료 (CSV).")
                    
                    # Google Sheet Upload (Separate File)
                    print(f"📤 데일리 평가액 구글 시트 업로드 중... (파일: '{DAILY_ASSET_SPREADSHEET_NAME}', 시트: '{DAILY_ASSET_SHEET_NAME}')")
                    if write_to_google_sheet(gc, DAILY_ASSET_SHEET_NAME, combined_values_df, spreadsheet_name=DAILY_ASSET_SPREADSHEET_NAME):
                        print(f"✅ 구글 시트 업로드 완료.")
                else: print("⚠️ 저장할 데일리 평가액 데이터 없음.")

        except Exception as e_save: print(f"❌ 결과 파일 저장 중 오류 발생: {e_save}"); traceback.print_exc(); calculation_success = False
    # --- ---
    print("\n--- TWR 결과 시각화 중 ---")
    if plt is None: print("⚠️ 'matplotlib' 라이브러리가 없어 그래프 생성 불가.")
    elif not twr_results or all(df is None for df in twr_results.values()): print("⚠️ 시각화할 TWR 데이터가 없음.")
    else:
        try:
            fig, axes = plt.subplots(3, 2, figsize=(14, 15)); axes = axes.flatten()
            plot_order = ['Total'] + list(ACCOUNT_SHEETS.keys()); plot_count = 0
            for i, acc_name in enumerate(plot_order):
                if acc_name not in twr_results: continue
                ax = axes[i]; twr_df = twr_results[acc_name]
                title_name = "전체 포트폴리오" if acc_name == "Total" else acc_name
                if twr_df is not None and not twr_df.empty:
                     plot_df = twr_df.copy()
                     if last_common_date_used: # 최종 공통 마감일 필터링
                         plot_df = plot_df[plot_df.index <= last_common_date_used]
                     if not plot_df.empty:
                         # **** 수정: Total 그래프 이동평균선 제거 ****
                         # if acc_name == 'Total':
                         #     plot_df['TWR_MA3'] = plot_df['TWR'].rolling(window=3, min_periods=1).mean()
                         #     ax.plot(plot_df.index, plot_df['TWR'], label=f'{title_name} TWR', linewidth=1.0, alpha=0.6, color='skyblue')
                         #     ax.plot(plot_df.index, plot_df['TWR_MA3'], label=f'{title_name} TWR (3일 이동평균)', linewidth=1.8, color='dodgerblue')
                         #     ax.legend()
                         # else: ax.plot(plot_df.index, plot_df['TWR'], label=f'{title_name} TWR', linewidth=1.5, color='dodgerblue')
                         ax.plot(plot_df.index, plot_df['TWR'], label=f'{title_name} TWR', linewidth=1.5, color='dodgerblue') # 통일된 스타일 적용 (선택적)
                         # **** --- ****
                         ax.set_title(f'{title_name} 시간가중수익률(TWR)'); ax.set_ylabel('수익률 (%)'); ax.grid(True, linestyle='--', alpha=0.6); plt.setp(ax.get_xticklabels(), rotation=30, ha='right'); ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1)); ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m')); plot_count += 1
                     else: ax.text(0.5, 0.5, f'{title_name}\n데이터 없음', ha='center', va='center', fontsize=12, color='gray'); ax.set_title(f'{title_name} 시간가중수익률(TWR)'); ax.set_xticks([]); ax.set_yticks([])
                else: ax.text(0.5, 0.5, f'{title_name}\n데이터 없음', ha='center', va='center', fontsize=12, color='gray'); ax.set_title(f'{title_name} 시간가중수익률(TWR)'); ax.set_xticks([]); ax.set_yticks([])
            for j in range(plot_count, len(axes)): axes[j].axis('off')
            plt.tight_layout(pad=3.0); plt.suptitle("전체 및 계좌별 시간가중수익률(TWR)", fontsize=16, y=1.03) # 제목에서 이동평균 언급 제거
            # **** 수정: plt.show() 주석 처리 또는 삭제 ****
            # print("✅ 그래프를 화면에 표시합니다...")
            # plt.show() # 자동 실행 위해 주석 처리/삭제
            graph_displayed = False # 자동 실행 시에는 True로 바꾸지 않음
            # **** --- ****
            # 그래프 파일 저장 (옵션 - 필요시 주석 해제)
            # graph_filename = f"twr_performance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            # graph_path = os.path.join(CURRENT_DIR, graph_filename)
            # try:
            #     plt.savefig(graph_path)
            #     print(f"✅ 그래프 파일 저장 완료: {graph_path}")
            # except Exception as e_save_fig:
            #     print(f"⚠️ 그래프 파일 저장 실패: {e_save_fig}")
            plt.close(fig) # 메모리 해제 위해 명시적 종료
        except Exception as e_graph: print(f"❌ 그래프 생성/표시 중 오류 발생: {e_graph}"); traceback.print_exc(); calculation_success = False
    # --- ---
    print("\n--- 모든 작업 완료 ---")
    return calculation_success

# --- 스크립트 실행 및 텔레그램 알림 ---
if __name__ == '__main__':
    start_run_time = time.time()
    final_message = ""; error_occurred = False; error_details_str = ""; main_success = False
    try:
        main_success = main()
        if not main_success: error_occurred = True; error_details_str = "계산, 저장 또는 그래프 생성 중 오류 발생 (로그 확인)"
    except ConnectionError as e: error_occurred = True; print(f"🔥 연결 오류: {e}"); error_details_str = traceback.format_exc()
    except Exception as e: error_occurred = True; print(f"🔥 예상치 못한 오류: {e}"); error_details_str = traceback.format_exc()
    finally:
        end_run_time = time.time(); elapsed_time = end_run_time - start_run_time
        if error_occurred: final_message = f"🔥 `{SCRIPT_NAME}` 실행 실패 (소요 시간: {elapsed_time:.2f}초)\n```\n{error_details_str[-1000:]}\n```"
        else: final_message = f"✅ `{SCRIPT_NAME}` 실행 성공 (소요 시간: {elapsed_time:.2f}초)"
        if final_message: telegram_utils.send_telegram_message(final_message)
        else: default_msg = f"ℹ️ `{SCRIPT_NAME}` 실행 완료되었으나 최종 상태 메시지 없음."; print(default_msg); telegram_utils.send_telegram_message(default_msg)