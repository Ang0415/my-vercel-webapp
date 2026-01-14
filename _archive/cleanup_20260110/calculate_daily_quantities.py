import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import os
import sys
import time
from datetime import datetime, timedelta

# --- 설정 ---
GOOGLE_SHEET_NAME = 'ISA_종목별_수량_RAW'
TRADES_SHEET_NAME = '매매일지'
QUANTITY_SHEET_NAME = '수량'

# 컬럼 인덱스 (0-based)
COL_DATE = 0     # A열
COL_STOCK = 1    # B열
COL_TYPE = 3     # D열 (매수/매도)
COL_QTY = 5      # F열

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')

def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if not os.path.exists(JSON_KEYFILE_PATH):
             print(f"❌ 키 파일 없음: {JSON_KEYFILE_PATH}")
             return None
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        print("✅ Google Sheets API 인증 성공.")
        return gc
    except Exception as e:
        print(f"❌ 구글 시트 연결 오류: {e}")
        return None

def safe_execute(func, *args, **kwargs):
    """API 호출 안전 실행 (재시도)"""
    for i in range(5):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "Quota exceeded" in str(e) or "429" in str(e):
                print(f"⚠️ Quota exceeded. Retrying... ({i+1}/5)")
                time.sleep(5 * (i + 1))
            else:
                if i == 4: raise e
                time.sleep(1)
    return None

def main():
    gc = connect_google_sheets()
    if not gc: return

    print(f"\n--- '{GOOGLE_SHEET_NAME}' 처리 시작 ---")
    
    try:
        spreadsheet = safe_execute(gc.open, GOOGLE_SHEET_NAME)
        
        # 1. 매매일지 로드
        print(f"📥 '{TRADES_SHEET_NAME}' 데이터 읽는 중...")
        trades_ws = safe_execute(spreadsheet.worksheet, TRADES_SHEET_NAME)
        trades_data = safe_execute(trades_ws.get_all_values)
        
        if len(trades_data) < 2:
            print("❌ 매매일지 데이터가 없습니다.")
            return

        # 데이터 프레임 변환
        df_trades = pd.DataFrame(trades_data[1:], columns=trades_data[0]) 
        
        # 필요한 컬럼만 추출 및 정리
        # 실제 컬럼명과 상관없이 인덱스로 접근 (헤더 변경 가능성 대비)
        processed_data = []
        for row in trades_data[1:]:
            if len(row) > COL_QTY:
                processed_data.append({
                    'Date': row[COL_DATE],
                    'Stock': row[COL_STOCK],
                    'Type': row[COL_TYPE],
                    'Qty': row[COL_QTY]
                })
        
        df = pd.DataFrame(processed_data)
        
        # 전처리
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df.dropna(subset=['Date'])
        
        # 수량 숫자 변환
        def clean_qty(val):
            if isinstance(val, (int, float)): return float(val)
            return float(str(val).replace(',', '').strip() or 0)
            
        df['Qty_Num'] = df['Qty'].apply(clean_qty)
        df = df.sort_values(by='Date')
        
        if df.empty:
            print("❌ 유효한 매매 데이터가 없습니다.")
            return

        # 2. 일별 수량 계산
        print("🔄 일별 수량 계산 중...")
        
        start_date = df['Date'].min()
        end_date = datetime.now() # 오늘까지
        
        # 날짜 범위 생성
        date_range = pd.date_range(start=start_date, end=end_date, freq='D')
        
        # 종목 리스트
        stock_list = sorted(df['Stock'].unique())
        
        # 결과 저장용 리스트
        daily_quantities = []
        
        # 현재 보유 수량 추적 (딕셔너리)
        current_holdings = {stock: 0.0 for stock in stock_list}
        
        # 매매 로그 인덱스 관리 (효율성)
        trade_idx = 0
        total_trades = len(df)
        
        for single_date in date_range:
            # 해당 날짜(single_date)에 일어난 모든 매매 처리
            while trade_idx < total_trades:
                trade_row = df.iloc[trade_idx]
                trade_date = trade_row['Date']
                
                if trade_date > single_date:
                    break # 매매 날짜가 현재 루프 날짜보다 미래면 중단
                
                # 매매 반영
                stock = trade_row['Stock']
                qty = trade_row['Qty_Num']
                trade_type = str(trade_row['Type']).strip()
                
                if trade_type == '매수':
                    current_holdings[stock] += qty
                elif trade_type == '매도':
                    current_holdings[stock] -= qty
                
                # 수량은 음수가 될 수 없음 (데이터 오류가 아닌 이상)
                # if current_holdings[stock] < 0: current_holdings[stock] = 0
                
                trade_idx += 1
            
            # 현재 상태 저장
            daily_snapshot = {'Date': single_date.strftime('%Y-%m-%d')}
            # 0이 아닌 수량만 저장? 아니면 전체 저장? -> 전체 저장이 시트 보기에 좋음
            for stock, qty in current_holdings.items():
                daily_snapshot[stock] = qty
            
            daily_quantities.append(daily_snapshot)
            
        # 결과 데이터프레임
        result_df = pd.DataFrame(daily_quantities)
        
        # 3. '수량' 시트에 저장
        print(f"💾 '{QUANTITY_SHEET_NAME}' 시트에 저장 중... ({len(result_df)}일 x {len(stock_list)}종목)")
        
        try:
            quantity_ws = safe_execute(spreadsheet.worksheet, QUANTITY_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            print(f"  - 시트 '{QUANTITY_SHEET_NAME}' 생성...")
            quantity_ws = safe_execute(spreadsheet.add_worksheet, title=QUANTITY_SHEET_NAME, rows=len(result_df)+100, cols=len(result_df.columns))
            
        # 데이터 준비
        # NaN을 0으로 채우기 (앞서 딕셔너리 초기화해서 없겠지만 안전장치)
        result_df = result_df.fillna(0)
        
        # 리스트 변환 [헤더] + [내용]
        values = [result_df.columns.values.tolist()] + result_df.values.tolist()
        
        safe_execute(quantity_ws.clear)
        safe_execute(quantity_ws.update, range_name='A1', values=values)
        
        print("✅ 완료!")

    except Exception as e:
        print(f"🔥 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
