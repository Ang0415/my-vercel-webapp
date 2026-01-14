# portfolio_update.py
import pandas as pd
import gspread
import traceback
import time
from datetime import datetime, timedelta
import portfolio_utils as utils

def remove_holiday_data(gc):
    """
    모든 데이터 파일에서 휴장일(주말/공휴일) 데이터를 찾아 자동으로 삭제합니다.
    (덮어쓰기 방식으로 삭제)
    """
    print("\n🧹 [청소] 휴장일 데이터 자동 삭제 시작...")
    
    kr_holidays = utils.kr_holidays

    # 점검 및 삭제 대상 목록
    TARGETS = [
        # 1. 원본 데이터
        {'file': utils.QUANTITY_RAW_FILE_NAME, 'sheets': [utils.QUANTITY_SHEET_NAME], 'date_col': 0},
        
        # 2. 결과 시트들 in KYI_자산배분
        {'file': utils.GOOGLE_SHEET_NAME, 'sheets': [utils.ACCOUNT_SHEETS['ISA'], utils.ACCOUNT_SHEETS['IRP'], utils.ACCOUNT_SHEETS['연금'], utils.ACCOUNT_SHEETS['금현물']], 'date_col': 0},
        
        # 3. 일별 비중
        {'file': '일별비중_RAW', 'sheets': ['일별비중_Raw'], 'date_col': 0},
    ]

    count_deleted_files = 0
    
    for item in TARGETS:
        file_name = item['file']
        target_sheets = item['sheets']
        date_col_idx = item['date_col']
        
        try:
            try:
                sh = utils.safe_execute_with_retry(gc.open, file_name)
            except gspread.exceptions.SpreadsheetNotFound:
                continue
                
            if target_sheets == 'ALL':
                worksheets = sh.worksheets()
            else:
                worksheets = []
                for s_name in target_sheets:
                    try: 
                        worksheets.append(utils.safe_execute_with_retry(sh.worksheet, s_name))
                    except: pass
            
            for ws in worksheets:
                if not ws: continue
                time.sleep(2) # Rate Limit

                try:
                    all_values = utils.safe_execute_with_retry(ws.get_all_values)
                    if not all_values or len(all_values) < 2: continue
                    
                    header = all_values[0]
                    rows = all_values[1:]
                    
                    new_rows = []
                    deleted_count = 0
                    
                    for row in rows:
                        if len(row) <= date_col_idx: 
                            new_rows.append(row)
                            continue
                            
                        date_str = str(row[date_col_idx]).strip()
                        if not date_str: 
                            new_rows.append(row)
                            continue
                            
                        try:
                            dt = pd.to_datetime(date_str, format='%Y-%m-%d', errors='coerce')
                            if pd.isna(dt):
                                new_rows.append(row)
                                continue
                                
                            is_weekend = dt.weekday() >= 5
                            is_holiday = (dt.strftime('%Y-%m-%d') in kr_holidays)
                            
                            if is_weekend or is_holiday:
                                deleted_count += 1
                            else:
                                new_rows.append(row)
                                
                        except:
                            new_rows.append(row)
                    
                    if deleted_count > 0:
                        print(f"  Start Cleaning '{file_name}' > '{ws.title}' ... ({deleted_count} rows found)")
                        utils.safe_execute_with_retry(ws.clear)
                        
                        update_data = [header] + new_rows
                        utils.safe_execute_with_retry(ws.update, range_name='A1', values=update_data, value_input_option='USER_ENTERED')
                        print(f"  ✅ 삭제 완료: {deleted_count}건 제거됨.")
                        count_deleted_files += 1
                    
                except Exception as e_ws:
                    print(f"  ⚠️ 시트 처리 오류({ws.title}): {e_ws}")
                    
        except Exception as e_file:
            print(f"  ❌ 파일 접근 오류({file_name}): {e_file}")
            
    print("🧹 휴장일 데이터 정리 완료.\n")

