import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re
from datetime import datetime
import time
import os
import json
import json
import sys

# --- 1. 기본 설정 및 유틸리티 ---
st.set_page_config(page_title="Data Entry - Money Manager", page_icon="📝", layout="wide")

# Google Sheets 인증 (기존 로직 재사용)
@st.cache_resource
def init_connection():
    try:
        if "gcs_connections" in st.secrets:
             credentials = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcs_connections"], ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        else:
            # 로컬 JSON 파일 fallback
            current_dir = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(current_dir, 'stock-auto-writer-44eaa06c140c.json')
            if os.path.exists(json_path):
                credentials = ServiceAccountCredentials.from_json_keyfile_name(json_path, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
            else:
                return None
        
        gc = gspread.authorize(credentials)
        return gc
    except Exception as e:
        print(f"Connection Error: {e}")
        return None

def safe_api_call(func, *args, retries=3, delay=2, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "Quota exceeded" in str(e) or "429" in str(e):
                time.sleep(delay * (i + 1))
            else:
                if i == retries - 1: raise e
                time.sleep(1)
    return None

gc = init_connection()

GOOGLE_SHEET_NAME = 'KYI_자산배분' # 메인 시트
TRADES_SHEET = '🗓️매매일지'
PRICE_FILE_NAME = '종가_RAW'
PRICE_SHEET_NAME = '종가관리'

st.title("📝 투자 데이터 입력 도구")

if not gc:
    st.error("구글 시트 연결에 실패했습니다. (인증 파일 확인 필요)")
    st.stop()

# --- 2. 탭 구성 ---
tab1, tab2 = st.tabs(["📨 매매일지 입력", "💰 금현물 종가 입력"])

# --- Tab 1: 매매일지 입력 ---
with tab1:
    st.info("증권사 체결 문자를 붙여넣어 '매매일지' 시트에 추가합니다.")
    
    raw_text = st.text_area("체결 문자 붙여넣기", height=200, placeholder="[한국투자] 삼성전자 매수 10주 70,000원 체결\n[키움] TIGER미국S&P500 매도 5주 @15,000")
    
    if st.button("분석하기") or raw_text:
        parsed_data = []
        # 메시지 블록 단위로 분리 (빈 줄 또는 '[' 문자로 시작하는 부분)
        # 1. Double Newline or 2. Start of '[' char (Lookahead)
        blocks = re.split(r'\n\s*\n|(?=\[)', raw_text)
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        if raw_text.strip():
            for block in blocks:
                block = block.strip()
                if not block: continue
                
                # 블록 내 줄 단위 처리 (정보 수집)
                lines = block.split('\n')
                
                date_val = today_str
                type_val = "매수" 
                name_val = ""
                qty_val = 0
                price_val = 0
                
                # 정보 추출
                found_info = {'name': None, 'qty': 0, 'price': 0, 'type': '매수'}
                
                for line in lines:
                    line = line.strip()
                    if not line: continue
                    
                    # [NEW] 명시적 라벨 파싱 (한국투자증권 등: *종목명: 삼성전자)
                    # 1. 종목명
                    if '종목명' in line and ':' in line:
                        target = line.split(':', 1)[1].strip()
                        # (123456) 같은 코드 제거
                        target = re.sub(r'\(\d{6}\)', '', target) 
                        target = target.strip()
                        if target:
                            name_val = target
                            found_info['name'] = True
                            continue # 종목명 줄 처리 완료

                    # 2. 매매구분
                    if '매매구분' in line and ':' in line:
                        target = line.split(':', 1)[1].strip()
                        if '매도' in target: type_val = '매도'
                        elif '매수' in target: type_val = '매수'
                        continue

                    # 3. 체결수량 / 수량
                    # "*체결수량:70주" or "*수량: 10"
                    if ('수량' in line or '체결수량' in line) and ':' in line:
                        target = line.split(':', 1)[1].strip()
                        match = re.search(r'(\d+)', target.replace(',',''))
                        if match:
                            qty_val = int(match.group(1))
                            found_info['qty'] = True
                            continue
                            
                    # 4. 체결단가 / 단가
                    if ('단가' in line or '체결단가' in line) and ':' in line:
                        target = line.split(':', 1)[1].strip()
                        match = re.search(r'([\d,]+)', target)
                        if match:
                            price_val = int(match.group(1).replace(',',''))
                            found_info['price'] = True
                            continue

                    # --- 기존 휴리스틱 로직 (라벨이 없는 경우 대비) ---
                    # 분석 및 제거를 위한 임시 변수
                    rem_line = line
                    
                    # 1. 수량 파악 & 제거
                    qty_match = re.search(r'(\d+)\s*주', rem_line)
                    if qty_match:
                        # 명시적 수량을 못 찾았거나, 이 줄에 수량이 있는 경우
                        temp_qty = int(qty_match.group(1))
                        if qty_val == 0: qty_val = temp_qty # 우선순위: 명시적 라벨 > 휴리스틱
                        
                        rem_line = rem_line.replace(qty_match.group(0), ' ') # 제거
                        
                        # 같은 줄에 매수/매도 키워드가 있다면 타입 결정
                        if '매도' in line: type_val = '매도' # 원본 line 기준
                        elif '매수' in line: type_val = '매수'

                    # 2. 가격 파악 & 제거
                    price_match = re.search(r'([\d,]+)\s*원', rem_line)
                    if not price_match:
                         # "@10,000" 형식
                         price_match = re.search(r'@\s*([\d,]+)', rem_line)
                    
                    if price_match:
                        price_val_str = price_match.group(1).replace(',', '')
                        try:
                             temp_price = int(price_val_str)
                             if price_val == 0: price_val = temp_price
                             rem_line = rem_line.replace(price_match.group(0), ' ') # 제거
                        except: pass
                        
                    # 3. 매수/매도 키워드 찾기
                    if '매도' in rem_line: type_val = '매도'
                    elif '매수' in rem_line: type_val = '매수'
                    
                    # 4. 종목명 후보 찾기 (이미 종목명을 찾았다면 스킵)
                    if not name_val:
                        # 키워드 및 특수문자 제거
                        # 대괄호 내용 제거 ([키움][한국투자] 등)
                        rem_line = re.sub(r'\[.*?\]', '', rem_line)
                        
                        # 불필요 키워드 제거
                        keywords = ['매수', '매도', '체결', '통보', '주문', '시장가', '체결가', '단가', '평균', '잔고', '안내', '종목명', '체결수량', '체결통보', '계좌', '번호', '총체결', '합계', '현금', '주문번호', '주문수량', '총체결수량']
                        for k in keywords:
                            rem_line = rem_line.replace(k, ' ')
                        
                        tokens = rem_line.split()
                        parsed_tokens = []
                        for t in tokens:
                            t_clean = t.strip()
                            # 숫자로만 구성된건 제외
                            if t_clean.replace(',','').replace('-','').replace('.','').isdigit(): continue
                            # 시간 패턴 제외 (09:22)
                            if re.match(r'^\d{1,2}:\d{2}$', t_clean): continue
                            # 마스킹된 패턴 제외 (****)
                            if '*' in t_clean: continue
                            # 콜론으로 시작하는 숫자 제외 (:692900)
                            if t_clean.startswith(':') and t_clean[1:].replace(',','').isdigit(): continue
                             
                            # 특수문자(* 등) 제거
                            t_clean = re.sub(r'[\*]', '', t_clean)
                            if len(t_clean) < 1: continue 
                             
                            parsed_tokens.append(t_clean)
                        
                        # 라인 단위로 남은 텍스트를 종목명으로 간주
                        rem_line_clean = " ".join(parsed_tokens)
                        
                        if len(rem_line_clean) > 1:
                              # 괄호 제거
                              name_val = re.sub(r'\(.*?\)', '', rem_line_clean).strip()
                              found_info['name'] = True

                # 블록 처리 끝

                # 저장 조건 강화: 종목명이 있고, 수량이나 가격 중 하나라도 유효해야 함
                if name_val and (qty_val > 0 or price_val > 0):
                     parsed_data.append({
                        '날짜': date_val,
                        '매매구분': type_val,
                        '종목명': name_val,
                        '수량': qty_val,
                        '단가': price_val
                    })

            if parsed_data:
                st.success(f"{len(parsed_data)}건 인식됨")
                df_parsed = pd.DataFrame(parsed_data)
                edited_df = st.data_editor(df_parsed, num_rows="dynamic", use_container_width=True)
                
                if st.button("💾 구글 시트에 저장 (매매일지)"):
                    try:
                        spreadsheet = safe_api_call(gc.open, GOOGLE_SHEET_NAME)
                        ws_trades = safe_api_call(spreadsheet.worksheet, TRADES_SHEET)
                        
                        header_row = safe_api_call(ws_trades.row_values, 1)
                        if not header_row:
                             st.error("시트 헤더를 읽을 수 없습니다.")
                        else:
                            col_map = {'날짜': -1, '매매구분': -1, '종목코드': -1, '종목명': -1, '수량': -1, '단가': -1}
                            for i, h in enumerate(header_row):
                                h_clean = h.strip()
                                if h_clean in col_map: col_map[h_clean] = i
                            
                            # 컬럼 인덱스 -> 엑셀 컬럼 문자 변환 (A, B, C...)
                            def get_col_letter(n):
                                string = ""
                                while n > 0:
                                    n, remainder = divmod(n - 1, 26)
                                    string = chr(65 + remainder) + string
                                return string

                            # 주요 컬럼의 인덱스(0-based) 및 문자(A, B...) 파악
                            name_idx_int = col_map.get('종목명', -1)
                            qty_idx_int = col_map.get('수량', -1)
                            price_idx_int = col_map.get('단가', -1)
                            
                            name_col_char = get_col_letter(name_idx_int + 1) if name_idx_int >= 0 else 'B'
                            qty_col_char = get_col_letter(qty_idx_int + 1) if qty_idx_int >= 0 else 'E'
                            price_col_char = get_col_letter(price_idx_int + 1) if price_idx_int >= 0 else 'F'

                            # [MOVED] 빈 행 찾기 로직 (수식 주입을 위해 먼저 실행)
                            all_values = safe_api_call(ws_trades.get_all_values)
                            first_empty_row_idx = len(all_values) + 1
                            
                            # 헤더 이후부터 실제 데이터가 없는 첫 번째 행 찾기
                            for i in range(1, len(all_values)):
                                row_vals = all_values[i]
                                date_idx = col_map.get('날짜', -1)
                                name_idx = col_map.get('종목명', -1)
                                
                                is_empty = True
                                if date_idx >= 0 and date_idx < len(row_vals) and row_vals[date_idx].strip(): is_empty = False
                                if name_idx >= 0 and name_idx < len(row_vals) and row_vals[name_idx].strip(): is_empty = False
                                
                                if is_empty:
                                    first_empty_row_idx = i + 1
                                    break
                            
                            start_row = first_empty_row_idx

                            # 데이터 구성 및 수식 주입
                            new_rows = []
                            for i, row in enumerate(edited_df.to_dict('records')):
                                row_data = [''] * len(header_row)
                                for col_name, col_idx in col_map.items():
                                    if col_idx >= 0 and col_name in row:
                                        row_data[col_idx] = str(row[col_name])
                                
                                # 수식 주입
                                current_row_num = start_row + i
                                
                                # C열 (Index 2): 종목코드
                                if len(row_data) > 2:
                                    row_data[2] = f'=IFERROR(VLOOKUP({name_col_char}{current_row_num},\'⚙️설정\'!Q:R,2,FALSE),"GOLD")'
                                
                                # G열 (Index 6): 금액
                                if len(row_data) > 6:
                                    row_data[6] = f'={qty_col_char}{current_row_num}*{price_col_char}{current_row_num}'
                                    
                                # H열 (Index 7): 대분류
                                if len(row_data) > 7:
                                    row_data[7] = f'=IFERROR(VLOOKUP({name_col_char}{current_row_num},\'⚙️설정\'!Q:U,5,FALSE),"미분류")'
                                    
                                # I열 (Index 8): 중분류
                                if len(row_data) > 8:
                                    row_data[8] = f'=IFERROR(VLOOKUP({name_col_char}{current_row_num},\'⚙️설정\'!Q:S,3,FALSE),"미분류")'

                                new_rows.append(row_data)
                            
                            # 업데이트 실행
                            for i, row_data in enumerate(new_rows):
                                target_row = start_row + i
                                safe_api_call(ws_trades.update, f"A{target_row}", [row_data], value_input_option='USER_ENTERED')
                                
                            st.success(f"✅ {len(new_rows)}건 저장 완료! (시작 행: {start_row})")
                            time.sleep(1)
                            st.rerun()
                    except Exception as e:
                        st.error(f"저장 중 오류: {e}")
            else:
                st.warning("인식된 내용이 없습니다. 텍스트 형식을 확인해주세요.")

# --- Tab 2: 금현물 종가 입력 ---
with tab2:
    st.info(f"'{PRICE_FILE_NAME}' 파일의 금현물 종가를 업데이트합니다.")
    
    col1, col2 = st.columns([1, 1])
    with col1:
        gold_input = st.number_input("오늘의 금현물 종가 (원/g)", min_value=0, step=100)
    
    with col2:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("종가 업데이트"):
            if gold_input > 0:
                try:
                    price_ss = safe_api_call(gc.open, PRICE_FILE_NAME)
                    price_ws = safe_api_call(price_ss.worksheet, PRICE_SHEET_NAME)
                    
                    headers = safe_api_call(price_ws.row_values, 1)
                    # 헤더 탐색 로직 (1행 or 2행)
                    if not headers or 'Code' not in headers[0]:
                         headers_2 = safe_api_call(price_ws.row_values, 2)
                         if headers_2 and ('Code' in headers_2[0] or 'KRX:' in str(headers_2)): 
                             headers = headers_2

                    target_col = -1
                    for i, h in enumerate(headers):
                        h_clean = str(h).replace(' ', '').upper()
                        if '금현물' in h_clean or 'GOLD' in h_clean or '999980' in h_clean:
                            target_col = i + 1
                            break
                    
                    if target_col == -1:
                        # 혹시 M열(13번째)이 금현물인지 확인 (사용자 제보)
                        if len(headers) >= 13:
                            header_m = headers[12]
                            st.warning(f"참고: M열(13번째) 헤더는 '{header_m}' 입니다.")
                            if '금' in header_m: target_col = 13
                        
                    if target_col == -1:
                        st.error(f"금현물 컬럼을 찾을 수 없습니다. (발견된 헤더: {headers})")
                    else:
                        today_str = datetime.now().strftime('%Y-%m-%d')
                        dates = safe_api_call(price_ws.col_values, 1)
                        
                        row_idx = -1
                        try: row_idx = dates.index(today_str) + 1
                        except ValueError: row_idx = -1
                        
                        target_val = int(gold_input)
                        
                        if row_idx > 0:
                            safe_api_call(price_ws.update_cell, row_idx, target_col, target_val)
                            st.success(f"{today_str} 업데이트 완료: {target_val:,}원")
                        else:
                            new_row = [today_str] + [''] * (len(headers) - 1)
                            new_row[target_col - 1] = target_val
                            safe_api_call(price_ws.append_row, new_row, value_input_option='USER_ENTERED')
                            st.success(f"{today_str} 추가 완료: {target_val:,}원")
                            
                except Exception as e:
                    st.error(f"오류 발생: {e}")
            else:
                st.warning("가격을 입력하세요.")
