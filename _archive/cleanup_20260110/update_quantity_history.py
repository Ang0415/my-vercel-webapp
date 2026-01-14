
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
import sys
import time
from datetime import datetime, timedelta

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

# Config
JSON_KEYFILE_PATH = 'stock-auto-writer-44eaa06c140c.json'
SRC_FILE = 'KYI_자산배분'
SRC_SHEET = '🗓️매매일지'
DST_FILE = '수량_RAW'
DST_SHEET = '수량'

def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        return gc
    except Exception as e:
        print(f"❌ 접속 실패: {e}")
        return None

def main():
    gc = connect_google_sheets()
    if not gc: return

    print("🚀 수량 이력 재구축 시작 (매매일지 기반)...")

    # 1. 매매일지 로드
    print(f"📥 '{SRC_FILE}/{SRC_SHEET}' 로드 중...")
    try:
        sh = gc.open(SRC_FILE)
        ws = sh.worksheet(SRC_SHEET)
        trade_data = ws.get_all_values()
        
        # Header: 날짜(0), 종목명(1), 매매구분(3), 수량(5)
        trades_df = pd.DataFrame(trade_data[1:], columns=trade_data[0])
        trades_df['날짜'] = pd.to_datetime(trades_df['날짜'], errors='coerce')
        trades_df['수량'] = pd.to_numeric(trades_df['수량'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        trades_df = trades_df.dropna(subset=['날짜']).sort_values('날짜')
        
        print(f"  - 매매 기록: {len(trades_df)}건")
    except Exception as e:
        print(f"❌ 매매일지 로드 실패: {e}")
        return

    # 2. 타겟 시트(수량_RAW) 로드
    print(f"📥 '{DST_FILE}/{DST_SHEET}' 로드 중...")
    try:
        sh_dst = gc.open(DST_FILE)
        # Find sheet
        ws_dst = None
        for s in sh_dst.worksheets():
            if DST_SHEET in s.title: ws_dst = s; break
        if not ws_dst: ws_dst = sh_dst.sheet1
        
        dst_data = ws_dst.get_all_values()
        headers = dst_data[0] # Headers (Stock Names)
        
        # A열(날짜) 확인
        dates = []
        for row in dst_data[1:]:
            dates.append(row[0]) # Keep as string first
            
        print(f"  - 타겟 날짜: {len(dates)}일")
        print(f"  - 타겟 종목(헤더): {headers[1:]}")
        
    except Exception as e:
        print(f"❌ 타겟 시트 로드 실패: {e}")
        return

    # 3. 일별 수량 계산 (Replay)
    print("🔄 일별 수량 재계산 중...")
    
    # Target Stocks (Headers)
    target_stocks = headers[1:]
    
    # Map headers to clean names if necessary (assuming headers match '종목명' in log)
    # Simulator
    current_holdings = {stock: 0.0 for stock in target_stocks}
    
    # We need to process trades up to each Target Date.
    # Logic:
    # Iterate Target Dates (sorted).
    # Maintain a 'processed_until' pointer for trades.
    # Apply trades between processed_until and Target Date.
    # Update holding.
    
    # Sort Target Dates to ensure chronological replay
    # But user wants to update specific rows. If rows in sheet are not sorted?
    # Usually they are.
    # Let's create a Dict: Date -> RowIndex (list incase dupes)
    
    target_date_map = [] # [(DateObj, RowIndex, OriginalDateStr)]
    for i, d_str in enumerate(dates):
        try:
            dt = pd.to_datetime(d_str, errors='coerce')
            if not pd.isna(dt):
                target_date_map.append({'date': dt, 'row_idx': i + 2, 'date_str': d_str}) # Row index starts at 2 (1-based, Header is 1)
        except: pass
        
    # Sort by date for replay
    msg_dates = sorted(target_date_map, key=lambda x: x['date'])
    
    trades_q = trades_df.copy()
    trade_idx = 0
    num_trades = len(trades_q)
    
    updates = [] # Batch Update Data
    
    # Optimization: Pre-calculate daily holdings for all trade dates + needed dates
    # But simple replay is fine.
    
    for item in msg_dates:
        curr_date = item['date']
        
        # Apply trades occurring on or before curr_date (that haven't been applied)
        # Actually, since dates can be duplicate or out of order in list (though we sorted msg_dates),
        # we strictly need a cumulative state.
        # But wait, if Sheet has 2024-01-01 then 2024-01-02.
        # We start from 0 holdings at time 0.
        # Then advance.
        
        # Ensure we only advance forward.
        # If msg_dates is sorted, we are good.
        
        while trade_idx < num_trades:
            trade_row = trades_q.iloc[trade_idx]
            t_date = trade_row['날짜']
            
            if t_date > curr_date:
                break # Stop, this trade is in future relative to curr_date
                
            # Apply Trade
            t_name = trade_row['종목명']
            t_type = trade_row['매매구분']
            t_qty = trade_row['수량']
            
            # Name Mapping: clean spaces
            t_name_clean = str(t_name).strip().replace(' ', '')
            
            # Find matching header (fuzzy or exact)
            matched_header = None
            for h in target_stocks:
                h_clean = h.strip().replace(' ', '')
                if t_name_clean == h_clean:
                    matched_header = h
                    break
                # Try contains?
                if t_name_clean in h_clean or h_clean in t_name_clean:
                    # Risk of incorrect match?
                    # e.g. 'TIGER...' vs 'TIGER'
                    # Assuming mostly exact matches or safe contains
                    matched_header = h
                    # don't break, prefer exact?
                    if t_name_clean == h_clean: break
            
            if matched_header:
                if '매수' in t_type:
                    current_holdings[matched_header] += t_qty
                elif '매도' in t_type:
                    current_holdings[matched_header] -= t_qty
                    if current_holdings[matched_header] < 0: current_holdings[matched_header] = 0
            
            trade_idx += 1
            
        # Snapshot for this date
        # Prepare row values [Val1, Val2, ...] corresponding to headers[1:]
        row_vals = []
        for h in target_stocks:
            row_vals.append(current_holdings.get(h, 0))
            
        # Store update: range B{row}:...
        # Col indices: B is 1 (0-based list index 1). A is 0.
        # Sheet columns: A=1, B=2.
        # We need to update B{row_idx} to End{row_idx}
        
        # Values need to be list of lists for gspread? Or just flat list?
        # ws.update takes [[v1, v2...]]
        
        updates.append({
            'range': f"B{item['row_idx']}:{chr(65 + len(target_stocks))}{item['row_idx']}", # A + N
            'values': [row_vals]
        })

    # 4. 업데이트 적용 (Bulk)
    # Too many individual updates -> Batch again.
    # Since headers are contiguous B..N
    # And items are sorted by Row Index (usually) if dates are sorted.
    # Actually, we sorted msg_dates by Date. Rows might be unsorted?
    # If DST Sheet is sorted by date, then efficient.
    # If sparse, we group by contiguous rows?
    
    # Strategy: Build the entire columns B..N in memory and update A2:N_End?
    # No, we only want to update B..N. A column (Date) should stay provided we matched it.
    
    # Let's assume sorted rows.
    # Construct full grid data for B2:N_Last.
    
    if len(updates) == 0:
        print("⚠️ 업데이트할 날짜가 없습니다.")
        return
        
    print(f"💾 시트 업데이트 중 ({len(updates)}행)...")
    
    # Group into one big block if consecutive
    # msg_dates is sorted by DATE.
    # If sheet dates are also sorted, row_idxs are increasing.
    
    final_data = [] # List of lists (rows)
    # We need to map back to original row order to do a single block update.
    # msg_dates has 'row_idx'.
    # We can create a list of length (MaxRow - 1) filled with current data, update based on calc, then write back?
    # Or just write row 2 to end.
    
    # Check max row
    max_row = len(dst_data)
    
    # Init empty grid (or copy existing)
    # It's safer to overwrite fully if we rely on reconstruction.
    # But we must align with Dates in A.
    
    # Array for B col result
    # We iterate 2 to max_row.
    # Find calc result for that row's date.
    
    # Re-map: DateStr -> Holdings
    # Use dictionary map from the calculation loop
    # Wait, calculation loop depended on sorting.
    # Since we iterated sorted dates, the 'current_holdings' state was valid for that point in time.
    # We captured the state in `updates` list (but `updates` is sorted by Date).
    
    # Create Dict: RowIdx -> Values
    row_val_map = {}
    for up in updates:
        # Parse row from range "B10:..."
        r = int(up['range'].split(':')[0][1:])
        v = up['values'][0]
        row_val_map[r] = v
        
    # Prepare ordered values for B2...
    batch_values = []
    start_row = 2
    
    for r in range(start_row, max_row + 1):
        if r in row_val_map:
            batch_values.append(row_val_map[r])
        else:
            # Row has date but we didn't calculate? (Maybe invalid date format)
            # Or Gap?
            # Fill 0 or Keep?
            # Provide 0s for safety in 'Raw' sheet if date failed parsing
            batch_values.append([0]*len(target_stocks))
            
    # Write Batch
    end_col = chr(65 + len(target_stocks))
    end_row = start_row + len(batch_values) - 1
    
    range_str = f"B{start_row}:{end_col}{end_row}"
    print(f"  - 범위: {range_str}")
    
    try:
        ws_dst.update(range_name=range_str, values=batch_values, value_input_option='USER_ENTERED')
        print("✅ 업데이트 완료")
    except Exception as e:
        print(f"❌ 업데이트 실패: {e}")

if __name__ == "__main__":
    main()
