# kiwoom_auth_isa_git_action.py
# Kiwoom ISA 인증 모듈 (GitHub Actions용)

import requests
import json
import yaml
import os
from datetime import datetime, timedelta
import traceback
import sys

# --- 경로 설정 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, 'kiwoom_config.yaml')
ACCESS_TOKEN_PATH = os.path.join(CURRENT_DIR, 'access_kiwoom_token.txt')

# --- 전역 변수 ---
_config = {}
_access_token_info = {}

def load_config():
    """설정 로드 (환경변수 우선, 파일 폴백)"""
    global _config
    
    # 1. 환경변수 (GitHub Secrets)
    appkey = os.environ.get('KIWOOM_APPKEY')
    secretkey = os.environ.get('KIWOOM_SECRET')
    account_no = os.environ.get('KIWOOM_CANO_ISA')

    if appkey and secretkey:
        _config = {
            'appkey': appkey,
            'secretkey': secretkey,
            'account_no': account_no,
            'base_url': 'https://api.kiwoom.com' # Kiwoom 실전 Base URL
        }
        print("✅ [GitAction] Kiwoom ISA 설정 로드 완료 (환경변수)")
        return True

    # 2. 파일 로드 (로컬 폴백)
    try:
        with open(CONFIG_PATH, encoding='UTF-8') as f:
            _config = yaml.load(f, Loader=yaml.FullLoader)
        return True
    except Exception as e:
        print(f"❌ 설정 로드 실패 (환경변수 없음 & 파일 오류): {e}")
        _config = {}
        return False

def save_token_to_file(token_data):
    # GitHub Action에서는 파일 저장이 선택적임 (매번 인증해도 됨)
    try:
        access_token = token_data.get('token')
        expires_dt_str = token_data.get('expires_dt')
        # ... 만료 시간 처리 생략 (필요하다면 원본 로직 유지) ...
        global _access_token_info
        _access_token_info = {
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_at': expires_dt_str # 저장용 단순화
        }
    except Exception: pass

def read_token_from_file():
    # 파일에서 읽는 기능은 로컬 폴백용
    # GitHub Env에서는 매번 새로 받거나 캐싱해야 함
    # 여기서는 "저장된 토큰이 없다"고 가정하고 항상 새로 받기 (Env 모드)
    # 하지만 로컬 테스트 호환성을 위해 파일 읽기 시도
    try:
        with open(ACCESS_TOKEN_PATH, 'r', encoding='utf-8') as f:
            global _access_token_info
            _access_token_info = json.load(f)
        return True
    except Exception: return False

def is_token_valid():
    # 토큰 유효성 검사 (간단화)
    if not _access_token_info: return False
    # 만료 시간 체크 로직이 필요하지만, 
    # GitAction은 보통 매 실행마다 새로 인증하므로 
    # 여기서는 '메모리 상에 토큰이 있다'면 유효하다고 가정
    return 'access_token' in _access_token_info

def issue_token():
    # 토큰 발급
    if not _config: load_config()
    if not _config: return False

    url = _config['base_url'] + '/oauth2/token'
    headers = {'Content-Type': 'application/json;charset=UTF-8'}
    data = {
        'grant_type': 'client_credentials',
        'appkey': _config['appkey'],
        'secretkey': _config['secretkey']
    }
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        res.raise_for_status()
        token_data = res.json()
        if token_data.get("return_code") == 0:
            save_token_to_file(token_data)
            return True
        else:
            print(f"❌ [GitAction] Kiwoom 토큰 발급 실패: url={url}, response={token_data}")
            return False
    except requests.exceptions.HTTPError as e:
        print(f"❌ [GitAction] Kiwoom 인증 HTTP 오류: {e}")
        if e.response is not None:
             print(f"   [상세 에러] {e.response.text}")
        return False
    except Exception as e:
        print(f"❌ [GitAction] Kiwoom 인증 예외 발생: {e}")
        return False

def authenticate():
    print("\n🔐 [Kiwoom] REST API 인증 시작 (GitAction)...")
    load_config()
    
    # Git Action은 매번 새로 받는 것을 권장 (만료 걱정 X)
    # 하지만 로컬 호환성을 위해 유효성 체크할 수도 있음
    if issue_token():
        print("✅ [GitAction] Kiwoom 토큰 발급 성공")
        return True
    return False

def get_config(): return _config
def get_access_token(): return _access_token_info.get('access_token')
def get_token_header(): 
    token = get_access_token()
    return f"Bearer {token}" if token else None

if __name__ == '__main__':
    authenticate()