def update_daily_quantities(gc):
    print("\n📦 [수량] 일별 보유 수량 업데이트 시작 (매매일지 기반)")
    try:
        # 1. 매매일지 로드
        spreadsheet = utils.safe_execute_with_retry(gc.open, utils.GOOGLE_SHEET_NAME)
        ws_trades = utils.safe_execute_with_retry(spreadsheet.worksheet, utils.TRADES_SHEET_NAME)
        trade_data = utils.safe_execute_with_retry(ws_trades.get_all_values)
        if len(trade_data) < 2:
            print("  ⚠️ 매매일지 데이터가 없습니다.")
            return

        trades_df = pd.DataFrame(trade_data[1:], columns=trade_data[0])
        trades_df['날짜'] = pd.to_datetime(trades_df['날짜'], errors='coerce')
        trades_df['수량'] = pd.to_numeric(trades_df['수량'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        trades_df = trades_df.dropna(subset=['날짜']).sort_values('날짜')
        
        # 2. 타겟 시트(수량_RAW) 로드
        try:
            q_sh = utils.safe_execute_with_retry(gc.open, utils.QUANTITY_RAW_FILE_NAME)
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"❌ 수량 파일('{utils.QUANTITY_RAW_FILE_NAME}')을 찾을 수 없습니다.")
            return

        ws_q = None
        worksheets = utils.safe_execute_with_retry(q_sh.worksheets)
        for s in worksheets:
            if utils.QUANTITY_SHEET_NAME in s.title: ws_q = s; break
        if not ws_q: ws_q = q_sh.sheet1
        
        q_data = utils.safe_execute_with_retry(ws_q.get_all_values)
        if not q_data:
            print("❌ 수량 시트 데이터가 비어있습니다.")
            return

        headers = q_data[0]
        target_stocks = headers[1:] # 0번째는 날짜
        
        dates = [row[0] for row in q_data[1:]]
        
        # 휴장일 체크 로직
        today = datetime.now().date()
        kr_holidays = utils.kr_holidays
        while today.weekday() >= 5 or today.strftime('%Y-%m-%d') in kr_holidays:
            today -= timedelta(days=1)
        today_str = today.strftime('%Y-%m-%d')
        
        if today_str not in dates:
            print(f"  ➕ 오늘 날짜({today_str}) 행 추가 중...")
            new_row = [today_str] + [0]*len(target_stocks)
            utils.safe_execute_with_retry(ws_q.append_row, new_row, value_input_option='USER_ENTERED')
            dates.append(today_str)
            
        # 3. 재계산 (Replay)
        date_row_map = {d: i+2 for i, d in enumerate(dates)}
        sorted_dates = sorted(dates)
        
        current_holdings = {stock: 0.0 for stock in target_stocks}
        trades_q = trades_df.copy()
        trade_idx = 0
        num_trades = len(trades_q)
        
        updates = []
        
        for d_str in sorted_dates:
            curr_dt = pd.to_datetime(d_str, errors='coerce')
            if pd.isna(curr_dt): continue
            
            while trade_idx < num_trades:
                row = trades_q.iloc[trade_idx]
                t_dt = row['날짜']
                if t_dt > curr_dt: break
                
                t_name = str(row['종목명']).strip().replace(' ', '')
                t_type = row['매매구분']
                t_qty = row['수량']
                
                matched_header = None
                for h in target_stocks:
                    h_clean = h.strip().replace(' ', '')
                    if t_name == h_clean:
                        matched_header = h; break
                    if t_name in h_clean or h_clean in t_name:
                        matched_header = h
                        if t_name == h_clean: break
                
                if matched_header:
                    if '매수' in t_type: current_holdings[matched_header] += t_qty
                    elif '매도' in t_type:
                        current_holdings[matched_header] -= t_qty
                        if current_holdings[matched_header] < 0: current_holdings[matched_header] = 0
                
                trade_idx += 1
                
            row_vals = [current_holdings.get(h, 0) for h in target_stocks]
            updates.append({'vals': row_vals})

        # 4. Batch Update
        is_sorted = all(dates[i] <= dates[i+1] for i in range(len(dates)-1))
        
        if is_sorted:
            matrix = [up['vals'] for up in updates]
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
                print(f"  ✅ {len(matrix)}일치 수량 데이터 업데이트 완료")
            except Exception as e:
                print(f"  ❌ 일괄 업데이트 실패: {e}")
        else:
             print("  ⚠️ 시트 날짜가 정렬되지 않아 업데이트를 건너뜁니다 (정렬 권장).")
                  
    except Exception as e:
        print(f"❌ 수량 업데이트 실패: {e}")
        traceback.print_exc()

def calculate_and_update_account_values(gc):
    print("\n🚀 [전계좌] 평가액 계산 및 업데이트 시작 (최근 2일)")
    
    QTY_FILE = '수량_RAW'
    QTY_SHEET = '수량'
    
    try:
        qty_ws = utils.safe_execute_with_retry(gc.open(QTY_FILE).worksheet, QTY_SHEET)
        qty_data = utils.safe_execute_with_retry(qty_ws.get_all_values)
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

    # 종가_RAW 로드
    PRICE_FILE = '종가_RAW'
    PRICE_SHEET = '종가관리'
    
    try:
        price_ws = utils.safe_execute_with_retry(gc.open(PRICE_FILE).worksheet, PRICE_SHEET)
        price_data = utils.safe_execute_with_retry(price_ws.get_all_values)
        if not price_data or len(price_data) < 3:
            print("❌ 종가 데이터 부족")
            return
            
        price_header_row = price_data[0]
        if len(price_data) > 1:
            row_1_str = " ".join([str(x) for x in price_data[1]])
            if 'KRX:' in row_1_str or 'Code' in row_1_str:
                price_header_row = price_data[1]
                print("ℹ️ 종가 데이터 헤더로 2행(종목코드) 사용")
        
        price_headers = price_header_row
        
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

    # 자산배분 & 설정 로드 (매핑용)
    name_to_code = {}
    ticker_to_account = {} 
    
    try:
        spreadsheet = utils.safe_execute_with_retry(gc.open, utils.GOOGLE_SHEET_NAME)
        setting_ws = utils.safe_execute_with_retry(spreadsheet.worksheet, '⚙️설정')
        setting_data = utils.safe_execute_with_retry(setting_ws.get_all_values)
        if setting_data:
            for row in setting_data[1:]:
                if len(row) > 17:
                    s_name = row[16].strip()
                    s_code = row[17].strip()
                    if s_name and s_code: name_to_code[s_name] = s_code
        
        alloc_ws = utils.safe_execute_with_retry(spreadsheet.worksheet, '자산배분')
        alloc_data = utils.safe_execute_with_retry(alloc_ws.get_all_values)
        
        if alloc_data:
            acc_idx = 11; name_idx = 0
            for row in alloc_data[1:]:
                if len(row) > max(acc_idx, name_idx):
                    t_name = row[name_idx].strip()
                    acc_raw = row[acc_idx].strip()
                    if not t_name or not acc_raw: continue
                    
                    target_key = None
                    if 'ISA' in acc_raw: target_key = 'ISA'
                    elif 'IRP' in acc_raw: target_key = 'IRP'
                    elif '연금' in acc_raw: target_key = '연금'
                    
                    if target_key:
                        ticker_to_account[t_name] = target_key
        
        ticker_to_account['금현물'] = '금현물'
        name_to_code['금현물'] = '금현물' 
                        
    except Exception as e:
        print(f"❌ 매핑 데이터 로딩 실패: {e}")
        return

    # 루프 실행
    target_sheets = {}
    target_dates_cache = {}
    
    for acc_key, sheet_name in utils.ACCOUNT_SHEETS.items():
        try:
             ws = utils.safe_execute_with_retry(spreadsheet.worksheet, sheet_name)
             target_sheets[acc_key] = ws
             target_dates_cache[acc_key] = utils.safe_execute_with_retry(ws.col_values, 1)
        except:
             print(f"⚠️ {sheet_name} 시트 접근 불가")

    for q_row in rows_to_process:
        target_date_str = q_row[0]
        if not target_date_str: continue
        
        day_prices = price_map.get(target_date_str)
        if not day_prices: pass
        
        account_totals = {k: 0 for k in utils.ACCOUNT_SHEETS.keys()}
        
        for idx, val in enumerate(q_row):
            if idx == 0: continue
            if idx >= len(qty_header): break
            
            s_name = qty_header[idx]
            try: qty = float(str(val).replace(',',''))
            except: qty = 0
            
            if qty <= 0: continue
            
            if s_name in ticker_to_account:
                acc_key = ticker_to_account[s_name]
            else:
                if '금현물' in s_name: acc_key = '금현물'
                else: acc_key = '기타'
            
            if acc_key not in account_totals: continue
            
            price = 0
            if day_prices:
                code = name_to_code.get(s_name)
                if code:
                    price = day_prices.get(code)
                    if price is None:
                        clean = code.replace('KRX:','').strip()
                        price = day_prices.get(clean)
                        if price is None:
                            for pk, pv in day_prices.items():
                                if clean in pk:
                                    price = pv; break
                else:
                    price = day_prices.get(s_name)
            
            if price:
                account_totals[acc_key] += qty * price
                
        for acc_key, total_val in account_totals.items():
            if total_val <= 0: continue
            if acc_key not in target_sheets: continue
            
            ws = target_sheets[acc_key]
            dates = target_dates_cache[acc_key]
            
            try:
                if target_date_str in dates:
                    r_idx = dates.index(target_date_str) + 1
                    utils.safe_execute_with_retry(ws.update_cell, r_idx, 5, total_val)
                    print(f"✅ [{acc_key}] {target_date_str} 업데이트: {total_val:,.0f}")
                else:
                    new_row = [target_date_str, "", "", "", total_val]
                    utils.safe_execute_with_retry(ws.append_row, new_row, value_input_option='USER_ENTERED')
                    dates.append(target_date_str)
                    print(f"✅ [{acc_key}] {target_date_str} 추가: {total_val:,.0f}")
            except Exception as e:
                print(f"❌ {acc_key} 업데이트 실패: {e}") 

def record_daily_weights(gc):
    print("\n🚀 [비중 기록] 일별 비중 데이터 기록 시작 (by Portfolio Script)")
    
    WEIGHTS_FILE_NAME = '일별비중_RAW'
    WEIGHTS_SHEET_NAME = '일별비중_Raw'
    WEIGHTS_HEADER = ['날짜', '계좌명', '종목코드', '종목명', '자산구분', '국적', '평가금액', '포트폴리오내비중(%)']
    
    try:
        # 날짜 결정
        target_date = datetime.now().date()
        kr_holidays = utils.kr_holidays
        while target_date.weekday() >= 5 or target_date.strftime('%Y-%m-%d') in kr_holidays:
            print(f"  🛑 {target_date}은 휴장일입니다. 하루 전으로 이동합니다.")
            target_date -= timedelta(days=1)
            
        target_date_str = target_date.strftime("%Y-%m-%d")
        
        spreadsheet = utils.safe_execute_with_retry(gc.open, utils.GOOGLE_SHEET_NAME)
        alloc_ws = utils.safe_execute_with_retry(spreadsheet.worksheet, '자산배분')
        settings_ws = utils.safe_execute_with_retry(spreadsheet.worksheet, '⚙️설정')
        
        alloc_data = utils.safe_execute_with_retry(alloc_ws.get_all_values)
        settings_data = utils.safe_execute_with_retry(settings_ws.get_all_values)
        
        if not alloc_data or len(alloc_data) < 2:
            print("❌ 자산배분 시트 데이터를 읽을 수 없습니다.")
            return

        name_to_code_map = {}
        if settings_data and len(settings_data) > 1:
            for row in settings_data[1:]:
                if len(row) >= 18:
                    s_name = str(row[16]).strip()
                    s_code = str(row[17]).strip()
                    if s_name and s_code:
                        name_to_code_map[s_name] = s_code
                        
        all_holdings_data = []
        
        for row in alloc_data[1:]:
            if len(row) > 11:
                row_name = str(row[0]).strip()
                row_type_raw = str(row[1]).strip()
                row_weight_str = str(row[2]).strip()
                row_val_str = str(row[8]).strip()
                row_acc = str(row[11]).strip()
                
                if not row_acc: continue
                
                try: val = float(row_val_str.replace(',',''))
                except: val = 0
                
                try: weight_raw = float(row_weight_str.replace('%', '').replace(',', ''))
                except: weight_raw = 0.0
                
                if val <= 0: continue
                
                code = name_to_code_map.get(row_name, "")
                if not code:
                    if '현금' in row_name: code = 'CASH'
                    elif '달러' in row_name: code = 'USD'
                    elif row_name in ['예수금(원화)', '예수금(외화)']: code = 'CASH'
                    elif '금현물' in row_name or row_name == '금': code = 'GOLD'
                    else: code = "N/A"
                
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

        try:
             w_spreadsheet = utils.safe_execute_with_retry(gc.open, WEIGHTS_FILE_NAME)
        except gspread.exceptions.SpreadsheetNotFound:
             print(f"❌ '{WEIGHTS_FILE_NAME}' 파일을 찾을 수 없습니다.")
             return

        try:
            found_ws = None
            all_worksheets = w_spreadsheet.worksheets()
            for ws in all_worksheets:
                if ws.title.strip().lower() == WEIGHTS_SHEET_NAME.lower():
                    found_ws = ws; break
            
            if found_ws:
                weights_ws = found_ws
            else:
                raise gspread.exceptions.WorksheetNotFound(WEIGHTS_SHEET_NAME)

        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️ '{WEIGHTS_SHEET_NAME}' 시트가 없어 생성 중...")
            weights_ws = utils.safe_execute_with_retry(w_spreadsheet.add_worksheet, title=WEIGHTS_SHEET_NAME, rows="1000", cols=len(WEIGHTS_HEADER))
            utils.safe_execute_with_retry(weights_ws.append_row, WEIGHTS_HEADER, value_input_option='USER_ENTERED')
            
        all_weights = utils.safe_execute_with_retry(weights_ws.get_all_values)
        if not all_weights: all_weights = [WEIGHTS_HEADER]
        
        header = all_weights[0]
        rows = all_weights[1:]
        
        filtered_rows = [r for r in rows if str(r[0]).strip() != target_date_str]
        
        final_rows = [header] + filtered_rows + all_holdings_data
        
        utils.safe_execute_with_retry(weights_ws.clear)
        utils.safe_execute_with_retry(weights_ws.update, range_name='A1', values=final_rows, value_input_option='USER_ENTERED')
        
        print(f"✅ 일별 비중 데이터 업데이트 완료 ({len(all_holdings_data)}건 기록)")

    except Exception as e:
        print(f"❌ 일별 비중 기록 실패: {e}")
        traceback.print_exc()

def check_holiday_data(gc):
    print("\n🕵️ [데이터 점검] 휴장일 데이터 포함 여부 검사 시작...")
    
    CHECK_LIST = [
        {'file': utils.TWR_RAW_SHEET, 'sheets': 'ALL', 'date_col': 0},
        {'file': utils.DAILY_ASSET_SPREADSHEET_NAME, 'sheets': 'ALL', 'date_col': 0},
        {'file': utils.GAIN_LOSS_RAW_SHEET, 'sheets': 'ALL', 'date_col': 0},
        {'file': '일별비중_RAW', 'sheets': ['일별비중_Raw'], 'date_col': 0},
        {'file': utils.QUANTITY_RAW_FILE_NAME, 'sheets': [utils.QUANTITY_SHEET_NAME], 'date_col': 0},
        {'file': '종가_RAW', 'sheets': ['종가관리'], 'date_col': 0},
    ]

    kr_holidays = utils.kr_holidays
    found_issues = False
    
    for item in CHECK_LIST:
        file_name = item['file']
        target_sheets = item['sheets']
        date_col_idx = item['date_col']
        
        time.sleep(2)

        try:
            try:
                sh = utils.safe_execute_with_retry(gc.open, file_name)
            except gspread.exceptions.SpreadsheetNotFound:
                continue
            except Exception as e:
                print(f"  - (Error) '{file_name}' 열기 실패: {e}")
                continue

            if target_sheets == 'ALL':
                worksheets = sh.worksheets()
            else:
                worksheets = []
                for s_name in target_sheets:
                    try: 
                        worksheets.append(utils.safe_execute_with_retry(sh.worksheet, s_name))
                    except: pass
            
            for ws in worksheets:
                if not ws: continue
                try:
                    dates = utils.safe_execute_with_retry(ws.col_values, date_col_idx + 1)
                    if not dates or len(dates) < 2: continue

                    for r_idx, date_str in enumerate(dates[1:], start=2):
                        if not date_str: continue
                        try:
                            dt = pd.to_datetime(date_str, format='%Y-%m-%d', errors='coerce')
                            if pd.isna(dt): continue
                            
                            is_weekend = dt.weekday() >= 5 
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
