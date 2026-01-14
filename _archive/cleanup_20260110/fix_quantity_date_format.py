import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import os
import sys
import time
from datetime import datetime

# --- 설정 ---
GOOGLE_SHEET_NAME = '수량_RAW'
WORKSHEET_NAME = '수량'

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

    print(f"\n--- '{GOOGLE_SHEET_NAME}/{WORKSHEET_NAME}' 날짜 포맷 수정 시작 ---")
    
    try:
        spreadsheet = safe_execute(gc.open, GOOGLE_SHEET_NAME)
        worksheet = safe_execute(spreadsheet.worksheet, WORKSHEET_NAME)
        
        # 데이터 읽기
        data = safe_execute(worksheet.get_all_values)
        if not data:
            print("❌ 데이터가 없습니다.")
            return

        header = data[0]
        rows = data[1:]
        
        print(f"📥 데이터 {len(rows)}행 읽음. 날짜 변환 및 재업로드 준비...")
        
        # A열(날짜) 변환
        # 이미 텍스트로 yyyy-mm-dd 형태일 가능성이 높지만, 확실하게 하기 위해 datetime 변환 후 다시 string화
        cleaned_rows = []
        for row in rows:
            if not row: continue
            date_val = row[0]
            other_vals = row[1:]
            
            # 날짜 파싱 시도
            try:
                dt = pd.to_datetime(date_val, errors='coerce')
                if pd.notna(dt):
                    new_date_str = dt.strftime('%Y-%m-%d')
                else:
                    new_date_str = date_val # 변환 실패 시 원본 유지
            except:
                new_date_str = date_val
                
            cleaned_rows.append([new_date_str] + other_vals)
            
        # 전체 데이터 재구성
        all_values = [header] + cleaned_rows
        
        # 업데이트 (USER_ENTERED 옵션 사용)
        print("💾 USER_ENTERED 옵션으로 데이터 덮어쓰기 중...")
        safe_execute(worksheet.clear)
        safe_execute(worksheet.update, range_name='A1', values=all_values, value_input_option='USER_ENTERED')
        
        # 포맷팅 적용 (A열)
        # 1행(헤더) 제외
        print("🎨 A열 날짜 포맷팅 적용 (yyyy-mm-dd)...")
        try:
            # gspread format
            worksheet.format(f"A2:A{len(all_values)}", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
        except Exception as e_fmt:
            print(f"⚠️ 포맷팅 적용 실패 (데이터는 업데이트됨): {e_fmt}")

        print("✅ 완료!")

    except Exception as e:
        print(f"🔥 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
