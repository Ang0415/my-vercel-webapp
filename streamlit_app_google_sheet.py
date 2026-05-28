# streamlit_app_google_sheet.py (Google Sheet Database Version)

import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import traceback
import yfinance as yf
from collections.abc import Mapping
import re
# --- 기본 설정 ---
PAGE_TITLE = "포트폴리오 대시보드"
PAGE_ICON = "📊"
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")

st.caption("v2.2 (Standalone Revert) - Updated at " + datetime.now().strftime("%H:%M:%S"))

# --- Header UI (Moved to top for immediate feedback) ---
col_title, col_refresh = st.columns([0.9, 0.1])
with col_title:
    st.title(PAGE_TITLE)
with col_refresh:
    st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
    if st.button("🔄", help="데이터 새로고침"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

# --- 경로 설정 ---
# TWR_CSV_PATH, GAIN_LOSS_JSON_PATH 삭제됨 (구글 시트로 대체)
DAILY_ASSET_SPREADSHEET_NAME = '성과_자산추이_Raw'
DAILY_ASSET_ACCOUNTS = ['Total', 'ISA', 'IRP', '연금', '금현물']
# DAILY_ASSET_SHEET_NAME 삭제 (이제 여러 시트 사용)
GOOGLE_SHEET_NAME = 'KYI_자산배분'
BALANCE_RAW_SHEET = '일별잔고_Raw'
WEIGHTS_RAW_SHEET = '일별비중_Raw'
SETTINGS_SHEET = '⚙️설정'
TRADES_SHEET = '🗓️매매일지'
GOLD_RATE_SHEET = '📈금현물 수익률'

# 성과 데이터 시트 이름
TWR_RAW_SHEET = '성과_TWR_Raw'
GAIN_LOSS_RAW_SHEET = '성과_손익_Raw'

# --- 지수 티커 설정 ---
KOSPI_TICKER = "^KS200"
SP500_TICKER = "^GSPC"

import time

# --- 유틸리티 함수 ---
import random

def safe_api_call(func, *args, retries=7, delay=2, **kwargs):
    """구글 시트 API 호출 래퍼 (Qouta exceeded 에러 발생 시 지수 백오프 적용 재시도)"""
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "Quota exceeded" in error_str:
                wait_time = delay * (2 ** i) + random.uniform(0, 1) # Jitter 추가
                if wait_time > 60: wait_time = 60 # 최대 60초 대기
                print(f"⚠️ API Quota exceeded. Retrying in {wait_time:.1f}s... ({i+1}/{retries})")
                time.sleep(wait_time)
            elif "500" in error_str or "502" in error_str or "503" in error_str: # Server Error
                 wait_time = delay * (2 ** i)
                 print(f"⚠️ Google API Server Error. Retrying in {wait_time:.1f}s... ({i+1}/{retries})")
                 time.sleep(wait_time)
            else:
                raise e
    
    # [MODIFIED] Crash 방지를 위해 Exception 발생 대신 None 반환
    print(f"❌ API Call Failed after {retries} retries (Max Limit)")
    return None

def clean_numeric_value(value, type_func=int):
    """단일 값을 숫자로 변환 (쉼표 및 타입 처리 개선)"""
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

@st.cache_data(ttl=600)
def load_daily_values(_gc):
    """데일리 평가액 시트들을 별도 구글 시트 파일에서 로드하여 하나로 합칩니다."""
    if not isinstance(_gc, gspread.Client): return pd.DataFrame()
    try:
        spreadsheet = safe_api_call(_gc.open, DAILY_ASSET_SPREADSHEET_NAME)
        
        all_dfs = []
        for acc in DAILY_ASSET_ACCOUNTS:
            try:
                worksheet = safe_api_call(spreadsheet.worksheet, acc)
                data = safe_api_call(worksheet.get_all_records)
                df = pd.DataFrame(data)
                if not df.empty and 'Date' in df.columns:
                    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
                    df['Account'] = acc # 계좌명 컬럼 추가
                    all_dfs.append(df)
            except gspread.exceptions.WorksheetNotFound:
                pass # 해당 계좌 시트가 아직 없으면 건너뜀
                
        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
            print(f"Log: 데일리 평가액 데이터 로드 완료 (총 {len(combined_df)} rows)")
            return combined_df
            
        return pd.DataFrame()
    except gspread.exceptions.SpreadsheetNotFound:
        st.warning(f"데일리 평가액 파일(Spreadsheet: '{DAILY_ASSET_SPREADSHEET_NAME}')을 찾을 수 없습니다. 공유 설정을 확인하세요.")
        return pd.DataFrame()
    except Exception as e: st.error(f"데일리 평가액 데이터 로딩 중 오류 발생: {e}"); return pd.DataFrame()

# --- 구글 시트 연결 ---
@st.cache_resource(ttl=600)
def connect_google_sheets():
    """구글 시트 API에 연결하고 클라이언트 객체를 반환합니다."""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # 1. JSON 파일 인증 시도 (로컬/GitHub Actions)
        json_key_file = 'stock-auto-writer-44eaa06c140c.json'
        if os.path.exists(json_key_file):
            print(f"Log: Found JSON key file '{json_key_file}'. Authenticating...")
            creds = ServiceAccountCredentials.from_json_keyfile_name(json_key_file, scope)
            gc = gspread.authorize(creds)
            return gc
            
        # 2. Streamlit Secrets 인증 시도 (Streamlit Cloud 배포용)
        if "gcs_credentials" in st.secrets:
            creds_value = st.secrets["gcs_credentials"]; creds_dict = None
            if isinstance(creds_value, Mapping): print("Log: Reading secrets as dictionary-like object."); creds_dict = dict(creds_value)
            elif isinstance(creds_value, str):
                print("Log: Reading secrets as string, attempting JSON parse.")
                try: creds_dict = json.loads(creds_value)
                except json.JSONDecodeError:
                    try: escaped_string = creds_value.replace("\n", "\\n"); creds_dict = json.loads(escaped_string); print("Log: JSON parsing successful after escaping newlines.")
                    except json.JSONDecodeError as e_escaped: st.error(f"Secrets의 'gcs_credentials' 값 JSON 파싱 오류: {e_escaped}..."); return None
            else: st.error(f"Secrets의 'gcs_credentials' 값 타입 오류..."); return None
            
            if creds_dict:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope); gc = gspread.authorize(creds)
                gc.list_spreadsheet_files(); print("Log: Google Sheets 연결 성공"); return gc
            else: st.error("인증 정보(creds_dict)를 준비하지 못했습니다."); return None
            
        st.error("Google Sheets 인증 정보를 찾을 수 없습니다. (JSON 파일 또는 st.secrets 필요)")
        return None
        
    except KeyError as e: st.error(f"Streamlit Secrets 접근 오류: 키 '{e}' 없음..."); return None
    except Exception as e: st.error(f"구글 시트 연결 실패: {e}"); traceback.print_exc(); return None

