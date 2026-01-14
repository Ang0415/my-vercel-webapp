# kis_auth_irp_git_action.py
# KIS IRP 인증 모듈 (GitHub Actions용)

import time
import copy
import yaml
import requests
import json
import os
import pandas as pd
from collections import namedtuple
from datetime import datetime
import traceback
import sys

# --- 경로 설정 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, 'kis_devlp_irp.yaml')
ACCESS_TOKEN_PATH = os.path.join(CURRENT_DIR, 'access_token_irp.txt')

# --- 전역 변수 ---
_TRENV = tuple()
_last_auth_time = datetime.now()
_cfg = None

KISEnv = namedtuple('KISEnv', ['my_app', 'my_sec', 'my_acct', 'my_prod', 'my_token', 'my_url'])

def getEnv():
    """설정 로드 (환경변수 우선, 파일 폴백)"""
    # 1. 환경변수 (GitHub Actions)
    kis_appkey = os.environ.get('KIS_APPKEY')
    kis_secret = os.environ.get('KIS_SECRET')
    kis_cano_irp = os.environ.get('KIS_CANO_IRP')
    
    if kis_appkey and kis_secret and kis_cano_irp:
        env_input = os.environ.get('KIS_ENV', 'prod').lower()
        print(f"✅ [GitAction] KIS IRP 설정 로드 완료 (환경변수, Mode={env_input})")
        
        base_url = 'https://openapi.koreainvestment.com:9443'
        if env_input == 'vps':
            base_url = 'https://openapivts.koreainvestment.com:29443'

        return {
            'my_app': kis_appkey,
            'my_sec': kis_secret,
            'my_acct': kis_cano_irp, 
            'my_acct_irp': kis_cano_irp, 
            'my_prod': '29',
            'my_token': '',
            'prod': base_url, # Force selected URL
            'vps': base_url   # Force selected URL
        }

    # 2. 파일 로드 (로컬 폴백)
    try:
        with open(CONFIG_PATH, encoding='UTF-8') as f:
            return yaml.load(f, Loader=yaml.FullLoader)
    except Exception as e:
        print(f"❌ 설정 로드 실패 (환경변수 없음 & 파일 오류): {e}")
        return None

_cfg = getEnv()

def _setTRENV(cfg_data):
    global _TRENV
    try:
        _TRENV = KISEnv(
            my_app=cfg_data['my_app'],
            my_sec=cfg_data['my_sec'],
            my_acct=cfg_data['my_acct'],
            my_prod=cfg_data['my_prod'],
            my_token=cfg_data['my_token'],
            my_url=cfg_data['my_url']
        )
    except Exception as e:
        print(f"❌ _setTRENV 오류: {e}")
        _TRENV = tuple()

def getTREnv():
    return _TRENV

def save_token_to_file(token, expire_time_str):
    try:
        with open(ACCESS_TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(f"token: {token}\n")
            f.write(f"valid-date: {expire_time_str}\n")
    except Exception: pass

def read_token_from_file():
    try:
        with open(ACCESS_TOKEN_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if len(lines) >= 2:
                return lines[0].strip().split(': ')[1], lines[1].strip().split(': ')[1]
    except Exception: pass
    return None, None

def is_token_valid(valid_date_str):
    if not valid_date_str: return False
    try:
        return datetime.strptime(valid_date_str, "%Y-%m-%d %H:%M:%S") > datetime.now()
    except Exception: return False

def auth(svr="prod", product="29"):
    global _cfg
    if not _cfg: _cfg = getEnv()
    if not _cfg: return False

    token, valid_date = read_token_from_file()
    if token and is_token_valid(valid_date):
        base_url = _cfg.get(svr, 'https://openapi.koreainvestment.com:9443')
        _setTRENV({
            'my_app': _cfg['my_app'], 'my_sec': _cfg['my_sec'],
            'my_acct': _cfg['my_acct'], 'my_prod': product,
            'my_token': f"Bearer {token}", 'my_url': base_url
        })
        return True

    try:
        base_url = _cfg.get(svr, 'https://openapi.koreainvestment.com:9443')
        url = f"{base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": _cfg['my_app'],
            "appsecret": _cfg['my_sec']
        }
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        res.raise_for_status()
        data = res.json()
        
        new_token = data.get('access_token')
        expire_time = data.get('access_token_token_expired')
        
        if new_token and expire_time:
            save_token_to_file(new_token, expire_time)
            _setTRENV({
                'my_app': _cfg['my_app'], 'my_sec': _cfg['my_sec'],
                'my_acct': _cfg['my_acct'], 'my_prod': product,
                'my_token': f"Bearer {new_token}", 'my_url': base_url
            })
            print("✅ [GitAction] KIS IRP 토큰 발급 성공")
            return True
        return False
    except requests.exceptions.HTTPError as e:
        print(f"❌ [GitAction] KIS IRP 인증 실패 (HTTP {e.response.status_code}): {e}")
        if e.response is not None:
             print(f"   [상세 에러] {e.response.text}")
        return False
    except Exception as e:
        print(f"❌ [GitAction] KIS IRP 인증 실패: {e}")
        return False

class APIResp:
    def __init__(self, resp):
        self._resp = resp
        self._body = resp.json() if resp else {}
    def getBody(self): return self._body
    def getResponse(self): return self._resp
    def isOK(self): return self._body.get("rt_cd", "1") == "0"

def _url_fetch(api_url, tr_id, tr_cont, params):
    current_env = getTREnv()
    if not current_env or not current_env.my_token: return None
    
    url = f"{current_env.my_url}{api_url}"
    headers = {
        "authorization": current_env.my_token,
        "appkey": current_env.my_app,
        "appsecret": current_env.my_sec,
        "tr_id": tr_id,
        "custtype": "P",
        "tr_cont": tr_cont if tr_cont else "",
        "Content-Type": "application/json; charset=utf-8"
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        return APIResp(res)
    except Exception as e:
        print(f"❌ API 호출 오류 ({api_url}): {e}")
        return None
