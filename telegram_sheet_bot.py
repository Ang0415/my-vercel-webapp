# -*- coding: utf-8 -*-
# telegram_sheet_bot.py
# (텔레그램 봇: 1회 실행 후 종료 방식)
# - 실행 시점까지 쌓인 메시지(체결 알림, 설정값)를 일괄 처리하고 종료함.
# - 스케줄러(Windows Task Scheduler 등)로 주기적 실행 권장.

import logging
import traceback
import re
from datetime import datetime
import os
import sys
import asyncio
import yaml
import requests

# 텔레그램 라이브러리 (Bot 직접 사용)
try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("오류: 'python-telegram-bot' 라이브러리가 설치되지 않았습니다.")
    print("설치 방법: pip install python-telegram-bot")
    sys.exit(1)

# 구글 시트 라이브러리
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError:
    print("오류: 'gspread' 또는 'oauth2client' 라이브러리가 설치되지 않았습니다.")
    print("설치 방법: pip install gspread oauth2client")
    sys.exit(1)

# --- 설정 및 전역 변수 ---
GOOGLE_SHEET_NAME = 'KYI_자산배분'
WORKSHEET_NAME = '🗓️매매일지'
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_KEYFILE_PATH = os.path.join(CURRENT_DIR, 'stock-auto-writer-44eaa06c140c.json')
CONFIG_PATH = os.path.join(CURRENT_DIR, 'telegram_config.yaml')
SCRIPT_NAME = os.path.basename(__file__)

TARGET_SHEET_FOR_NUMBER = '⚙️설정'
TARGET_CELL_FOR_NUMBER = 'J9'

_telegram_config = {}

# --- Logging 설정 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 통합된 텔레그램 유틸리티 함수들 ---

def load_telegram_config():
    """텔레그램 설정 파일(telegram_config.yaml)을 로드합니다."""
    global _telegram_config
    if _telegram_config: return True
    
    # 설정 파일이 있으면 로드
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='UTF-8') as f:
                _telegram_config = yaml.load(f, Loader=yaml.FullLoader) or {}
            if _telegram_config:
                logger.info(f"✅ 텔레그램 설정 로드 완료: {CONFIG_PATH}")
                return True
        except Exception as e:
            logger.error(f"❌ 텔레그램 설정 파일 로드 실패: {e}")
            
    return False

def get_telegram_credentials():
    """봇 토큰과 Chat ID를 반환합니다. (환경변수 우선)"""
    # 1. 환경변수 확인 (GitHub Actions 등)
    env_token = os.environ.get('TELEGRAM_TOKEN')
    env_chat_id = os.environ.get('TELEGRAM_TO') or os.environ.get('TELEGRAM_CHAT_ID')
    
    if env_token and env_chat_id:
        return env_token, env_chat_id

    # 2. Config 파일 확인
    if not _telegram_config: load_telegram_config()
    return _telegram_config.get('bot_token'), _telegram_config.get('chat_id')

def send_telegram_message(message):
    """(자체 발송용) 텔레그램 메시지 전송"""
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token or not chat_id: return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"텔레그램 발송 실패: {e}")

# --- 구글 시트 함수 ---

