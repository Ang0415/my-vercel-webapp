
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import os
import sys

# Encoding fix for Windows
sys.stdout.reconfigure(encoding='utf-8')

# Config
JSON_KEYFILE_PATH = 'stock-auto-writer-44eaa06c140c.json'
TARGET_ACCOUNTS = ['금현물', '연금']
FILES_TO_CHECK = {
    '성과_TWR_Raw': ['날짜', '계좌명', 'TWR'],
    '성과_자산추이_Raw': ['Date', 'Value'] # Or Date, Value columns from simple sheet
}

def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(credentials)
        return gc
    except Exception as e:
        print(f"❌ 접속 실패: {e}")
        return None

def check_sheet_data(gc, file_name, accounts):
    print(f"\n📂 파일 점검: {file_name}")
    try:
        sh = gc.open(file_name)
    except Exception as e:
        print(f"  ❌ 파일을 열 수 없습니다: {e}")
        return

    for acc in accounts:
        print(f"  User Account: {acc}")
        # Try to find sheet with robust matching
        found_ws = None
        for ws in sh.worksheets():
            if ws.title.strip() == acc:
                found_ws = ws
                break
        
        if not found_ws:
            print(f"    ⚠️ 시트를 찾을 수 없습니다: {acc}")
            continue

        data = found_ws.get_all_values()
        if len(data) < 2:
            print("    ⚠️ 데이터가 없습니다.")
            continue
            
        header = data[0]
        df = pd.DataFrame(data[1:], columns=header)
        
        # Determine Date and Value columns
        date_col = next((c for c in df.columns if '날짜' in c or 'Date' in c or 'date' in c), None)
        
        if 'TWR' in df.columns: val_col = 'TWR'
        elif 'Value' in df.columns: val_col = 'Value'
        else: val_col = df.columns[-1] # Fallback
        
        if not date_col:
            print("    ⚠️ 날짜 컬럼을 찾을 수 없습니다.")
            continue

        print(f"    - 컬럼 감지: Date='{date_col}', Value='{val_col}'")
        
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df[val_col] = pd.to_numeric(df[val_col].astype(str).str.replace(',', ''), errors='coerce')
        
        df = df.dropna(subset=[date_col]).sort_values(date_col)
        
        # 1. 0 or Negative Check
        zeros = df[df[val_col] <= 0]
        if not zeros.empty:
            print(f"    ⚠️ 0 또는 음수 값 발견 ({len(zeros)}건):")
            print(zeros[[date_col, val_col]].head(5).to_string(index=False))
            
        # 2. Sudden Change Check (Day-over-Day > 50%)
        df['Prev'] = df[val_col].shift(1)
        df['Change'] = ((df[val_col] - df['Prev']).abs() / df['Prev'].replace(0, 1)) * 100
        
        spikes = df[df['Change'] > 50]
        if not spikes.empty:
            print(f"    ⚠️ 급격한 변동(>50%) 발견 ({len(spikes)}건):")
            for _, row in spikes.head(10).iterrows():
                print(f"      {row[date_col].date()}: {row['Prev']:.2f} -> {row[val_col]:.2f} ({row['Change']:.1f}%)")
        
        # 3. Duplicate Dates
        dups = df[df.duplicated(subset=[date_col], keep=False)]
        if not dups.empty:
            print(f"    ⚠️ 중복 날짜 발견 ({len(dups)}건) - 예: {dups[date_col].iloc[0].date()}")

        # 4. Show Recent Data
        print(f"    ℹ️ 최근 데이터 (5건):")
        print(df[[date_col, val_col]].tail(5).to_string(index=False))

def main():
    gc = connect_google_sheets()
    if not gc: return

    check_sheet_data(gc, '성과_TWR_Raw', TARGET_ACCOUNTS)
    check_sheet_data(gc, '성과_자산추이_Raw', TARGET_ACCOUNTS)

if __name__ == "__main__":
    main()
