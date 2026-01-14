import gspread
import pandas as pd
import FinanceDataReader as fdr
from oauth2client.service_account import ServiceAccountCredentials
import os
import sys
import time
import traceback
from datetime import datetime, timedelta

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

# --- 설정 ---
GOOGLE_SHEET_NAME = '종가_RAW'  # 파일 이름
WORKSHEET_NAME = '종가관리'   # 시트 이름
START_DATE = '2024-09-03'    # 시작 날짜
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')

def safe_execute(func, *args, retries=3, sleep=2, **kwargs):
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

def main():
    # 0. 날짜 결정 (휴장일이면 직전 영업일)
    try:
        import holidays
        kr_holidays = holidays.KR()
    except ImportError:
        kr_holidays = {}
        
    target_date = datetime.now().date()
    while target_date.weekday() >= 5 or target_date in kr_holidays:
        print(f"🛑 {target_date}은 휴장일입니다. 하루 전으로 이동합니다.")
        target_date -= timedelta(days=1)
    
    target_date_str = target_date.strftime('%Y-%m-%d')
    print(f"🚀 [{GOOGLE_SHEET_NAME}/{WORKSHEET_NAME}] 종가 업데이트 시작 (using FinanceDataReader)")
    print(f"📅 처리 기준일: {target_date_str} (실행일: {datetime.now().date()})")

    # 1. 구글 시트 연결
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        print("✅ 구글 시트 연결 성공")
    except Exception as e:
        print(f"❌ 구글 시트 '{GOOGLE_SHEET_NAME}' 찾기 실패: {e}")
        print("\n🔍 접근 가능한 시트 목록 (공유 여부 확인용):")
        try:
            file_list = gc.openall()
            if not file_list:
                print("  (없음 - 서비스 계정에 공유된 시트가 없습니다.)")
            else:
                for f in file_list:
                    print(f"  - {f.title} (ID: {f.id})")
        except Exception as list_e:
            print(f"  (목록 조회 실패: {list_e})")
        return

    # 2. 티커 읽기 (B2:I2)
    try:
        # headers 읽기
        headers = safe_execute(worksheet.row_values, 2)
        if not headers or len(headers) < 2:
            print("❌ 헤더를 찾을 수 없습니다.")
            return

        all_tickers = headers[1:]
        print(f"📌 전체 티커 목록: {all_tickers}")

        # 2.5 기존 데이터 보존을 위해 현재 시트 데이터 읽기 (A3 ~ 끝)
        existing_records = safe_execute(worksheet.get_all_values)
        if existing_records and len(existing_records) > 2:
             # Header 길이만큼 자르기 (데이터가 더 길면 에러 발생 방지)
             header_len = len(headers)
             data_rows = [row[:header_len] for row in existing_records[2:]]
             existing_df = pd.DataFrame(data_rows, columns=headers)
             try:
                 existing_df.iloc[:, 0] = pd.to_datetime(existing_df.iloc[:, 0], errors='coerce').dt.strftime('%Y-%m-%d')
                 existing_df.set_index(headers[0], inplace=True)
                 for col in existing_df.columns:
                      existing_df[col] = pd.to_numeric(existing_df[col].astype(str).str.replace(',',''), errors='coerce')
             except:
                 existing_df = pd.DataFrame(columns=all_tickers)
        else:
             existing_df = pd.DataFrame(columns=all_tickers)
        
        # 기본적으로 기존 데이터를 유지
        final_df = existing_df.copy()

        # 3. 다운로드 대상 티커 분류
        tickers_to_download = []
        manual_tickers = []
        
        for t in all_tickers:
            clean_t = t.strip()
            if not clean_t: continue
            
            if '금현물' in clean_t or 'GOLD' in clean_t.upper() or '999980' in clean_t:
                manual_tickers.append(t)
                print(f"  Example: '{t}'는 수기 관리 항목이므로 다운로드를 건너뜁니다.")
            elif '#N/A' in clean_t or '#NUM!' in clean_t or '#REF!' in clean_t:
                print(f"  Skip: '{t}'는 유효하지 않은 티커(에러값)입니다.")
                continue
            else:
                code = clean_t
                if 'KRX:' in code: code = code.replace('KRX:', '')
                code = code.split('.')[0]
                tickers_to_download.append({'header': t, 'code': code})
        
        print(f"📥 다운로드 대상 ({len(tickers_to_download)}개): {[x['code'] for x in tickers_to_download]}")

        # 4. 데이터 다운로드
        if tickers_to_download:
            print(f"📥 FinanceDataReader 데이터 다운로드 중... ({START_DATE} ~ Today)")
            downloaded_data = {}
            
            for item in tickers_to_download:
                code = item['code']
                header_name = item['header']
                try:
                    d = fdr.DataReader(code, start=START_DATE)['Close']
                    downloaded_data[header_name] = d
                    time.sleep(0.1)
                except Exception as e:
                    print(f"  ⚠️ {code} ({header_name}) 다운로드 실패: {e}")

            if downloaded_data:
                new_df = pd.DataFrame(downloaded_data)
                new_df.index = new_df.index.strftime('%Y-%m-%d')
                
                # 기존 데이터 + 신규 데이터 병합
                combined_index = final_df.index.union(new_df.index).sort_values()
                final_df = final_df.reindex(combined_index)
                final_df.update(new_df) # 신규 데이터로 덮어쓰기 (NaN 제외)
                final_df = final_df.fillna(0)
                
                print(f"✅ 데이터 병합 완료: {len(final_df)}일치 데이터")
            else:
                print("❌ 유효한 다운로드 데이터가 없습니다. 기존 데이터를 유지합니다.")
        else:
            print("ℹ️ 다운로드할 티커가 없습니다.")

    except Exception as e:
        print(f"❌ 데이터 처리 중 오류: {e}"); traceback.print_exc()
        return

    # 4. 시트에 쓰기 준비
    print("💾 시트 기록 준비 중...")
    
    if final_df is None:
         print("❌ 최종 데이터가 없습니다.")
         return

    final_df.reset_index(inplace=True)
    final_df.rename(columns={'index': headers[0]}, inplace=True)
    
    # 필요한 컬럼만, 순서대로 추출
    cols_to_write = headers 
    
    # 혹시 날짜 컬럼 이름이 매칭 안되면 강제 할당
    if headers[0] not in final_df.columns:
        final_df.rename(columns={final_df.columns[0]: headers[0]}, inplace=True)
        
    final_df = final_df[cols_to_write]
    
    rows_to_write = []
    # 중복 컬럼(#N/A 등)으로 인한 데이터 폭증 방지를 위해 Header 길이만큼 강제 Slice
    header_len = len(headers)
    
    for _, row in final_df.iterrows():
        # 날짜 포맷팅 등은 이미 strftime으로 됨.
        # NaN 처리 등
        row_data = row.fillna('').tolist()
        # 데이터가 Header보다 많으면 자름 (중복 선택 이슈 방어)
        if len(row_data) > header_len:
            row_data = row_data[:header_len]
        rows_to_write.append(row_data)
        
    if rows_to_write:
        try:
            # 1. 기존 데이터 클리어 (3행부터 아래로)
            # A3부터 I열 끝까지 (또는 Z열까지 넉넉하게)
            # 데이터 양이 많지 않으므로 range를 특정해서 지우거나,
            # 아니면 3행부터 덮어쓰고 나머지는 그대로 둘지 결정.
            # 깔끔하게 하기 위해 3행 이하는 지우는게 좋음.
            
            # 시트 전체 행 수 확인
            # safe_execute(worksheet.resize, rows=len(rows_to_write)+2) # 필요시 리사이즈
            
            # 범위 업데이트 (A3 시작)
            end_row = 2 + len(rows_to_write)
            end_col_char = chr(ord('A') + len(all_tickers)) # A(0) + N -> 해당 알파벳
            
            range_str = f'A3:{end_col_char}{end_row}'
            
            print(f"  > 시트 업데이트 범위: {range_str}")
            safe_execute(worksheet.update, range_name=range_str, values=rows_to_write, value_input_option='USER_ENTERED')
            print("✅ 시트 업데이트 완료!")
            
        except Exception as e:
            print(f"❌ 시트 쓰기 실패: {e}")
    # [NEW] 자산 수량 정보 업데이트 (KYI_자산배분 -> ISA_종목별_수량_RAW)
    # returns: {'StockName': Qty, ...}
    holdings = update_asset_quantities(gc, target_date_str)
    
    # [MOVED] ISA 평가액 계산 로직은 portfolio_performance_google sheet.py 로 이동됨
    
