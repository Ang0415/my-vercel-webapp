import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from datetime import datetime
import holidays # 공휴일 확인용 라이브러리
import os
import traceback

# --- 설정 ---
# JSON 키 파일 경로 (현재 스크립트 파일 기준)
JSON_KEYFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock-auto-writer-44eaa06c140c.json')
GOOGLE_SHEET_NAME = 'KYI_자산배분' # 대상 스프레드시트 이름
# 확인할 시트 이름 목록
SHEET_NAMES = ['📈ISA 수익률', '📈IRP 수익률', '📈연금 수익률', '📈금현물 수익률']
DATE_COLUMN_INDEX = 1 # 날짜가 있는 열 번호 (A열 = 1)
# ---

def find_closed_market_days_in_sheets():
    """각 시트 A열에서 주말 또는 공휴일 날짜를 찾아 출력합니다."""
    closed_days_found = {} # 결과를 저장할 딕셔너리 {sheet_name: [date1_str, date2_str, ...]}

    # 1. Google Sheet 연결
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if not os.path.exists(JSON_KEYFILE_PATH):
            print(f"오류: 서비스 계정 키 파일을 찾을 수 없습니다: {JSON_KEYFILE_PATH}")
            return None
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        print("✅ Google Sheets 연결 성공.")
    except Exception as e:
        print(f"❌ Google Sheets 연결 실패: {e}")
        traceback.print_exc()
        return None

    all_dates_parsed = [] # 모든 시트의 날짜를 모아 연도 범위 확인용

    # 2. 각 시트에서 날짜 데이터 읽기 및 파싱
    sheet_dates_parsed = {} # 시트별 파싱된 날짜 저장 {sheet_name: [date_obj1, ...]}
    for sheet_name in SHEET_NAMES:
        try:
            print(f"\n📄 시트 '{sheet_name}' 처리 중...")
            worksheet = spreadsheet.worksheet(sheet_name)
            # A열 전체 값 가져오기
            date_values_raw = worksheet.col_values(DATE_COLUMN_INDEX)
            if not date_values_raw:
                print(f"  - 정보: '{sheet_name}' 시트의 A열이 비어 있습니다.")
                continue

            parsed_dates_in_sheet = []
            for i, date_str in enumerate(date_values_raw):
                # 간단하게 헤더 '날짜' 문자열 포함 시 건너뛰기
                if i == 0 and isinstance(date_str, str) and '날짜' in date_str.strip():
                    continue
                if not date_str: # 빈 셀 건너뛰기
                    continue

                # 날짜 변환 시도 (다양한 형식 지원)
                dt_obj = None
                possible_formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"] # 필요시 다른 형식 추가
                current_date_str = str(date_str).strip() # 공백 제거
                for fmt in possible_formats:
                    try:
                        # datetime 객체로 변환 후 date 객체만 사용
                        dt_obj = datetime.strptime(current_date_str, fmt).date()
                        break # 성공 시 중단
                    except ValueError:
                        continue # 실패 시 다음 형식 시도

                if dt_obj:
                    parsed_dates_in_sheet.append(dt_obj)
                    all_dates_parsed.append(dt_obj)
                # else: # 파싱 실패 경고는 너무 많을 수 있어 일단 주석 처리
                    # print(f"  - ⚠️ 경고: '{sheet_name}' 시트 {i+1}행 값 '{current_date_str}' 날짜 변환 실패.")

            if parsed_dates_in_sheet:
                sheet_dates_parsed[sheet_name] = parsed_dates_in_sheet
                print(f"  - 날짜 {len(parsed_dates_in_sheet)}개 파싱 완료.")
            else:
                print(f"  - 정보: '{sheet_name}' 시트에서 유효한 날짜 데이터를 찾지 못했습니다.")

        except gspread.exceptions.WorksheetNotFound:
            print(f"  - ❌ 오류: '{sheet_name}' 워크시트를 찾을 수 없습니다.")
        except Exception as e:
            print(f"  - ❌ 오류: '{sheet_name}' 처리 중 오류 발생: {e}")
            traceback.print_exc()

    # 3. 공휴일 정보 로드 (데이터가 있는 경우에만)
    if not all_dates_parsed:
        print("\nℹ️ 유효한 날짜 데이터가 없어 공휴일 검사를 진행할 수 없습니다.")
        return {}

    min_year = min(d.year for d in all_dates_parsed)
    max_year = max(d.year for d in all_dates_parsed)
    try:
        # 대한민국 공휴일 정보 로드
        kr_holidays = holidays.KR(years=range(min_year, max_year + 1), observed=True)
        print(f"\n✅ {min_year}년 ~ {max_year}년 한국 공휴일 정보 로드 완료.")
    except Exception as e:
        print(f"❌ 공휴일 정보 로드 실패: {e}. 주말만 검사합니다.")
        kr_holidays = None # 공휴일 로드 실패 시 None 처리

    # 4. 휴장일(주말 또는 공휴일) 검사
    print("\n️️🔍 휴장일 데이터 검사 시작...")
    for sheet_name, dates_in_sheet in sheet_dates_parsed.items():
        closed_days_in_sheet = []
        for check_date in dates_in_sheet:
            weekday = check_date.weekday() # 월요일=0, 일요일=6
            is_weekend = weekday >= 5 # 토요일(5) 또는 일요일(6)
            is_holiday = check_date in kr_holidays if kr_holidays else False

            if is_weekend or is_holiday:
                reason = "주말" if is_weekend else "공휴일"
                # 발견된 날짜를 YYYY-MM-DD 형식의 문자열로 저장
                closed_days_in_sheet.append(f"{check_date.strftime('%Y-%m-%d')} ({reason})")

        if closed_days_in_sheet:
            # 중복 제거 후 저장 (같은 날짜가 여러 번 나올 경우 대비)
            closed_days_found[sheet_name] = sorted(list(set(closed_days_in_sheet)))
            print(f"  - '{sheet_name}': 휴장일 {len(closed_days_found[sheet_name])}건 발견.")
        else:
            print(f"  - '{sheet_name}': 휴장일 데이터 없음.")

    return closed_days_found

# --- 스크립트 실행 ---
if __name__ == "__main__":
    results = find_closed_market_days_in_sheets()
    print("\n" + "="*30)
    print("      결과 요약")
    print("="*30)
    if results is None:
        print("오류로 인해 검사를 완료하지 못했습니다.")
    elif not results:
        print("모든 검사 대상 시트의 A열에서 주말 또는 공휴일 날짜가 발견되지 않았습니다.")
    else:
        print("❗ 아래 시트의 A열에서 주말 또는 공휴일 날짜가 발견되었습니다:")
        for sheet, dates in results.items():
            print(f"\n📄 시트: [ {sheet} ]")
            if dates:
                for date_info in dates:
                    print(f"  - {date_info}")
            else:
                # 이 경우는 거의 없지만, 혹시 몰라 추가
                print("  (발견된 휴장일 없음)")
    print("\n" + "="*30)