def setup_google_sheet():
    """구글 시트 연결"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # 키 파일 내용이 환경변수에 있으면 파일 생성 (GitHub Actions)
        if not os.path.exists(JSON_KEYFILE_PATH) and os.environ.get("GCP_JSON_KEY_CONTENT"):
            with open(JSON_KEYFILE_PATH, 'w', encoding='utf-8') as f:
                f.write(os.environ.get("GCP_JSON_KEY_CONTENT"))
                
        if os.path.exists(JSON_KEYFILE_PATH):
            credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE_PATH, scope)
            gc = gspread.authorize(credentials)
            return gc, gc.open(GOOGLE_SHEET_NAME)
        else:
            logger.error(f"키 파일({JSON_KEYFILE_PATH})을 찾을 수 없습니다.")
            return None, None
    except Exception as e:
        logger.error(f"구글 시트 연결 오류: {e}")
        return None, None

def append_to_sheet(spreadsheet, data):
    """매매일지 시트 추가"""
    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        row_to_append = [
            data.get("날짜", ""), data.get("종목명", ""), "", data.get("구분", ""),
            data.get("단가", ""), data.get("수량", ""), data.get("금액", ""),
            data.get("계좌", "미분류계좌"), "", data.get("종목코드", "")
        ]
        worksheet.append_row(row_to_append, value_input_option='USER_ENTERED')
        
        # 수식 자동 채우기 (마지막 행)
        last_row = len(worksheet.get_all_values()) # append 직후이므로 정확함
        # C열(분류), I열(자산종류) 수식 복구
        worksheet.update_acell(f'C{last_row}', f'=IFERROR(VLOOKUP(B{last_row},\'⚙️설정\'!Q:R,2,FALSE),"미분류")')
        worksheet.update_acell(f'I{last_row}', f'=IFERROR(VLOOKUP(B{last_row},\'⚙️설정\'!Q:S,3,FALSE),"미분류")')
        
        return True, f"✅ '{data.get('종목명')}' ({data.get('구분')}) 입력 완료"
    except Exception as e:
        logger.error(f"시트 추가 오류: {e}")
        return False, "❌ 시트 추가 중 오류 발생"

def update_gold_closing_price(gc, price):
    """'종가_RAW' 파일의 '종가관리' 시트 M열(금현물) 업데이트"""
    PRICE_FILE = '종가_RAW'
    PRICE_SHEET = '종가관리'
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # 파일 및 시트 열기
        sh = gc.open(PRICE_FILE)
        ws = sh.worksheet(PRICE_SHEET)
        
        # 날짜 컬럼(A열) 확인
        dates = ws.col_values(1)
        
        # 오늘 날짜 행 찾기
        try:
            # col_values는 1-based index가 리스트 인덱스와 같음 (0번이 1행)
            # 날짜 헤더가 있을 수 있으므로 정확한 매칭 필요
            row_idx = dates.index(today_str) + 1 
            
            # M열(13번째 열) 업데이트
            ws.update_cell(row_idx, 13, price)
            return True, f"✅ 금 종가 반영 완료: {today_str} -> {price}"
        except ValueError:
            return False, f"⚠️ 금 종가 반영 실패: 오늘 날짜({today_str})가 '{PRICE_SHEET}' 시트에 없습니다."
            
    except Exception as e:
        logger.error(f"금 종가 업데이트 중 오류: {e}")
        return False, f"❌ 금 종가 반영 오류: {e}"

def update_numeric_setting(gc, spreadsheet, number_text):
    """설정 시트 숫자 업데이트 및 금 종가 반영"""
    try:
        # 1. 설정 시트 업데이트 (기존 로직)
        ws = spreadsheet.worksheet(TARGET_SHEET_FOR_NUMBER)
        val = int(number_text) if number_text.isdigit() else float(number_text)
        ws.update_acell(TARGET_CELL_FOR_NUMBER, val)
        msg = f"✅ 설정값(J9) 업데이트: {val}"
        
        # 2. 금 종가 파일 업데이트 (추가 로직)
        success, gold_msg = update_gold_closing_price(gc, val)
        
        return True, f"{msg}\n{gold_msg}"
    except Exception as e:
        return False, f"❌ 설정값 업데이트 실패: {e}"

# --- 파싱 함수 ---

def parse_hantoo_message(text, lines):
    # (기존 로직 유지)
    parsed = {'날짜': datetime.now().strftime("%Y-%m-%d")}
    action_line = ""
    for line in lines:
        if "매수체결" in line: parsed['구분'] = "매수"; action_line = line; break
        elif "매도체결" in line: parsed['구분'] = "매도"; action_line = line; break
    
    if '구분' not in parsed: return None
    
    try:
        idx = lines.index(action_line)
        if len(lines) > idx + 4:
            parsed['종목명'] = lines[idx+1].strip().replace(" ", "")
            code_m = re.search(r"\(([A-Z]?\d+)\)", lines[idx+2])
            parsed['종목코드'] = code_m.group(1) if code_m else ""
            
            qty_m = re.search(r"([\d,]+)\s*주", lines[idx+3])
            parsed['수량'] = int(qty_m.group(1).replace(',', '')) if qty_m else 0
            
            price_m = re.search(r"([\d,]+)\s*원", lines[idx+4])
            parsed['단가'] = int(price_m.group(1).replace(',', '')) if price_m else 0
            
            parsed['금액'] = parsed['수량'] * parsed['단가']
            
            if parsed['종목명'] == "TIGER미국S&P500": parsed['계좌'] = "한투_연금"
            else: parsed['계좌'] = "한투_IRP"
            
            return parsed
    except: pass
    return None

def parse_kiwoom_message(text, lines):
    # (기존 로직 유지)
    parsed = {'날짜': datetime.now().strftime("%Y-%m-%d"), '계좌': '키움_ISA', '종목코드': ''}
    try:
        if len(lines) >= 4:
            parsed['종목명'] = lines[1].strip().replace(" ", "")
            aq_line = lines[2].strip()
            m = re.match(r"(매수|매도)\s*([\d,]+)\s*주", aq_line)
            if m:
                parsed['구분'] = m.group(1)
                parsed['수량'] = int(m.group(2).replace(',', ''))
            
            p_line = lines[3].strip()
            pm = re.search(r"(?:평균)?단가\s*([\d,]+)", p_line)
            if pm: parsed['단가'] = int(pm.group(1).replace(',', ''))
            
            if '수량' in parsed and '단가' in parsed:
                parsed['금액'] = parsed['수량'] * parsed['단가']
                return parsed
    except: pass
    return None

def parse_message(text):
    lines = [l.strip() for l in text.strip().split('\n') if l.strip() and "[Web발신]" not in l]
    if "[한투]" in text or "한국투자증권" in text: return parse_hantoo_message(text, lines)
    if "[키움]" in text or "키움증권" in text: return parse_kiwoom_message(text, lines)
    return None

# --- Main Bot Loop ---

async def process_updates(bot, gc, spreadsheet):
    try:
        updates = await bot.get_updates(timeout=10)
    except Exception as e:
        logger.error(f"텔레그램 통신 오류: {e}")
        return

    if not updates: return
    logger.info(f"{len(updates)}개 메시지 처리 시작")
    
    max_id = 0
    for update in updates:
        if update.update_id > max_id: max_id = update.update_id
        if not update.message or not update.message.text: continue
        
        text = update.message.text
        chat_id = update.effective_chat.id
        reply = ""
        
        # 1. 숫자 처리
        if re.match(r'^\d+(\.\d+)?$', text):
            _, reply = update_numeric_setting(gc, spreadsheet, text)
        # 2. 문자 처리
        else:
            data = parse_message(text)
            if data:
                _, reply = append_to_sheet(spreadsheet, data)
            else:
                if text.startswith("/start"): reply = "봇이 준비되었습니다."
                # else: reply = "이해할 수 없는 메시지입니다." # 너무 시끄러울 수 있어 생략 가능
        
        if reply:
            try: await bot.send_message(chat_id=chat_id, text=reply)
            except: pass
            
    if max_id > 0:
        await bot.get_updates(offset=max_id+1, timeout=1)

def main():
    # 봇 토큰 확인
    token, _ = get_telegram_credentials()
    if not token:
        logger.error("봇 토큰이 설정되지 않았습니다.")
        return

    # 구글 시트 연결
    gc, spreadsheet = setup_google_sheet()
    if not spreadsheet: return

    # 실행
    try:
        bot = Bot(token=token)
        asyncio.run(process_updates(bot, gc, spreadsheet))
    except Exception as e:
        logger.error(f"실행 중 오류: {e}")
        send_telegram_message(f"🔥 봇 실행 오류: {e}")

if __name__ == '__main__':
    main()