def update_asset_quantities(gc, target_date_str=None):
    """KYI_자산배분 시트의 수량 정보를 읽어와 수량_RAW 파일에 날짜별로 누적 기록"""
    SRC_FILE = 'KYI_자산배분'
    SRC_SHEET = '자산배분'
    DST_FILE = '수량_RAW'
    DST_SHEET = '수량'
    
    if target_date_str is None:
        target_date_str = datetime.now().strftime('%Y-%m-%d')
    
    print(f"\n🚀 [{DST_FILE}/{DST_SHEET}] 수량 정보 스냅샷 업데이트 시작 (날짜: {target_date_str})")
    
    try:
        # 1. 소스 데이터 읽기 (KYI_자산배분 > 자산배분)
        # 종목명: A열 (idx 0), 수량: E열 (idx 4)
        print(f"📥 '{SRC_FILE}'에서 자산 수량 읽기...")
        src_ws = safe_execute(gc.open(SRC_FILE).worksheet, SRC_SHEET)
        src_data = safe_execute(src_ws.get_all_values)
        
        if not src_data:
            print("❌ 소스 데이터가 비어있습니다.")
            return

        current_holdings = {}
        # 헤더 건너뛰고 데이터 파싱 (보통 1행 헤더)
        for row in src_data[1:]:
            if len(row) > 4:
                try:
                    stock_name = row[0].strip()
                    qty_str = row[4].strip().replace(',', '')
                    if stock_name and qty_str:
                        qty = float(qty_str)
                        if qty > 0:
                            current_holdings[stock_name] = qty
                except ValueError:
                    continue # 숫자가 아닌 경우 패스

        if not current_holdings:
            print("❌ 유효한 보유 종목이 없습니다.")
            return {}

        print(f"📌 감지된 보유 종목 ({len(current_holdings)}개): {list(current_holdings.keys())}")

        # 2. 타겟 시트 열기 (ISA_종목별_수량_RAW > 수량)
        try:
            dst_spread = safe_execute(gc.open, DST_FILE)
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"❌ '{DST_FILE}' 파일을 찾을 수 없습니다.")
            print("🔍 접근 가능한 시트 목록:")
            try:
                for f in gc.openall(): print(f"  - {f.title} (ID: {f.id})")
            except: pass
            return {}

        try:
            dst_ws = safe_execute(dst_spread.worksheet, DST_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️ '{DST_SHEET}' 시트가 없어 생성합니다.")
            dst_ws = safe_execute(dst_spread.add_worksheet, title=DST_SHEET, rows=1000, cols=20)
            
        # 3. 헤더 확인 및 업데이트
        existing_data = safe_execute(dst_ws.get_all_values)
        if not existing_data:
            # 헤더 없음: 생성
            headers = ['Date'] + sorted(list(current_holdings.keys()))
            safe_execute(dst_ws.append_row, headers)
            existing_data = [headers]
        
        headers = existing_data[0]
        
        # 새 종목이 있으면 헤더에 추가 (A열 Date 다음부터)
        existing_stocks = headers[1:] # A열(Date) 제외
        new_stocks = [s for s in current_holdings.keys() if s not in existing_stocks]
        
        if new_stocks:
            print(f"🆕 새로운 종목 발견: {new_stocks} -> 헤더 추가")
            # 헤더 업데이트 로직 (간단히 끝에 추가)
            # 주의: 중간에 끼워넣으려면 전체 데이터를 밀어야 함. 여기선 끝에 추가.
            # get_all_values로 가져온 데이터는 리스트의 리스트.
            # 데이터프레임으로 처리하는 게 깔끔함.
            
            # 기존 데이터 DF 변환
            df_old = pd.DataFrame(existing_data[1:], columns=headers) if len(existing_data) > 1 else pd.DataFrame(columns=headers)
            
            # 새 컬럼 추가
            for s in new_stocks:
                df_old[s] = 0.0 # 기존 행에는 0 채움
                headers.append(s)
            
            # 헤더 업데이트 (1행 전체)
            safe_execute(dst_ws.update, range_name='A1', values=[headers], value_input_option='USER_ENTERED')
            print("✅ 헤더 업데이트 완료")

        # 4. 오늘 데이터 행 생성
        # 4. 오늘 데이터 행 생성 (전달받은 날짜 사용)
        today_str = target_date_str
        
        # 날짜 중복 체크 (마지막 행 확인)
        last_row = existing_data[-1] if len(existing_data) > 1 else []
        is_update = False
        target_row_idx = len(existing_data) + 1 # 1-based, append location
        
        if last_row and last_row[0] == today_str:
            print(f"ℹ️ 오늘({today_str}) 데이터가 이미 존재합니다. 업데이트(덮어쓰기) 합니다.")
            is_update = True
            target_row_idx = len(existing_data) # 마지막 행 위치
        
        new_row = [today_str]
        for col_name in headers[1:]:
            qty = current_holdings.get(col_name, 0.0)
            new_row.append(qty)
            
        # 5. 시트 기록
        if is_update:
            # 마지막 행 업데이트
            safe_execute(dst_ws.update, range_name=f'A{target_row_idx}', values=[new_row], value_input_option='USER_ENTERED')
            print(f"✅ {today_str} 데이터 업데이트 완료")
        else:
            # 행 추가
            safe_execute(dst_ws.append_row, new_row, value_input_option='USER_ENTERED')
            print(f"✅ {today_str} 데이터 추가 완료")

        return current_holdings

    except Exception as e:
        print(f"❌ 수량 정보 업데이트 실패: {e}")
        traceback.print_exc()
        return {}

if __name__ == '__main__':
    main()