# --- 데이터 로딩 함수들 (구글 시트 기반으로 변경됨) ---
@st.cache_data(ttl=60)
def load_twr_data(_gc):
    """'성과_TWR_Raw' 파일의 각 계좌별 시트에서 TWR 데이터를 로드하여 통합합니다."""
    TWR_SPREADSHEET_NAME = '성과_TWR_Raw'
    TARGET_ACCOUNTS = ['Total', 'ISA', 'IRP', '연금', '금현물']
    
    try:
        spreadsheet = safe_api_call(_gc.open, TWR_SPREADSHEET_NAME)
        all_dfs = []
        
        for acc in TARGET_ACCOUNTS:
            try:
                ws = safe_api_call(spreadsheet.worksheet, acc)
                data = safe_api_call(ws.get_all_records)
                df = pd.DataFrame(data)
                
                if not df.empty:
                    # 컬럼명 매핑 (한글 -> 영어)
                    # 저장 시 '날짜', '계좌명', 'TWR' 로 저장됨
                    if '날짜' in df.columns: df = df.rename(columns={'날짜': 'Date'})
                    if '계좌명' in df.columns: df = df.rename(columns={'계좌명': 'Account'})
                    
                    # Account 컬럼이 없으면 시트명으로 추가 (안전장치)
                    if 'Account' not in df.columns:
                        df['Account'] = acc
                        
                    if 'Date' in df.columns and 'TWR' in df.columns:
                        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
                        df['TWR'] = pd.to_numeric(df['TWR'], errors='coerce')
                        all_dfs.append(df)
            except gspread.exceptions.WorksheetNotFound:
                pass # 해당 계좌 시트가 없으면 건너뜀
            except Exception as e_sheet:
                print(f"Log: '{acc}' 시트 로딩 중 오류: {e_sheet}")

        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
            combined_df = combined_df.dropna(subset=['Date']).sort_values('Date')
            combined_df['Account'] = combined_df['Account'].astype(str).str.strip()
            print(f"Log: TWR 데이터 로드 완료 (총 {len(combined_df)} rows, {len(all_dfs)}개 시트)")
            return combined_df
        else:
            return pd.DataFrame()

    except gspread.exceptions.SpreadsheetNotFound:
        st.warning(f"TWR 데이터 파일('{TWR_SPREADSHEET_NAME}')을 찾을 수 없습니다.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"TWR 데이터 로딩 중 오류 발생: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def load_gain_loss_data(_gc):
    """구글 시트의 성과_손익_Raw 파일에서 각 계좌별 최신 단순 손익 데이터를 로드합니다."""
    # [MODIFIED] 성과_손익_Raw는 별도 파일이며, 각 계좌별 시트가 존재함
    try:
        spreadsheet = safe_api_call(_gc.open, GAIN_LOSS_RAW_SHEET)
        TARGET_ACCOUNTS = ['Total', 'ISA', 'IRP', '연금', '금현물']
        
        all_dfs = []
        for acc in TARGET_ACCOUNTS:
            try:
                ws = safe_api_call(spreadsheet.worksheet, acc)
                data = safe_api_call(ws.get_all_records)
                df = pd.DataFrame(data)
                if not df.empty:
                    all_dfs.append(df)
            except gspread.exceptions.WorksheetNotFound:
                pass
                
        if not all_dfs:
            return {}
            
        df = pd.concat(all_dfs, ignore_index=True)
        
        if not df.empty:
            # 컬럼명 공백 제거
            df.columns = df.columns.str.strip()
            
            # 필수 컬럼 존재 확인
            required_cols = ['날짜', '계좌명', '단순손익']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                return {}

            # 날짜 변환 및 유효 데이터 필터링
            df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
            df = df.dropna(subset=['날짜'])
            
            if df.empty: return {}
            
            # 각 계좌별로 가장 최근 날짜의 행만 추출
            # 계좌명 공백 제거 (데이터 정규화)
            df['계좌명'] = df['계좌명'].astype(str).str.strip()
            
            # 계좌별 최신 날짜 인덱스 찾기
            idx = df.groupby('계좌명')['날짜'].idxmax()
            latest_df = df.loc[idx]
            
            result = {}
            for _, row in latest_df.iterrows():
                acc = row['계좌명']
                val = row['단순손익']
                clean_val = clean_numeric_value(val, float)
                result[acc] = clean_val
                
            print(f"Log: 단순 손익 데이터 로드 완료 ({len(result)}개 계좌)")
            return result
        else:
            return {}
            
    except gspread.exceptions.SpreadsheetNotFound:
        st.warning(f"단순손익 파일('{GAIN_LOSS_RAW_SHEET}')을 찾을 수 없습니다.")
        return {}
    except Exception as e:
        st.error(f"단순 손익 데이터 로딩 중 오류 발생: {e}")
        return {}

@st.cache_data(ttl=60)
def load_historical_total_profit(_gc):
    """성과_손익_Raw 파일의 Total 시트에서 역사적 단순손익 데이터를 전체 로드합니다."""
    try:
        spreadsheet = safe_api_call(_gc.open, GAIN_LOSS_RAW_SHEET)
        ws = safe_api_call(spreadsheet.worksheet, 'Total')
        data = safe_api_call(ws.get_all_records)
        df = pd.DataFrame(data)
        if not df.empty:
            df.columns = df.columns.str.strip()
            df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
            df = df.dropna(subset=['날짜'])
            df = df.rename(columns={'날짜': 'Date', '단순손익': 'Profit'})
            return df[['Date', 'Profit']]
    except Exception as e:
        print("Error in load_historical_total_profit:", e)
    return pd.DataFrame()

# load_latest_balances, load_historical_balances 삭제됨 (성과_자산추이_Raw 사용으로 대체)


@st.cache_data(ttl=600)
def load_allocation_data(_gc, latest_data_date):
    """자산 배분 데이터('⚙️설정', '일별비중_Raw')를 로드하고 비교 테이블 생성"""
    if not isinstance(_gc, gspread.Client) or not isinstance(latest_data_date, pd.Timestamp): st.error("load_allocation_data: 유효한 gc 또는 latest_data_date 아님."); return pd.DataFrame(), pd.DataFrame()
    settings_df = pd.DataFrame(); comparison_df_final = pd.DataFrame()
    BASE_TOTAL_ASSET = 80000000 
    
    # [MODIFIED] 별도 파일 설정
    WEIGHTS_FILE_NAME = '일별비중_RAW'
    WEIGHTS_SHEET_NAME = '일별비중_Raw'
    
    try:
        # 1. 설정 로드 (기존 KYI_자산배분 파일)
        spreadsheet = safe_api_call(_gc.open, GOOGLE_SHEET_NAME)
        settings_ws = safe_api_call(spreadsheet.worksheet, SETTINGS_SHEET)
        settings_values = safe_api_call(settings_ws.get_all_values)
        if len(settings_values) > 1:
            header = settings_values[0]
            try:
                required_cols = ['목표구분', '목표국적', '목표비중']; col_indices = {}
                for col in required_cols: col_indices[col] = header.index(col)
                target_class_col, target_nation_col, target_perc_col = col_indices['목표구분'], col_indices['목표국적'], col_indices['목표비중']

                processed_targets_combined = {}; unique_target_keys = set()
                for i, row in enumerate(settings_values[1:]):
                    if len(row) > max(target_class_col, target_nation_col, target_perc_col):
                        try:
                            asset_class = str(row[target_class_col]).strip(); nationality = str(row[target_nation_col]).strip(); target_perc_str = str(row[target_perc_col]).strip().replace('%','')
                            combined_key = (asset_class, nationality)
                            if asset_class and nationality and target_perc_str:
                                if combined_key not in unique_target_keys:
                                     try:
                                         target_perc = float(target_perc_str)
                                         if target_perc > 0:
                                             combined_name = f"{nationality} {asset_class}" if asset_class != '대체투자' else "금"
                                             processed_targets_combined[combined_name] = target_perc; unique_target_keys.add(combined_key)
                                     except ValueError: pass
                        except Exception: pass
                
                if processed_targets_combined:
                    target_df = pd.DataFrame(list(processed_targets_combined.items()), columns=['종합 분류', '목표 비중(%)'])
                    settings_df = target_df[target_df['목표 비중(%)'] > 0].sort_values(by='목표 비중(%)', ascending=False)
            except ValueError: pass

        # 2. 비중 데이터 로드 (별도 파일: 일별비중_Raw)
        try:
            w_spreadsheet = safe_api_call(_gc.open, WEIGHTS_FILE_NAME)
            
            # [MODIFIED] Robust sheet matching
            found_ws = None
            worksheets_list = safe_api_call(w_spreadsheet.worksheets)
            for ws in worksheets_list:
                if ws.title.strip().lower() == WEIGHTS_SHEET_NAME.lower():
                    found_ws = ws
                    break
            
            if found_ws:
                weights_ws = found_ws
            else:
                raise gspread.exceptions.WorksheetNotFound(WEIGHTS_SHEET_NAME)
                
            weights_data = safe_api_call(weights_ws.get_all_records)
        except gspread.exceptions.SpreadsheetNotFound:
            st.warning(f"일별비중 파일('{WEIGHTS_FILE_NAME}')을 찾을 수 없습니다.")
            weights_data = []
        except gspread.exceptions.WorksheetNotFound:
            st.warning(f"일별비중 시트('{WEIGHTS_SHEET_NAME}')를 찾을 수 없습니다.")
            weights_data = []
            
        if not weights_data:
            comparison_df_final = pd.DataFrame(columns=['종합 분류', '현재 비중(%)', '현재 평가액', '목표 비중(%)', '목표 금액', '차이(%)', '현금차이'])
            if not settings_df.empty:
                comparison_df_final = settings_df.rename(columns={'목표 비중(%)':'목표 비중(%)'})
                comparison_df_final['현재 비중(%)'] = 0.0; comparison_df_final['현재 평가액'] = 0; comparison_df_final['목표 금액'] = (BASE_TOTAL_ASSET * (comparison_df_final['목표 비중(%)'] / 100)).round(0).astype(int); comparison_df_final['차이(%)'] = -comparison_df_final['목표 비중(%)']; comparison_df_final['현금차이'] = -comparison_df_final['목표 금액']
            return comparison_df_final.round({'차이(%)': 2}), settings_df

        weights_df = pd.DataFrame(weights_data); weights_df['날짜'] = pd.to_datetime(weights_df['날짜'], errors='coerce')
        latest_weights_df = weights_df[weights_df['날짜'] == latest_data_date].copy()
        
        if latest_weights_df.empty:
             comparison_df_final = pd.DataFrame(columns=['종합 분류', '현재 비중(%)', '현재 평가액', '목표 비중(%)', '목표 금액', '차이(%)', '현금차이'])
             if not settings_df.empty:
                 comparison_df_final = settings_df.rename(columns={'목표 비중(%)':'목표 비중(%)'})
                 comparison_df_final['현재 비중(%)'] = 0.0; comparison_df_final['현재 평가액'] = 0; comparison_df_final['목표 금액'] = (BASE_TOTAL_ASSET * (comparison_df_final['목표 비중(%)'] / 100)).round(0).astype(int); comparison_df_final['차이(%)'] = -comparison_df_final['목표 비중(%)']; comparison_df_final['현금차이'] = -comparison_df_final['목표 금액']
             return comparison_df_final.round({'차이(%)': 2}), settings_df

        has_nationality_col = '국적' in latest_weights_df.columns
        def get_combined_name(row):
            asset_class = str(row.get('자산구분', '')).strip(); nationality = str(row.get('국적', '')).strip() if has_nationality_col else ""
            if not asset_class: return '미분류'
            if asset_class == '대체투자': return "금"
            elif not nationality: return asset_class
            else:
                combined = f"{nationality} {asset_class}"
                # [MODIFIED] 한국 채권 -> 한국 채권 30 병합 (사용자 요청)
                if combined == "한국 채권":
                    return "한국 채권 30"
                return combined

        latest_weights_df['종합 분류'] = latest_weights_df.apply(get_combined_name, axis=1)
        latest_weights_df['현재 비중(%)'] = pd.to_numeric(latest_weights_df['포트폴리오내비중(%)'], errors='coerce').fillna(0.0)
        latest_weights_df['현재 평가액'] = pd.to_numeric(latest_weights_df['평가금액'].astype(str).str.replace(',','', regex=False), errors='coerce').fillna(0).astype(int)
        
        current_weights_grouped = latest_weights_df.groupby('종합 분류').agg({'현재 비중(%)': 'sum', '현재 평가액': 'sum'}).reset_index()
        current_weights_df = current_weights_grouped[current_weights_grouped['현재 비중(%)'] > 0].sort_values(by='현재 비중(%)', ascending=False)

        if not current_weights_df.empty:
            if not settings_df.empty: comparison_df = current_weights_df.merge(settings_df.set_index('종합 분류'), on='종합 분류', how='outer').fillna(0)
            else: comparison_df = current_weights_df.copy(); comparison_df['목표 비중(%)'] = 0.0

            for col in ['현재 비중(%)', '현재 평가액', '목표 비중(%)']:
                if col not in comparison_df.columns: comparison_df[col] = 0.0 if '%' in col else 0

            comparison_df['차이(%)'] = comparison_df['현재 비중(%)'] - comparison_df['목표 비중(%)']
            comparison_df['목표 금액'] = BASE_TOTAL_ASSET * (comparison_df['목표 비중(%)'] / 100)
            comparison_df['현금차이'] = comparison_df['현재 평가액'] - comparison_df['목표 금액']

            for col in ['현재 평가액', '목표 금액', '현금차이']:
                 if col in comparison_df.columns: comparison_df[col] = comparison_df[col].round(0).astype(int)

            comparison_df_final = comparison_df
        else:
             if not settings_df.empty:
                 comparison_df_final = settings_df.rename(columns={'목표 비중(%)':'목표 비중(%)'})
                 comparison_df_final['현재 비중(%)'] = 0.0; comparison_df_final['현재 평가액'] = 0; comparison_df_final['목표 금액'] = (BASE_TOTAL_ASSET * (comparison_df_final['목표 비중(%)'] / 100)).round(0).astype(int); comparison_df_final['차이(%)'] = -comparison_df_final['목표 비중(%)']; comparison_df_final['현금차이'] = -comparison_df_final['목표 금액']
             else: comparison_df_final = pd.DataFrame(columns=['종합 분류', '현재 비중(%)', '현재 평가액', '목표 비중(%)', '목표 금액', '차이(%)', '현금차이'])

    except Exception as e: st.error(f"자산 배분 데이터 로딩/처리 중 오류: {e}"); traceback.print_exc()

    final_cols_order = ['종합 분류', '현재 비중(%)', '현재 평가액', '목표 비중(%)', '목표 금액', '차이(%)', '현금차이']
    available_final_cols = [col for col in final_cols_order if col in comparison_df_final.columns]
    return comparison_df_final[available_final_cols], settings_df

import FinanceDataReader as fdr

# ... (기존 import 일부 생략, yfinance 제거)

# --- 지수 티커 설정 (FDR용) ---
KOSPI_TICKER = "KS200" # 코스피 200
SP500_TICKER = "US500" # S&P 500

# ... (중략)

@st.cache_data(ttl=3600)
def download_fdr_data(ticker, start_date, end_date):
    """FinanceDataReader 데이터 다운로드 (Naver Finance 기반)"""
    try:
        # 날짜 객체를 문자열로 변환
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        start_str = start_dt.strftime('%Y-%m-%d')
        end_str = end_dt.strftime('%Y-%m-%d')

        # FDR은 end 날짜를 포함하지 않거나 데이터 소스에 따라 다를 수 있음.
        # 안전하게 다운로드 후 index로 필터링하는 방식 권장될 수 있으나,
        # 기본적으로 start, end 인자 지원함.
        
        data = fdr.DataReader(ticker, start=start_str, end=end_str)
        
        if data is None or data.empty:
            st.warning(f"⚠️ {ticker} 데이터 다운로드 실패. (기간: {start_str} ~ {end_str})")
            return pd.DataFrame()
            
        if isinstance(data.index, pd.DatetimeIndex) and data.index.tz is not None:
             data.index = data.index.tz_localize(None)
             
        # FDR은 'Close' 컬럼을 기본으로 제공.
        return data
    except Exception as e: 
        st.error(f"{ticker} 데이터 다운로드 중 오류: {e}")
        return pd.DataFrame()

@st.cache_data
def calculate_index_twr(index_df, ticker):
    """주가 데이터프레임으로 TWR(%) 계산"""
    if index_df is None or index_df.empty or len(index_df) < 2: return pd.DataFrame()

    close_col_name = None
    if isinstance(index_df.columns, pd.MultiIndex):
        level_zero = index_df.columns.get_level_values(0)
        if 'Adj Close' in level_zero: close_col_name = [c for c in index_df.columns if c[0] == 'Adj Close'][0]
        elif 'Close' in level_zero: close_col_name = [c for c in index_df.columns if c[0] == 'Close'][0]
    elif 'Adj Close' in index_df.columns: close_col_name = 'Adj Close'
    elif 'Close' in index_df.columns: close_col_name = 'Close'

    if close_col_name is None: return pd.DataFrame()

    df = index_df[[close_col_name]].copy(); df.columns = ['Close']
    df = df.dropna().astype('float64')
    if df.empty: return pd.DataFrame()

    df = df.sort_index()
    df['StartValue'] = df['Close'].shift(1)
    df = df.iloc[1:].copy()
    
    denominator = df['StartValue']
    df['DailyFactor'] = 1.0
    valid_calc_mask = (denominator.abs() > 1e-9)
    df.loc[valid_calc_mask, 'DailyFactor'] = (df.loc[valid_calc_mask, 'Close'] / denominator.loc[valid_calc_mask])
    
    df['DailyFactor'] = df['DailyFactor'].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    df['DailyFactor'] = df['DailyFactor'].clip(lower=0.1, upper=10.0)

    df['CumulativeFactor'] = df['DailyFactor'].cumprod()
    df['TWR'] = (df['CumulativeFactor'] - 1.0) * 100.0
    
    # [FIX] 결과 DataFrame에 'Date' 컬럼이 반드시 존재하도록 인덱스 명시
    df.index.name = 'Date'
    return df[['TWR']].reset_index()

@st.cache_data(ttl=600)
def load_current_holdings(_gc, latest_data_date):
    """'일별비중_Raw' 시트에서 현재 보유 종목 목록 로드 (별도 파일 연결)"""
    if not isinstance(_gc, gspread.Client) or not isinstance(latest_data_date, pd.Timestamp): st.error("load_current_holdings: 유효한 gc 또는 latest_data_date 아님."); return pd.DataFrame(columns=['종목코드', '종목명'])
    
    WEIGHTS_FILE_NAME = '일별비중_RAW'
    WEIGHTS_SHEET_NAME = '일별비중_Raw'
    
    try:

        # [MODIFIED] 별도 파일 연결
        spreadsheet = safe_api_call(_gc.open, WEIGHTS_FILE_NAME)
        
        # [MODIFIED] Robust sheet matching
        found_ws = None
        worksheets_list = safe_api_call(spreadsheet.worksheets)
        for ws in worksheets_list:
            if ws.title.strip().lower() == WEIGHTS_SHEET_NAME.lower():
                found_ws = ws
                break
        
        if found_ws:
            weights_ws = found_ws
        else:
            raise gspread.exceptions.WorksheetNotFound(WEIGHTS_SHEET_NAME)
        
        weights_data = safe_api_call(weights_ws.get_all_records)
        holdings_df = pd.DataFrame(columns=['종목코드', '종목명'])
        if not weights_data: st.warning(f"'{WEIGHTS_SHEET_NAME}' 시트 데이터 없음."); return holdings_df

        weights_df = pd.DataFrame(weights_data); weights_df['날짜'] = pd.to_datetime(weights_df['날짜'], errors='coerce')
        latest_weights_df = weights_df[weights_df['날짜'] == latest_data_date].copy()
        if latest_weights_df.empty: st.warning(f"{latest_data_date.strftime('%Y-%m-%d')} 날짜의 비중 데이터 없음."); return holdings_df

        latest_weights_df['평가금액_num'] = pd.to_numeric(latest_weights_df['평가금액'].astype(str).str.replace(',','', regex=False), errors='coerce').fillna(0).astype(int)
        latest_weights_df['종목명_정리'] = latest_weights_df['종목명'].astype(str).str.replace(' ', '')

        holdings_df = latest_weights_df[latest_weights_df['평가금액_num'] > 0][['종목코드', '종목명_정리']].rename(columns={'종목명_정리':'종목명'}).drop_duplicates().sort_values(by='종목명').reset_index(drop=True)

        gold_mask = (holdings_df['종목명'] == '금현물') | (holdings_df['종목명'] == '금')
        code_missing_mask = holdings_df['종목코드'].isnull() | (holdings_df['종목코드'].astype(str).str.strip() == '')
        rows_to_update = gold_mask & code_missing_mask
        if rows_to_update.any():
            holdings_df.loc[rows_to_update, '종목코드'] = 'GOLD'

        return holdings_df
    except gspread.exceptions.SpreadsheetNotFound: st.error(f"일별비중 파일('{WEIGHTS_FILE_NAME}')을 찾을 수 없습니다."); return pd.DataFrame(columns=['종목코드', '종목명'])
    except gspread.exceptions.WorksheetNotFound: st.error(f"워크시트 '{WEIGHTS_SHEET_NAME}'를 찾을 수 없음."); return pd.DataFrame(columns=['종목코드', '종목명'])
    except Exception as e: st.error(f"보유 종목 목록 로딩 중 오류: {e}"); traceback.print_exc(); return pd.DataFrame(columns=['종목코드', '종목명'])

@st.cache_data(ttl=300)
def calculate_moving_avg_cost(_gc, stock_code, stock_name=None):
    """'🗓️매매일지' 시트에서 이동평균법으로 평단가 계산"""
    if not isinstance(_gc, gspread.Client): st.error("calculate_moving_avg_cost: 유효한 Google Sheets 클라이언트 객체(gc)가 아닙니다."); return 0.0
    if not stock_code: return 0.0
    final_avg_cost = 0.0
    TRADE_DATE_HEADER = '날짜'; TRADE_TYPE_HEADER = '매매구분'; TRADE_PRICE_HEADER = '단가'; TRADE_QTY_HEADER = '수량'; TRADE_CODE_HEADER = '종목코드'; TRADE_NAME_HEADER = '종목명'
    try:
        spreadsheet = safe_api_call(_gc.open, GOOGLE_SHEET_NAME); trades_ws = safe_api_call(spreadsheet.worksheet, TRADES_SHEET)
        all_trades_records = safe_api_call(trades_ws.get_all_records)
        if not all_trades_records: return final_avg_cost

        trades_df = pd.DataFrame(all_trades_records)
        trades_df['Date'] = pd.to_datetime(trades_df[TRADE_DATE_HEADER], errors='coerce')
        trades_df = trades_df.dropna(subset=['Date']).sort_values(by='Date')

        stock_code_str = str(stock_code).strip().upper().replace('KRX:', '').replace('A','')
        is_gold = (stock_code_str == 'GOLD')
        
        filtered_trades = []
        for idx, row in trades_df.iterrows():
            code_in_row = str(row.get(TRADE_CODE_HEADER, '')).strip().upper().replace('A','')
            name_in_row = str(row.get(TRADE_NAME_HEADER, '')).strip()
            
            match = False
            if is_gold:
                if code_in_row == 'GOLD': match = True
                elif '금현물' in name_in_row or '금99' in name_in_row: match = True
            else:
                 if code_in_row == stock_code_str: match = True
                 elif stock_name and name_in_row.replace(' ', '') == stock_name.replace(' ', ''): match = True
            
            if match: filtered_trades.append(row)
            
        if not filtered_trades: return 0.0
        
        current_qty = 0; current_total_cost = 0
        
        for row in filtered_trades:
            trade_type = str(row.get(TRADE_TYPE_HEADER, '')).strip()
            try: qty = float(str(row.get(TRADE_QTY_HEADER,0)).replace(',',''))
            except: qty = 0
            try: price = float(str(row.get(TRADE_PRICE_HEADER,0)).replace(',',''))
            except: price = 0
            
            if qty == 0: continue
            
            if '매수' in trade_type or 'BUY' in trade_type.upper():
                current_total_cost += qty * price
                current_qty += qty
            elif '매도' in trade_type or 'SELL' in trade_type.upper():
                if current_qty > 0:
                    avg_price = current_total_cost / current_qty
                    current_total_cost -= qty * avg_price
                    current_qty -= qty
                    if current_qty < 0: current_qty = 0; current_total_cost = 0
        
        if current_qty > 0: final_avg_cost = current_total_cost / current_qty
        else: final_avg_cost = 0.0
            
    except Exception as e:
         print(f"Log: 평단가 계산 오류: {e}")
         return 0.0
    return float(final_avg_cost)

@st.cache_data(ttl=3600)
def get_first_purchase_date(_gc, stock_code, stock_name=None):
    """'🗓️매매일지' 시트에서 최초 매수일 찾기"""
    if not isinstance(_gc, gspread.Client): return None
    if not stock_code: return None
    first_date = None
    TRADE_DATE_HEADER = '날짜'; TRADE_TYPE_HEADER = '매매구분'; TRADE_CODE_HEADER = '종목코드'; TRADE_NAME_HEADER = '종목명'
    try:
        spreadsheet = safe_api_call(_gc.open, GOOGLE_SHEET_NAME); trades_ws = safe_api_call(spreadsheet.worksheet, TRADES_SHEET)
        all_trades_records = safe_api_call(trades_ws.get_all_records)
        if not all_trades_records: return None

        trades_df = pd.DataFrame(all_trades_records)
        trades_df['Date'] = pd.to_datetime(trades_df[TRADE_DATE_HEADER], errors='coerce')
        trades_df = trades_df.dropna(subset=['Date'])

        stock_code_str = str(stock_code).strip().upper().replace('KRX:', '').replace('A','')
        is_gold = (stock_code_str == 'GOLD')
        
        for idx, row in trades_df.iterrows():
            code_in_row = str(row.get(TRADE_CODE_HEADER, '')).strip().upper().replace('A','')
            name_in_row = str(row.get(TRADE_NAME_HEADER, '')).strip()
            
            match = False
            if is_gold:
                 if code_in_row == 'GOLD': match = True
                 elif '금현물' in name_in_row or '금99' in name_in_row: match = True
            else:
                 if code_in_row == stock_code_str: match = True
                 elif stock_name and name_in_row.replace(' ', '') == stock_name.replace(' ', ''): match = True
            
            if match:
                 t_date = row['Date']
                 if first_date is None or t_date < first_date:
                     first_date = t_date
                     
    except Exception as e:
        print(f"Log: 최초 매수일 찾기 오류: {e}")
        return None
        
    return first_date



def get_fdr_ticker(stock_code):
    """종목코드를 FinanceDataReader 티커 형식으로 변환"""
    code = str(stock_code).strip()
    if code == 'GOLD': return None
    if code.startswith('KRX:'): code_only = code.split(':')[-1]
    elif code.startswith('A') and code[1:].isdigit(): code_only = code[1:]
    else: code_only = code
    
    # FDR은 한국 주식의 경우 종목코드(6자리)만 있으면 됨 (예: '005930')
    if code_only.isdigit() and len(code_only) == 6: return code_only
    elif code_only.isalnum() or '.' in code_only: return code_only.upper()
    else: return code_only

@st.cache_data(ttl=600)
def load_gold_price_data(_gc):
    """📈금현물 수익률 시트에서 날짜(A열)와 금가격(J열)을 로드합니다."""
    if not isinstance(_gc, gspread.Client): return pd.DataFrame()
    DATE_COL = 1; PRICE_COL = 10
    try:
        spreadsheet = safe_api_call(_gc.open, GOOGLE_SHEET_NAME); worksheet = safe_api_call(spreadsheet.worksheet, GOLD_RATE_SHEET)
        data = safe_api_call(worksheet.get_all_values)
        if len(data) < 2: return pd.DataFrame()

        header = data[0]; records = data[1:]
        dates = []; prices = []
        for row in records:
            if len(row) >= PRICE_COL:
                date_str = row[DATE_COL-1]; price_str = row[PRICE_COL-1]
                dt_obj = pd.to_datetime(date_str, errors='coerce')
                if pd.notna(dt_obj):
                    dates.append(dt_obj)
                    prices.append(clean_numeric_value(price_str, float))

        if not dates: return pd.DataFrame()
        df = pd.DataFrame({'Date': dates, 'Close': prices}).set_index('Date').sort_index()
        return df
    except Exception as e: st.error(f"금 가격 데이터 로딩 중 오류 발생: {e}"); traceback.print_exc(); return pd.DataFrame()


# --- 데이터 로드 (메인 실행) ---
with st.spinner('데이터를 불러오는 중입니다...'):
    gc = connect_google_sheets()

    if gc:
        # 수정된 부분: gc 객체를 전달하여 구글 시트에서 데이터 로드
        twr_data_df = load_twr_data(gc)
        daily_values_df = load_daily_values(gc)
        gain_loss_data = load_gain_loss_data(gc)
        
        
        # [NEW] 매매일지 한 번에 로드 (Bulk Load) - REVERTED
        # all_trades_df = load_all_trades_data(gc)
        
        # [MODIFIED] latest_balances, latest_data_date 유도 (daily_values_df 사용)
        # load_latest_balances 함수 제거됨
        
        latest_balances = {}
        latest_data_date = None
        
        if not daily_values_df.empty:
            # daily_values_df: ['Date', 'Account', 'Value'?] 
            # Note: load_daily_values combines sheets. Check structure.
            # It loads sheets named 'Total', 'ISA', etc.
            # Columns: Date, Account, ... (content of sheet)
            
            # Find max date
            latest_data_date = daily_values_df['Date'].max()
            
            # Extract latest values for "Total Asset" calculation
            # We need values for each account at latest_data_date
            # Filter by date
            latest_df = daily_values_df[daily_values_df['Date'] == latest_data_date]
            
            # create balances dict: {Account: Value}
            # Note: daily_values_df value column might be named 'Value' or something else?
            # load_daily_values logic:
            # df = pd.DataFrame(data) -> df['Date'] = ... df['Account'] = acc
            # If sheet has 'Value', it's fine.
            # The '성과_자산추이_Raw' sheets usually have 'Date' and 'Value' (from update_closing_prices/portfolio_perf logic).
            
            # Check column names
            val_col = 'Value'
            if 'Value' not in latest_df.columns:
                 # Try finding numeric column
                 nums = latest_df.select_dtypes(include=[np.number]).columns
                 if len(nums) > 0: val_col = nums[0]
            
            if val_col in latest_df.columns:
                for _, row in latest_df.iterrows():
                    acc = row['Account']
                    val = row[val_col]
                    latest_balances[acc] = val
                    
    else:
        st.error("Google Sheets에 연결할 수 없어 데이터를 로드할 수 없습니다.")
        twr_data_df = pd.DataFrame(); gain_loss_data = {}
        latest_balances = {}; latest_data_date = None
        daily_values_df = pd.DataFrame()
        allocation_comparison_df, target_allocation_df = pd.DataFrame(), pd.DataFrame()

    # Asset Allocation Load (using latest_data_date derived above)
    if gc and latest_data_date:
        allocation_comparison_df, target_allocation_df = load_allocation_data(gc, latest_data_date)
        if not allocation_comparison_df.empty and '현재 평가액' in allocation_comparison_df.columns:
             allocation_comparison_df = allocation_comparison_df.sort_values(by='현재 평가액', ascending=False).reset_index(drop=True)
    else:
        allocation_comparison_df, target_allocation_df = pd.DataFrame(), pd.DataFrame()
        if gc: st.warning("최신 데이터 기준일을 가져올 수 없어 자산 배분 정보를 로드할 수 없습니다.")

# --- 대시보드 UI 시작 ---

if latest_data_date: st.caption(f"데이터 기준일: {latest_data_date.strftime('%Y년 %m월 %d일')}")
else: st.caption("데이터 기준일을 불러올 수 없습니다.")

# --- 개요 (Overview) 섹션 ---
st.header("📊 개요")
col1, col2, col3, col4 = st.columns(4)

# 1. 값 계산
# Total Asset (Overview Total)
# If 'Total' account exists in latest_balances, use it. Otherwise sum others.
total_asset = 0
if 'Total' in latest_balances:
    total_asset = float(latest_balances['Total'])
elif latest_balances:
    total_asset = sum(float(v) for k, v in latest_balances.items() if k != 'Total')

# TWR
latest_twr = "N/A"
if not twr_data_df.empty and 'Account' in twr_data_df.columns:
    # ... logic continues
    if 'Total' in twr_data_df['Account'].unique():
        total_twr_series = twr_data_df[twr_data_df['Account'] == 'Total'].sort_values(by='Date', ascending=False)
        if not total_twr_series.empty:
            latest_twr_value = total_twr_series['TWR'].iloc[0]
            latest_twr = f"{latest_twr_value:.2f}%" if pd.notna(latest_twr_value) else "N/A"

@st.cache_data(ttl=600)
def calculate_total_principal(_gc):
    """
    각 계좌 시트('📈ISA 수익률', '📈IRP 수익률', '📈연금 수익률', '📈금현물 수익률')의 
    '입금'(B열) 값을 모두 합산하여 총 원금을 계산합니다.
    """
    if not isinstance(_gc, gspread.Client): return 0
    
    ACCOUNT_SHEET_MAP = {
        'ISA': '📈ISA 수익률',
        'IRP': '📈IRP 수익률',
        '연금': '📈연금 수익률',
        '금현물': '📈금현물 수익률'
    }
    DEPOSIT_COL_IDX = 1 # B열 (0-based index: 1)
    
    total_principal = 0
    
    try:
        spreadsheet = _gc.open(GOOGLE_SHEET_NAME)
        for acc, sheet_name in ACCOUNT_SHEET_MAP.items():
            try:
                ws = spreadsheet.worksheet(sheet_name)
                # B열(입금) 가져오기
                deposit_values = ws.col_values(DEPOSIT_COL_IDX + 1) # B열
                
                # 헤더 제외하고 합산
                current_acc_sum = 0
                for val in deposit_values[1:]:
                     current_acc_sum += clean_numeric_value(val, float)
                    
                total_principal += current_acc_sum
                # print(f"Log: {acc} 원금 합산: {current_acc_sum:,.0f}")
            except gspread.exceptions.WorksheetNotFound:
                continue
            except Exception as e:
                print(f"Log: {acc} 원금 계산 중 오류: {e}")
                
    except Exception as e:
        print(f"Log: 총 원금 계산 실패: {e}")
        return 0
        
    return total_principal

# 2. 화면 표시 (순서 변경: 원금 -> 평가액 -> TWR -> 손익)
# [MODIFIED] 원금 계산 방식 변경 (입금액 합산)
calculated_principal = calculate_total_principal(gc)
total_principal_str = f"{calculated_principal:,.0f} 원"

# 손익 재계산 (총자산 - 원금)
# [FIX] 초기값 설정 (API 에러 시 NameError 방지)
total_gain_loss_str = "0 원"

if latest_balances:
    recalc_gain_loss = total_asset - calculated_principal
    if calculated_principal != 0:
        return_rate = (recalc_gain_loss / calculated_principal) * 100
        total_gain_loss_str = f"{recalc_gain_loss:,.0f} 원 ({return_rate:+.2f}%)"
    else:
        total_gain_loss_str = f"{recalc_gain_loss:,.0f} 원"


# 2. 화면 표시 (순서 변경: 원금 -> 평가액 -> TWR -> 손익)
col1.metric("💼 원금 (추정)", total_principal_str)
col2.metric("💰 총 평가액", f"{total_asset:,.0f} 원")
col3.metric("📈 전체 TWR (기간)", latest_twr)
col4.metric("💸 전체 단순 손익 (기간)", total_gain_loss_str)
st.markdown("---")



# --- 자산 배분 섹션 ---
st.header("⚖️ 자산 배분")
if allocation_comparison_df is not None and not allocation_comparison_df.empty:
    st.subheader("현재 vs 목표 비중 비교")
    df_to_display = allocation_comparison_df.copy()
    
    total_target_amount = 0; target_amount_col_original = '목표 금액'; new_target_amount_header = target_amount_col_original
    if target_amount_col_original in df_to_display.columns:
        try:
            numeric_target_amounts = pd.to_numeric(df_to_display[target_amount_col_original], errors='coerce').fillna(0)
            total_target_amount = int(numeric_target_amounts.sum())
            new_target_amount_header = f"{target_amount_col_original} (총 {total_target_amount:,.0f} 원)"
        except Exception: pass

    formats = {
        '현재 비중(%)': '{:.2f}%', '목표 비중(%)': '{:.2f}%', '차이(%)': '{:+.2f}%',
        '현재 평가액': '{:,.0f} 원', target_amount_col_original: '{:,.0f} 원', '현금차이': '{:+,d} 원'
    }
    
    if new_target_amount_header != target_amount_col_original and target_amount_col_original in df_to_display.columns:
        df_to_display.rename(columns={target_amount_col_original: new_target_amount_header}, inplace=True)
        formats[new_target_amount_header] = formats.pop(target_amount_col_original)

    original_display_cols = ['종합 분류', '목표 비중(%)', '현재 비중(%)', '차이(%)', target_amount_col_original, '현재 평가액', '현금차이']
    display_cols_final = [];
    for col in original_display_cols:
        if col == target_amount_col_original and new_target_amount_header != target_amount_col_original:
             if new_target_amount_header in df_to_display.columns: display_cols_final.append(new_target_amount_header)
        elif col in df_to_display.columns: display_cols_final.append(col)

    available_formats = {k: v for k, v in formats.items() if k in display_cols_final}

    if display_cols_final:
        sort_col = '목표 비중(%)' if '목표 비중(%)' in display_cols_final else ('종합 분류' if '종합 분류' in display_cols_final else None)
        if sort_col and sort_col == '목표 비중(%)' and not pd.api.types.is_numeric_dtype(df_to_display[sort_col]): sort_col = '종합 분류'
        if sort_col: df_display_final = df_to_display[display_cols_final].sort_values(by=sort_col, ascending=False).reset_index(drop=True)
        else: df_display_final = df_to_display[display_cols_final]
        st.dataframe(df_display_final.style.format(available_formats).set_properties(**{'text-align': 'center'}))
    else: st.warning("비교 테이블에 표시할 컬럼이 부족합니다.")

    st.subheader("자산 배분 시각화 (종합 분류 기준)")
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        if '현재 비중(%)' in allocation_comparison_df.columns:
             current_display_df = allocation_comparison_df[allocation_comparison_df['현재 비중(%)'] > 0].copy()
             if not current_display_df.empty:
                 fig_current = px.pie(current_display_df, values='현재 비중(%)', names='종합 분류', title='현재 자산 배분', hole=.4, color_discrete_sequence=px.colors.sequential.RdBu)
                 fig_current.update_traces(textposition='outside', textinfo='percent+label', insidetextorientation='radial', sort=False)
                 fig_current.update_layout(showlegend=False, margin=dict(l=40, r=40, t=50, b=40))
                 st.plotly_chart(fig_current, use_container_width=True)
             else: st.info("현재 보유 자산 비중 정보가 없습니다 (0% 초과).")
    with col_chart2:
        if target_allocation_df is not None and not target_allocation_df.empty and '목표 비중(%)' in target_allocation_df.columns:
             target_display_df = target_allocation_df[target_allocation_df['목표 비중(%)'] > 0].copy()
             if not target_display_df.empty:
                 fig_target = px.pie(target_display_df, values='목표 비중(%)', names='종합 분류', title='목표 자산 배분', hole=.4, color_discrete_sequence=px.colors.sequential.RdBu)
                 fig_target.update_traces(textposition='outside', textinfo='percent+label', insidetextorientation='radial', sort=False)
                 fig_target.update_layout(showlegend=False, margin=dict(l=40, r=40, t=50, b=40))
                 st.plotly_chart(fig_target, use_container_width=True)
             else: st.info("목표 자산 배분 정보가 없습니다 (0% 초과).")

elif allocation_comparison_df is None: st.error("자산 배분 데이터 로딩 실패 또는 기준 날짜 데이터 없음.")
else: st.info("표시할 자산 배분 정보가 없습니다.")
st.markdown("---")

# --- 성과 분석 섹션 ---
st.header("📈 성과 분석")
if twr_data_df is not None and not twr_data_df.empty:
    st.subheader("📊 시간가중수익률(TWR) 추이")
    st.markdown("#### 전체 포트폴리오 TWR 및 시장 지수 비교")
    total_twr_df = twr_data_df[twr_data_df['Account'] == 'Total'].sort_values(by='Date')
    if not total_twr_df.empty:
        start_date = total_twr_df['Date'].min(); end_date = total_twr_df['Date'].max()
        kospi_raw_data = download_fdr_data(KOSPI_TICKER, start_date, end_date)
        sp500_raw_data = download_fdr_data(SP500_TICKER, start_date, end_date)
        kospi_twr_df = calculate_index_twr(kospi_raw_data, KOSPI_TICKER)
        sp500_twr_df = calculate_index_twr(sp500_raw_data, SP500_TICKER)
        
        fig_total_compare = go.Figure()
        fig_total_compare.add_trace(go.Scatter(x=total_twr_df['Date'], y=total_twr_df['TWR'], mode='lines', name='전체 포트폴리오', line=dict(color='royalblue', width=2.5)))
        if kospi_twr_df is not None and not kospi_twr_df.empty:
            fig_total_compare.add_trace(go.Scatter(x=kospi_twr_df['Date'], y=kospi_twr_df['TWR'], mode='lines', name='KOSPI 200', line=dict(color='tomato', width=1.5, dash='dot')))
        if sp500_twr_df is not None and not sp500_twr_df.empty:
            fig_total_compare.add_trace(go.Scatter(x=sp500_twr_df['Date'], y=sp500_twr_df['TWR'], mode='lines', name='S&P 500', line=dict(color='mediumseagreen', width=1.5, dash='dot')))
        fig_total_compare.update_layout(title='전체 포트폴리오 TWR 및 시장 지수 비교', xaxis_title='날짜', yaxis_title='TWR (%)', legend_title='구분', hovermode="x unified")
        st.plotly_chart(fig_total_compare, use_container_width=True)
    else: st.info("전체 TWR 데이터가 없습니다.")

    st.markdown("#### 계좌별 TWR")
    account_twr_df = twr_data_df[twr_data_df['Account'] != 'Total'].sort_values(by=['Account', 'Date'])
    if not account_twr_df.empty:
        fig_accounts_twr = px.line(account_twr_df, x='Date', y='TWR', color='Account', title='계좌별 TWR 추이', labels={'TWR': 'TWR (%)'})
        fig_accounts_twr.update_layout(xaxis_title='날짜', yaxis_title='수익률 (%)', hovermode="x unified")
        st.plotly_chart(fig_accounts_twr, use_container_width=True)
    else: st.info("개별 계좌 TWR 데이터가 없습니다.")

    # 3. 전체 포트폴리오 TWR vs MWR (행동 격차) 그래프 추가
    st.markdown("#### ⚖️ 전체 포트폴리오 TWR vs MWR 비교 (행동 격차)")
    
    total_profit_df = load_historical_total_profit(gc)
    
    if (not total_twr_df.empty) and (daily_values_df is not None) and (not total_profit_df.empty):
        try:
            total_val_df = daily_values_df[daily_values_df['Account'] == 'Total'].copy()
            total_val_df = total_val_df.rename(columns={'Value': 'Valuation'})
            
            m1 = pd.merge(total_twr_df[['Date', 'TWR']], total_val_df[['Date', 'Valuation']], on='Date', how='inner')
            m2 = pd.merge(m1, total_profit_df[['Date', 'Profit']], on='Date', how='inner')
            
            if not m2.empty:
                m2['Valuation'] = pd.to_numeric(m2['Valuation'], errors='coerce')
                m2['Profit'] = pd.to_numeric(m2['Profit'], errors='coerce')
                m2['TWR'] = pd.to_numeric(m2['TWR'], errors='coerce')
                
                # MWR = Profit / (Valuation - Profit) * 100
                # 분모가 0 이하가 되는 것 방지
                valid_mwr = (m2['Valuation'] - m2['Profit']).abs() > 1e-9
                m2.loc[valid_mwr, 'MWR'] = (m2.loc[valid_mwr, 'Profit'] / (m2.loc[valid_mwr, 'Valuation'] - m2.loc[valid_mwr, 'Profit'])) * 100
                m2['MWR'] = m2['MWR'].fillna(0.0)
                m2 = m2.sort_values(by='Date')
                
                # Plot
                fig_mwr_compare = go.Figure()
                fig_mwr_compare.add_trace(go.Scatter(x=m2['Date'], y=m2['TWR'], mode='lines', name='시간가중수익률 (TWR, 자산배분 성과)', line=dict(color='royalblue', width=2.5)))
                fig_mwr_compare.add_trace(go.Scatter(x=m2['Date'], y=m2['MWR'], mode='lines', name='금액가중수익률 (MWR, 실제 지갑 성과)', line=dict(color='darkorange', width=2.5)))
                
                latest_row = m2.iloc[-1]
                gap = latest_row['MWR'] - latest_row['TWR']
                gap_sign = "+" if gap >= 0 else ""
                
                fig_mwr_compare.update_layout(
                    title=f"전체 포트폴리오 TWR vs MWR 추이 (최신 행동격차: {gap_sign}{gap:.2f}%)",
                    xaxis_title='날짜',
                    yaxis_title='수익률 (%)',
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    hovermode="x unified"
                )
                st.plotly_chart(fig_mwr_compare, use_container_width=True)
                
                if gap >= 0:
                    st.success(f"🎉 **긍정적인 행동 격차(Timing Alpha): {gap_sign}{gap:.2f}%**  \n시장 하락기나 조정 국면에서 자금을 효과적으로 추가 투입하여, 자산배분 기본 수익률보다 실제 계좌가 더 빠르게 늘어났습니다! 대단한 투자 의사결정입니다.")
                else:
                    st.warning(f"⚠️ **행동 격차(Behavior Gap): {gap:.2f}%**  \n포트폴리오 자체 수익률보다 실제 지갑 수익률이 약간 낮습니다. 이는 주로 급등장에서 매입을 몰아서 했거나 하락장에서 현금을 투입하지 못했을 때 생기는 현상입니다. 분할 적립식 자동 납입을 고려해보세요!")
            else:
                st.info("TWR/MWR 비교를 위한 공통 날짜 데이터가 부족합니다.")
        except Exception as e:
            st.error(f"TWR vs MWR 비교 차트 생성 중 오류: {e}")
    else:
        st.info("TWR vs MWR 비교를 위한 원천 데이터가 부족합니다.")

    # [NEW] 일별 총 자산 추이 그래프 (위치 이동됨)
    # --- 월별 총 자산 추이 섹션 ---
    if daily_values_df is not None and not daily_values_df.empty:
        st.markdown("#### 📅 월별 총 자산 추이")
        
        # [수정] 미래 데이터 원천 차단 (현재 시간 기준)
        # 1. 원본 데이터 필터링
        current_ts = pd.Timestamp.now()
        daily_values_df['Date'] = pd.to_datetime(daily_values_df['Date']) # 확인 사살
        daily_values_df = daily_values_df[daily_values_df['Date'] <= current_ts]
        
        # 1. 데이터 가공 (월말 기준 -> 월초 기준으로 변경하여 날짜 필터링 용이하게 함)
        daily_pivot = daily_values_df.pivot(index='Date', columns='Account', values='Value')
        
        # 리샘플링: 'MS' (월초, 1일)
        monthly_df = daily_pivot.resample('MS').last()
        
        # [수정] 명확하게 "이번 달 1일"까지만 허용 (이후 날짜는 모두 제거)
        # 예: 오늘이 1월 10일 -> current_month_start = 1월 1일.
        #     1월 데이터(1월 1일) <= 1월 1일 (남음)
        #     2월 데이터(2월 1일) <= 1월 1일 (거짓 -> 제거)
        current_month_start = current_ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_df = monthly_df[monthly_df.index <= current_month_start]
        
        # 'Total' 컬럼 제외하고 개별 계좌만 스택킹 (Total은 검증용)
        if not monthly_df.empty:
            # 데이터프레임 정리 (Total 제거, 0 채우기)
            display_cols = [c for c in monthly_df.columns if c != 'Total']
            display_df = monthly_df[display_cols].fillna(0)
            
            # Plotly Stacked Bar Chart
            # 계좌별 색상 정의 (사용자 요청: ISA 연한 파랑, IRP 진한 파랑, 나머지 유지)
            colors = {
                'ISA': '#87CEEB',       # SkyBlue (연한 파랑)
                'IRP': '#00008B',       # DarkBlue (진한 파랑)
                '연금': '#228B22',      # ForestGreen (유지)
                '금현물': '#9ACD32',    # YellowGreen (유지)
            }
            
            fig_monthly = go.Figure()
            for col in display_df.columns:
                color_val = colors.get(col, None) # None lets Plotly decide loop color if missing
                fig_monthly.add_trace(go.Bar(
                    x=display_df.index,
                    y=display_df[col],
                    name=col,
                    marker_color=color_val
                ))
            
            # [NEW] 상단에 총액 텍스트 표시 (go.Scatter w/ text)
            # 1. 총액 계산
            total_values = display_df.sum(axis=1)
            
            # 2. 포맷팅 함수 (X억 X천)
            def format_large_krw(val):
                if val < 0: return "-"
                eok = int(val // 100000000)
                remain = val % 100000000
                cheon = int(remain // 10000000) # 천만 단위
                
                if eok > 0:
                    if cheon > 0: return f"{eok}억{cheon}천"
                    else: return f"{eok}억"
                else:
                    if cheon > 0: return f"{cheon}천"
                    else: return "" # 1천만원 미만은 표시 생략 or 그냥 숫자? (요청: X억X천)

            text_labels = total_values.apply(format_large_krw)

            # 3. 텍스트 트레이스 추가
            fig_monthly.add_trace(go.Scatter(
                x=display_df.index,
                y=total_values, # 막대 꼭대기 좌표
                text=text_labels,
                mode='text',
                textposition='top center',
                name='총액',
                showlegend=False,
                textfont=dict(size=12, color='black') # 가독성 위해 검은색
            ))
            
            # Y축 범위 조정 (텍스트 잘림 방지: Max값의 1.1배)
            if not total_values.empty:
                max_val = total_values.max()
                fig_monthly.update_yaxes(range=[0, max_val * 1.15])

            fig_monthly.update_layout(
                barmode='stack',
                title='월별 총 자산 (원금 + 평가손익) 구성 (당월 1일 기준)',
                xaxis_title='날짜 (월)',
                yaxis_title='평가금액 (원)',
                hovermode="x unified",
                xaxis=dict(
                    dtick="M1",
                    tickformat="%Y-%m"
                )
            )
            st.plotly_chart(fig_monthly, use_container_width=True)
        else:
            st.info("월별 데이터로 변환할 수 없습니다.")

    st.markdown("---"); st.subheader("📈 종목별 가격/주가 및 평단가 (이동평균법)")
    if gc and latest_data_date:
        holdings_list_df = load_current_holdings(gc, latest_data_date)
        if holdings_list_df is not None and not holdings_list_df.empty:
            stock_options = holdings_list_df.set_index('종목명')['종목코드'].to_dict()
            stock_names = ["종목을 선택하세요..."] + list(holdings_list_df['종목명'])
            default_index = 0; default_stock_name = "TIGER미국S&P500"
            try:
                 if default_stock_name in stock_names: default_index = stock_names.index(default_stock_name)
            except ValueError: pass
            selected_stock_name = st.selectbox("종목 선택:", stock_names, index=default_index)

            if selected_stock_name != "종목을 선택하세요...":
                stock_code = stock_options.get(selected_stock_name)
                if stock_code:
                    # [Reverted] Legacy functions call with gc (API call per stock)
                    avg_cost = calculate_moving_avg_cost(gc, stock_code, selected_stock_name)
                    first_purchase_dt = get_first_purchase_date(gc, stock_code, selected_stock_name)

                    if first_purchase_dt:
                        chart_start_date = first_purchase_dt.date()
                        current_date = datetime.now().date()
                        close_price_df = pd.DataFrame()
                        plot_title = f"{selected_stock_name}"
                        yaxis_title = '가격 (KRW)'

                        if stock_code == 'GOLD':
                            st.info(f"'{selected_stock_name}' 가격 데이터를 구글 시트 '{GOLD_RATE_SHEET}'에서 로드합니다.")
                            gold_price_history = load_gold_price_data(gc)
                            if gold_price_history is not None and not gold_price_history.empty:
                                gold_data_filtered = gold_price_history[gold_price_history.index.date >= chart_start_date]
                                if not gold_data_filtered.empty:
                                    close_price_df = gold_data_filtered
                                    plot_title = f"{selected_stock_name} 가격 추이 및 평단가 (KRW/g)"
                                    yaxis_title = '가격 (KRW/g)'
                        else:
                            fdr_ticker = get_fdr_ticker(stock_code)
                            if not fdr_ticker:
                                if avg_cost > 0: st.metric(label=f"{selected_stock_name} 평단가 (이동평균)", value=f"{avg_cost:,.0f} 원")
                                else: st.metric(label=f"{selected_stock_name} 평단가 (이동평균)", value="계산 불가")
                            else:
                                stock_price_data = download_fdr_data(fdr_ticker, chart_start_date, current_date)
                                if stock_price_data is not None and not stock_price_data.empty:
                                    close_col_found = None
                                    if isinstance(stock_price_data.columns, pd.MultiIndex):
                                        level_zero = stock_price_data.columns.get_level_values(0)
                                        if 'Adj Close' in level_zero: close_col_found = [c for c in stock_price_data.columns if c[0] == 'Adj Close'][0]
                                        elif 'Close' in level_zero: close_col_found = [c for c in stock_price_data.columns if c[0] == 'Close'][0]
                                    elif 'Adj Close' in stock_price_data.columns: close_col_found = 'Adj Close'
                                    elif 'Close' in stock_price_data.columns: close_col_found = 'Close'

                                    if close_col_found:
                                        temp_df = stock_price_data[[close_col_found]].copy(); temp_df.columns = ['Close']
                                        close_price_df = temp_df.dropna(subset=['Close'])
                                        plot_title = f"{selected_stock_name} ({fdr_ticker}) 주가 추이 및 평단가"
                                        
                        if not close_price_df.empty:
                            fig_stock = go.Figure()
                            fig_stock.add_trace(go.Scatter(x=close_price_df.index, y=close_price_df['Close'], mode='lines', name='종가/가격', line=dict(color='skyblue', width=2)))
                            if avg_cost > 0:
                                avg_cost_format = "{:,.2f}" if stock_code == 'GOLD' else "{:,.0f}"
                                fig_stock.add_hline(y=avg_cost, line_dash="dot", line_color="tomato",
                                                    annotation_text=f"평단가: {avg_cost_format.format(avg_cost)}",
                                                    annotation_position="bottom right")
                            fig_stock.update_layout(title=plot_title, xaxis_title='날짜', yaxis_title=yaxis_title, yaxis=dict(showticklabels=True, autorange=True), hovermode="x unified")
                            st.plotly_chart(fig_stock, use_container_width=True)
                    elif gc: st.warning(f"{selected_stock_name}의 매수 기록을 '{TRADES_SHEET}' 시트에서 찾을 수 없어 그래프를 그릴 수 없습니다.")
                else: st.error("선택된 종목의 코드를 찾을 수 없습니다.")
        elif holdings_list_df is None: st.error("현재 보유 중인 종목 정보를 불러오는 데 실패했습니다.")
        else: st.info("현재 보유 중인 종목 정보가 없습니다. ('일별비중_Raw' 시트 확인 필요)")
    else: st.info("구글 시트 연결 실패 또는 최신 데이터 날짜가 없어 보유 종목을 조회할 수 없습니다.")

if twr_data_df is None: st.error("성과 분석 데이터 로딩 실패.")
# else: st.warning("성과 분석을 위한 TWR 데이터가 없습니다 (구글 시트 확인 필요).") # 불필요한 경고면 주석 처리 또는 logic 수정

st.markdown("---")

# --- 📝 데이터 입력 (Data Entry) 섹션 제거됨 (별도 앱으로 분리) ---
