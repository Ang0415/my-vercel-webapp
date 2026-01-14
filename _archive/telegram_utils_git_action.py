# telegram_utils_git_action.py
# 텔레그램 설정을 로드하고 메시지를 발송하는 유틸리티 모듈 (GitHub Actions용)

import yaml
import requests
import os
import traceback

# --- 경로 설정 (로컬 환경 폴백용) ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, 'telegram_config.yaml')
# --- ---

# --- 전역 변수 ---
_telegram_config = {}
# --- ---

def load_telegram_config():
    """텔레그램 설정을 로드합니다 (환경변수 우선, 실패 시 파일)."""
    global _telegram_config
    if _telegram_config: 
        return True

    # 1. 환경변수 확인 (GitHub Secrets)
    env_token = os.environ.get('TELEGRAM_TOKEN')
    env_chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    if env_token and env_chat_id:
        _telegram_config = {
            'bot_token': env_token,
            'chat_id': env_chat_id
        }
        print("✅ [GitAction] 텔레그램 설정 로드 완료 (환경변수)")
        return True

    # 2. 파일 로드 (로컬 폴백)
    try:
        with open(CONFIG_PATH, encoding='UTF-8') as f:
            _telegram_config = yaml.load(f, Loader=yaml.FullLoader)
        if not _telegram_config:
             print(f"⚠️ 텔레그램 설정 파일({CONFIG_PATH}) 내용은 비어있습니다.")
             _telegram_config = {}
             return False
        if 'bot_token' not in _telegram_config or 'chat_id' not in _telegram_config:
            print(f"❌ 텔레그램 설정 파일({CONFIG_PATH})에 필수 키가 없습니다.")
            _telegram_config = {}
            return False
        print(f"✅ 텔레그램 설정 로드 완료: {CONFIG_PATH}")
        return True
    except FileNotFoundError:
        print(f"❌ 텔레그램 설정 파일({CONFIG_PATH})을 찾을 수 없습니다.")
        _telegram_config = {}
        return False
    except Exception as e:
        print(f"❌ 텔레그램 설정 로드 중 오류: {e}")
        _telegram_config = {}
        return False

def get_telegram_credentials():
    """차례로 로드된 설정 반환"""
    if not _telegram_config and not load_telegram_config():
        return None, None
    return _telegram_config.get('bot_token'), _telegram_config.get('chat_id')

def send_telegram_message(message):
    """텔레그램 메시지 전송"""
    bot_token, chat_id = get_telegram_credentials()

    if not bot_token or not chat_id:
        print("텔레그램 토큰 또는 Chat ID가 없어 메시지를 보낼 수 없습니다.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status() 
        print(f"📢 텔레그램 메시지 발송 완료 (Chat ID: {str(chat_id)[:4]}...)")
    except Exception as e:
        print(f"텔레그램 메시지 발송 실패: {e}")
