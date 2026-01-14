# telegram_scan.py
# (Local Version) Scan Telegram updates and update Google Sheet (Gold Price etc)

import os
import sys
import json
import re
import requests
import traceback
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# --- Utils ---
try:
    import telegram_utils
except ImportError:
    print("⚠️ telegram_utils module not found.")
    sys.exit(1)

# --- Configuration ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
WORKSHEET_NAME = '🗓️매매일지'
SETTINGS_SHEET = '⚙️설정'
TARGET_CELL_FOR_NUMBER = 'J9'
OFFSET_CELL = 'J12'
OFFSET_DESC_CELL = 'J13'
OFFSET_DESC_TEXT = '텔레그램 메시지 처리 번호 (삭제금지)'
JSON_KEYFILE_PATH = 'stock-auto-writer-44eaa06c140c.json'

# --- Retry Helper ---
def safe_execute(func, *args, retries=3, sleep=2, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "Quota exceeded" in str(e):
                time.sleep(sleep * (i + 1))
            else:
                if i == retries - 1: raise e
                time.sleep(1)
    return None

# --- Parsing Logic ---
def parse_transaction_message(text):
    # (Simplified for local use - keeping essentially the same logic)
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    if not lines: return None
    
    parsed = {}
    # Basic logic to distinguish Hantoo/Kiwoom based on keywords
    is_hantoo = "[한투]" in text or "한국투자" in text
    is_kiwoom = "[키움]" in text or "키움증권" in text

    try:
        if is_hantoo:
            # Hantoo Logic
            action_line = next((l for l in lines if "체결" in l), None)
            if not action_line: return None
            parsed['구분'] = "매수" if "매수" in action_line else "매도"
            idx = lines.index(action_line)
            parsed['종목명'] = lines[idx+1].replace(" ", "")
            code_m = re.search(r"\(([A-Z]?\d+)\)", lines[idx+2])
            parsed['종목코드'] = code_m.group(1) if code_m else ""
            qty_m = re.search(r"([\d,]+)\s*주", lines[idx+3])
            parsed['수량'] = int(qty_m.group(1).replace(',', '')) if qty_m else 0
            price_m = re.search(r"([\d,]+)\s*원", lines[idx+4])
            parsed['단가'] = int(price_m.group(1).replace(',', '')) if price_m else 0
            parsed['계좌'] = "한투_연금" if parsed['종목명'] == "TIGER미국S&P500" else "한투_IRP"
            
        elif is_kiwoom:
            # Kiwoom Logic
            parsed['종목명'] = lines[1].replace(" ", "")
            qty_line = lines[2]
            parsed['구분'] = "매수" if "매수" in qty_line else "매도"
            qty_m = re.search(r"([\d,]+)\s*주", qty_line)
            parsed['수량'] = int(qty_m.group(1).replace(',', '')) if qty_m else 0
            price_line = lines[3]
            price_m = re.search(r"([\d,]+)\s*원", price_line)
            parsed['단가'] = int(price_m.group(1).replace(',', '')) if price_m else 0
            parsed['계좌'] = "키움_ISA"
            parsed['종목코드'] = ""
            
        else:
            return None

        parsed['금액'] = parsed.get('수량', 0) * parsed.get('단가', 0)
        parsed['날짜'] = datetime.now().strftime("%Y-%m-%d")
        return parsed
    except Exception:
        return None

# --- Main ---
def main():
    print(f"--- Telegram Update Scan ({datetime.now().strftime('%H:%M:%S')}) ---")
    
    # 1. Connect Sheet
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
        gc = safe_execute(gspread.authorize, creds)
        spreadsheet = safe_execute(gc.open, GOOGLE_SHEET_NAME)
        settings_ws = safe_execute(spreadsheet.worksheet, SETTINGS_SHEET)
        # Ensure description exists
        safe_execute(settings_ws.update_acell, OFFSET_DESC_CELL, OFFSET_DESC_TEXT)
    except Exception as e:
        print(f"❌ Sheet Connection Failed: {e}")
        return

    # 2. Get Telegram Token
    token, _ = telegram_utils.get_telegram_credentials()
    if not token: 
        print("❌ No Telegram Token.")
        return

    # 3. Get Offset
    try:
        last_offset = safe_execute(settings_ws.acell, OFFSET_CELL).value
        offset = int(last_offset) + 1 if last_offset and last_offset.isdigit() else 0
    except: offset = 0
    print(f"ℹ️ Offset: {offset}")

    # 4. Fetch Updates
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        res = requests.get(url, params={'offset': offset, 'timeout': 5}).json()
    except Exception as e:
        print(f"❌ Telegram API Failed: {e}")
        return

    updates = res.get('result', [])
    if not updates:
        print("✅ No new messages.")
        return

    print(f"📥 Found {len(updates)} updates.")
    trade_sheet = safe_execute(spreadsheet.worksheet, WORKSHEET_NAME)
    max_id = 0
    processed = 0

    for u in updates:
        uid = u['update_id']
        max_id = max(max_id, uid)
        msg = u.get('message') or u.get('edited_message')
        if not msg: continue
        text = msg.get('text', '').strip()
        if not text: continue

        # A. Numeric (Gold Price)
        if re.match(r'^\d+(\.\d+)?$', text):
            val = float(text)
            safe_execute(settings_ws.update_acell, TARGET_CELL_FOR_NUMBER, val)
            print(f"  ✅ Gold Price Updated: {val}")
            processed += 1
        
        # B. Trade Log
        else:
            parsed = parse_transaction_message(text)
            if parsed:
                row = [
                    parsed.get("날짜", ""), parsed.get("종목명", ""), "", parsed.get("구분", ""),
                    parsed.get("단가", ""), parsed.get("수량", ""), parsed.get("금액", ""),
                    parsed.get("계좌", "미분류"), "", parsed.get("종목코드", "")
                ]
                safe_execute(trade_sheet.append_row, row, value_input_option='USER_ENTERED')
                print(f"  ✅ Trade Logged: {parsed['종목명']}")
                processed += 1

    # 5. Save Offset
    if max_id > 0:
        safe_execute(settings_ws.update_acell, OFFSET_CELL, max_id)
        print("✅ Offset Updated.")
    
    if processed:
        telegram_utils.send_telegram_message(f"✅ [Local] Processed {processed} messages (Gold/Trade).")

if __name__ == '__main__':
    main()
