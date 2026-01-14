# telegram_to_sheet_git_action.py
# (GitHub Actions용) 텔레그램 메시지 파싱 및 구글시트 업데이트
# - 매매 알림 문자 -> '매매일지' 시트 추가
# - 숫자 메시지 -> '설정'!J9 (금현물 등) 업데이트
# - 마지막 Update ID -> '설정'!Z1 저장 (Offset 관리)

import os
import sys
import json
import re
import requests
import traceback
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Git Action Utils ---
try:
    import telegram_utils_git_action as telegram_utils
    print("✅ [Init] telegram_utils_git_action 로드 성공")
except ImportError:
    try:
        import telegram_utils
        print("✅ [Init] telegram_utils 로드 성공")
    except ImportError:
        # Fallback for local testing logic
        class MockUtils:
            def get_telegram_credentials(self):
                return os.environ.get('TELEGRAM_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
            def send_telegram_message(self, msg):
                print(f"[Telegram Mock] {msg}")
        telegram_utils = MockUtils()

# --- Configuration ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
WORKSHEET_NAME = '🗓️매매일지'
SETTINGS_SHEET = '⚙️설정'
TARGET_CELL_FOR_NUMBER = 'J9' # 숫자 입력 시 업데이트할 셀
OFFSET_CELL = 'Z1'            # 마지막 Update ID 저장 셀

# --- Parsing Logic (Ported from telegram_sheet_bot.py) ---

def parse_hantoo_message(lines):
    """한국투자증권 문자 분석"""
    parsed_data = {}
    action_line = ""
    for line in lines:
        if "매수체결" in line: parsed_data['구분'] = "매수"; action_line = line; break
        elif "매도체결" in line: parsed_data['구분'] = "매도"; action_line = line; break
    
    if '구분' not in parsed_data: return None

    try:
        action_index = lines.index(action_line)
        if len(lines) > action_index + 2:
            original_name = lines[action_index + 1].strip()
            parsed_data['종목명'] = original_name.replace(" ", "")
            code_match = re.search(r"\(([A-Z]?\d+)\)", lines[action_index + 2])
            parsed_data['종목코드'] = code_match.group(1) if code_match else None
        else: return None

        if len(lines) > action_index + 3:
            qty_match = re.search(r"([\d,]+)\s*주", lines[action_index + 3])
            parsed_data['수량'] = int(qty_match.group(1).replace(',', '')) if qty_match else None
        else: return None

        if len(lines) > action_index + 4:
            price_match = re.search(r"([\d,]+)\s*원", lines[action_index + 4])
            parsed_data['단가'] = int(price_match.group(1).replace(',', '')) if price_match else None
        else: return None

        if parsed_data['수량'] is None or parsed_data['단가'] is None: return None

        parsed_data['금액'] = parsed_data['수량'] * parsed_data['단가']
        parsed_data['날짜'] = datetime.now().strftime("%Y-%m-%d")
        
        if parsed_data.get('종목명') == "TIGER미국S&P500": parsed_data['계좌'] = "한투_연금"
        else: parsed_data['계좌'] = "한투_IRP"
        
        return parsed_data
    except: return None

def parse_kiwoom_message(lines):
    """키움증권 문자 분석"""
    parsed_data = {}
    try:
        if len(lines) < 4: return None
        original_name = lines[1].strip()
        parsed_data['종목명'] = original_name.replace(" ", "")
        parsed_data['종목코드'] = None # 키움 문자는 코드 없음
        
        action_qty_line = lines[2].strip()
        action_qty_match = re.match(r"(매수|매도)\s*([\d,]+)\s*주", action_qty_line)
        if action_qty_match:
            parsed_data['구분'] = action_qty_match.group(1)
            parsed_data['수량'] = int(action_qty_match.group(2).replace(',', ''))
        else: return None
        
        price_line = lines[3].strip()
        price_match = re.search(r"(?:평균)?단가\s*([\d,]+)\s*원?", price_line)
        if price_match:
            parsed_data['단가'] = int(price_match.group(1).replace(',', ''))
        else: return None
        
        parsed_data['금액'] = parsed_data['수량'] * parsed_data['단가']
        parsed_data['날짜'] = datetime.now().strftime("%Y-%m-%d")
        parsed_data['계좌'] = "키움_ISA"
        
        return parsed_data
    except: return None

def parse_transaction_message(text):
    """메시지 파싱 분기"""
    lines = [line.strip() for line in text.strip().split('\n') if line.strip() and "[Web발신]" not in line]
    if not lines: return None
    
    if "[한투]" in text or "한국투자증권" in text: return parse_hantoo_message(lines)
    elif "[키움]" in text or "키움증권" in text: return parse_kiwoom_message(lines)
    return None

# --- Main Logic ---
def connect_google_sheets():
    try:
        gcs_json_str = os.environ.get('GCS_CREDENTIALS')
        if not gcs_json_str:
            # Local fallback
            if os.path.exists('stock-auto-writer-44eaa06c140c.json'):
                scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_name('stock-auto-writer-44eaa06c140c.json', scope)
                return gspread.authorize(creds)
            return None
        key_dict = json.loads(gcs_json_str)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
        return gspread.authorize(credentials)
    except Exception as e:
        print(f"❌ 구글 시트 연결 오류: {e}")
        return None

def main():
    print(f"--- 텔레그램 메시지 수집 시작 ({datetime.now()}) ---")
    
    # 1. Init
    gc = connect_google_sheets()
    if not gc: return
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    
    token, _ = telegram_utils.get_telegram_credentials()
    if not token: 
        print("❌ Telegram Token 없음")
        return

    # 2. Get Last Offset
    settings_ws = spreadsheet.worksheet(SETTINGS_SHEET)
    try:
        last_offset_val = settings_ws.acell(OFFSET_CELL).value
        offset = int(last_offset_val) + 1 if last_offset_val and last_offset_val.isdigit() else 0
    except: offset = 0
    
    print(f"ℹ️ Last Offset: {offset}")

    # 3. Fetch Updates (Requests)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {'offset': offset, 'timeout': 10}
    try:
        res = requests.get(url, params=params).json()
    except Exception as e:
        print(f"❌ Telegram API Error: {e}")
        return

    if not res.get('ok'):
        print(f"❌ Telegram Error: {res}")
        return

    updates = res.get('result', [])
    if not updates:
        print("ℹ️ 새로운 메시지가 없습니다.")
        return

    print(f"📥 {len(updates)}개의 새 메시지 발견.")
    
    max_update_id = 0
    processed_count = 0
    
    trade_sheet = spreadsheet.worksheet(WORKSHEET_NAME)
    
    for update in updates:
        update_id = update['update_id']
        max_update_id = max(max_update_id, update_id)
        
        message = update.get('message') or update.get('edited_message')
        if not message: continue
        
        text = message.get('text', '').strip()
        if not text: continue
        
        # A. Numeric Input (Gold Price etc) -> Update Cell
        if re.match(r'^\d+(\.\d+)?$', text):
            # 정수 또는 실수
            val = int(text) if text.isdigit() else float(text)
            settings_ws.update_acell(TARGET_CELL_FOR_NUMBER, val)
            print(f"  ✅ 설정 시트 업데이트 ({TARGET_CELL_FOR_NUMBER}): {val}")
            processed_count += 1
            
        # B. Trade Log -> Append Row
        else:
            parsed = parse_transaction_message(text)
            if parsed:
                # [날짜, 종목명, "", 구분, 단가, 수량, 금액, 계좌, "", 종목코드]
                row = [
                    parsed.get("날짜", ""), parsed.get("종목명", ""), "", parsed.get("구분", ""),
                    parsed.get("단가", ""), parsed.get("수량", ""), parsed.get("금액", ""),
                    parsed.get("계좌", "미분류"), "", parsed.get("종목코드", "")
                ]
                trade_sheet.append_row(row, value_input_option='USER_ENTERED')
                
                # Add Formulas for C and I cols (Last Row)
                last_row = len(trade_sheet.get_all_values())
                f_c = f'=IFERROR(VLOOKUP(B{last_row},\'⚙️설정\'!Q:R,2,FALSE),"미분류")'
                f_i = f'=IFERROR(VLOOKUP(B{last_row},\'⚙️설정\'!Q:S,3,FALSE),"미분류")'
                trade_sheet.update_acell(f'C{last_row}', f_c)
                trade_sheet.update_acell(f'I{last_row}', f_i)
                
                print(f"  ✅ 매매일지 추가: {parsed['종목명']} ({parsed['구분']})")
                processed_count += 1
            else:
                print(f"  ⚠️ 파싱 실패/무시된 메시지: {text[:20]}...")

    # 4. Save New Offset
    if max_update_id > 0:
        settings_ws.update_acell(OFFSET_CELL, max_update_id)
        print(f"ℹ️ New Offset 저장 완료: {max_update_id}")
        
    if processed_count > 0:
        telegram_utils.send_telegram_message(f"✅ [GitAction] 텔레그램 메시지 {processed_count}건 처리 완료.")

if __name__ == '__main__':
    main()
