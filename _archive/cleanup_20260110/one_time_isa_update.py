
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import numpy as np
import os
import sys
import time

# Encoding for Windows
sys.stdout.reconfigure(encoding='utf-8')

# Config
JSON_KEYFILE_PATH = 'stock-auto-writer-44eaa06c140c.json'
GOOGLE_SHEET_NAME = 'KYI_자산배분'
QTY_FILE = '수량_RAW'
PRICE_FILE = '종가_RAW'
TARGET_SHEET = '📈ISA 수익률'
SETTING_SHEET = '⚙️설정'

def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        print("✅ 구글 시트 연결 성공")
        return gc
    except Exception as e:
        print(f"❌ 접속 실패: {e}")
        return None

def main():
    gc = connect_google_sheets()
    if not gc: return

    print("🚀 ISA 평가액 일괄 재계산 및 업데이트 시작...")

    # 1. 맵핑 정보 로드 (종목명 -> 종목코드)
    print("📥 종목 매핑 정보 로드 중...")
    try:
        sh = gc.open(GOOGLE_SHEET_NAME)
        setting_ws = sh.worksheet(SETTING_SHEET)
        settings = setting_ws.get_all_values()
        
        name_map = {} # Name -> Code (clean)
        for row in settings[1:]:
            if len(row) >= 18:
                name = row[16].strip()
                code = row[17].strip().replace('KRX:', '')
                if name and code:
                    name_map[name] = code
        print(f"  - 매핑 로드 완료 ({len(name_map)}개)")
    except Exception as e:
        print(f"❌ 설정 로드 실패: {e}")
        return

    # 2. 수량 데이터 로드
    print(f"📥 '{QTY_FILE}' 로드 중...")
    try:
        qty_sh = gc.open(QTY_FILE)
        # 수량 시트 이름 찾기 (보통 '수량')
        qty_ws = qty_sh.worksheets()[0] # 첫번째 시트 가정 또는 '수량'
        for s in qty_sh.worksheets():
            if '수량' in s.title: qty_ws = s; break
            
        qty_data = qty_ws.get_all_values()
        qty_df = pd.DataFrame(qty_data[1:], columns=qty_data[0])
        qty_df['Date'] = pd.to_datetime(qty_df['Date'], errors='coerce')
        qty_df = qty_df.dropna(subset=['Date']).sort_values('Date').set_index('Date')
        
        # 숫자 변환
        for col in qty_df.columns:
            qty_df[col] = pd.to_numeric(qty_df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            
        print(f"  - 수량 데이터: {len(qty_df)}일치")
    except Exception as e:
        print(f"❌ 수량 데이터 로드 실패: {e}")
        return

    # 3. 종가 데이터 로드
    print(f"📥 '{PRICE_FILE}' 로드 중...")
    try:
        price_sh = gc.open(PRICE_FILE)
        price_ws = price_sh.worksheets()[0]
        for s in price_sh.worksheets():
            if '종가' in s.title or 'Price' in s.title: price_ws = s; break
        
        price_data = price_ws.get_all_values()
        
        # Header Parsing Logic based on inspection:
        # Row 1 (idx 0): Names (Corrupted 'Date', 'Tiger...')
        # Row 2 (idx 1): Codes ('', 'KRX:xxxx', ...)
        # Row 3 (idx 2): Data start
        
        if len(price_data) < 3:
            print("❌ 종가 데이터가 부족합니다.")
            return

        # Use 2nd row (index 1) as Ticker Headers
        raw_headers = price_data[1] 
        
        # Treat first column as Date
        headers = ['Date'] + raw_headers[1:]
        
        # Clean Headers (Tickers)
        clean_headers = []
        for h in headers:
            h = str(h).strip()
            if 'KRX:' in h: h = h.replace('KRX:', '')
            clean_headers.append(h)
            
        # Create DataFrame from Row 3 (index 2)
        price_df = pd.DataFrame(price_data[2:], columns=clean_headers)
        
        # Set Date Index
        price_df['Date'] = pd.to_datetime(price_df['Date'], errors='coerce')
        price_df = price_df.dropna(subset=['Date']).set_index('Date').sort_index()
        
        # Numeric Conversion
        for col in price_df.columns:
            price_df[col] = pd.to_numeric(price_df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

        print(f"  - 종가 데이터: {len(price_df)}일치")
    except Exception as e:
        print(f"❌ 종가 데이터 로드 실패: {e}")
        return

    # 4. 일별 평가액 계산
    print("🔄 일별 평가액 계산 중...")
    calculated_values = {} # Date -> Value
    
    # Iterate Qty Dates
    for dt in qty_df.index:
        date_str = dt.strftime('%Y-%m-%d')
        
        # Find matching price date (exact match for now)
        # If weekend rollback is needed, price_df should already have the Friday date?
        # If Qty has 1/10 but Price has 1/9, we might mismatch.
        # User wants to update ISA chart. The chart date depends on ISA sheet date.
        
        if dt in price_df.index:
            prices_series = price_df.loc[dt]
        else:
            # Try finding nearest previous price?
            # Or just skip
            # print(f"  ⚠️ {date_str} 종가 데이터 없음 -> Skip")
            continue
            
        qty_series = qty_df.loc[dt]
        
        total_val = 0
        for stock_name, qty in qty_series.items():
            if qty <= 0: continue
            
            # Name -> Code
            code = name_map.get(stock_name)
            if not code: continue
            
            # Code -> Price
            # Tickers in price_df might be '005930.KS' etc.
            # code from map is '005930'.
            # Match fuzzy
            price = 0
            if code in prices_series: 
                price = prices_series[code]
            else:
                # Try suffix match
                for p_col in prices_series.index:
                    if p_col.startswith(code):
                        price = prices_series[p_col]
                        break
            
            if price > 0:
                total_val += qty * price
                
        calculated_values[date_str] = total_val
        
    print(f"  - 계산된 일자: {len(calculated_values)}일")
    if not calculated_values:
        print("❌ 계산된 결과가 없습니다. 매칭되는 날짜/종목이 없는지 확인하세요.")
        return

    # 5. 시트 업데이트 (Bulk Update)
    print(f"💾 '{TARGET_SHEET}' 업데이트 중 (Bulk Mode)...")
    try:
        sh = gc.open(GOOGLE_SHEET_NAME)
        ws = sh.worksheet(TARGET_SHEET)
        
        # A열(날짜) 가져오기
        # E열(평가액) 업데이트
        
        # 전체 데이터 가져오기
        sheet_data = ws.get_all_values()
        if len(sheet_data) < 2:
            print("❌ 시트 데이터가 부족합니다.")
            return
            
        header = sheet_data[0]
        rows = sheet_data[1:]
        
        # E열 인덱스 찾기 (보통 4)
        # 하지만 헤더 이름이 '평가액'인지 확인
        try:
            val_idx = header.index('평가액')
        except:
            val_idx = 4 # Default E column
            
        print(f"  - 타켓 컬럼 인덱스: {val_idx} (헤더: {header[val_idx]})")
        
        # 업데이트할 값 리스트 준비
        # 기존 데이터를 유지하되, 계산된 날짜만 덮어쓰기 위해
        # 현재 E열 데이터를 읽어야 하는데 get_all_values에 이미 포함됨.
        
        final_values = []
        update_count = 0
        
        for row in rows:
            row_date = row[0]
            current_val = row[val_idx] if len(row) > val_idx else ""
            new_val = current_val
            
            try:
                dt_obj = pd.to_datetime(row_date, errors='coerce')
                if not pd.isna(dt_obj):
                    d_str = dt_obj.strftime('%Y-%m-%d')
                    if d_str in calculated_values:
                        new_val = int(calculated_values[d_str])
                        update_count += 1
            except: pass
            
            final_values.append([new_val])
            
        if update_count > 0:
            print(f"  - {update_count}건의 데이터 준비됨. 일괄 업데이트 시작...")
            # E2 부터 시작
            range_str = f"{chr(65+val_idx)}2:{chr(65+val_idx)}{len(rows)+1}"
            ws.update(range_name=range_str, values=final_values, value_input_option='USER_ENTERED')
            print("✅ 업데이트 완료")
        else:
             print("⚠️ 업데이트할 데이터가 매칭되지 않았습니다.")

    except Exception as e:
        print(f"❌ 시트 업데이트 실패: {e}")

if __name__ == "__main__":
    main()
