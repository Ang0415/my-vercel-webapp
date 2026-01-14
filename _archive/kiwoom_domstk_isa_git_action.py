# -*- coding: utf-8 -*-
# kiwoom_domstk_isa_git_action.py: 키움증권 REST API 호출 함수 모음 (GitHub Action용)

import requests
import json
import time
from datetime import datetime, date
import traceback
import pandas as pd
try:
    import kiwoom_auth_isa_git_action as auth
except ModuleNotFoundError:
    try:
        import kiwoom_auth_isa as auth
    except ModuleNotFoundError:
        print("❌ 인증 모듈(kiwoom_auth_isa)을 찾을 수 없습니다.")
        raise

# --- 기본 API 요청 함수 ---
def _kiwoom_fetch(path: str, method: str = "GET", api_id: str = None, params: dict = None, body: dict = None, cont_yn: str = 'N', next_key: str = ''):
    """키움증권 REST API 공통 요청 함수"""
    config = auth.get_config()
    token_header = auth.get_token_header()

    if not config and not auth.authenticate():
        print("❌ API 요청 실패: 인증 실패.")
        return None
    if not config: config = auth.get_config()
    if not token_header: token_header = auth.get_token_header()

    base_url = config.get('base_url')
    url = f"{base_url}{path}"

    headers = {
        "authorization": token_header,
        "appkey": config.get('appkey', ''),
        "appsecret": config.get('secretkey', ''),
        "Content-Type": "application/json; charset=utf-8",
        "cont-yn": cont_yn if cont_yn else 'N',
        "next-key": next_key if next_key else '',
    }
    if api_id: headers["api-id"] = api_id

    # print(f"\n🚀 API 요청: {method} {url}") # 로그 줄임

    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=body)
        else:
            return None

        # response_headers = response.headers
        next_key_from_header = response.headers.get('next-key', '')
        cont_yn_from_header = response.headers.get('cont-yn', 'N')

        response_package = {'headers': {'next-key': next_key_from_header, 'cont-yn': cont_yn_from_header}}
        if response.status_code == 200:
            response_data = response.json()
            response_package['body'] = response_data
            return_code = response_data.get('return_code', -1)
            # if return_code != 0: print(f"❌ API 오류 응답: {response_data.get('return_msg')}")
        else:
            # print(f"❌ API 호출 실패! Status: {response.status_code}")
            try: response_package['body'] = response.json()
            except: response_package['body'] = {'error': response.text}

        return response_package

    except Exception as e:
        print(f"❌ API 요청 오류: {e}")
        return None

# --- 데이터 클리닝 함수 ---
def clean_num_str(num_str, type_func=int):
    if not num_str: return type_func(0)
    try:
        cleaned = num_str.lstrip('-').lstrip('0')
        if not cleaned: return type_func(0)
        value = type_func(cleaned)
        return -value if num_str.startswith('-') else value
    except (ValueError, TypeError):
        return type_func(0)

# --- API 호출 함수들 ---

# 계좌평가잔고내역요청 (kt00018)
def get_account_evaluation_balance(query_type: str = '1', exchange_type: str = 'KRX', cont_yn: str = 'N', next_key: str = ''):
    api_path = "/api/dostk/acnt"
    http_method = "POST"
    api_identifier = "kt00018"
    request_body = {'qry_tp': query_type, 'dmst_stex_tp': exchange_type}

    response_package = _kiwoom_fetch(
        path=api_path, method=http_method, api_id=api_identifier,
        body=request_body, cont_yn=cont_yn, next_key=next_key
    )

    result = {'success': False, 'data': None, 'next_key': None, 'cont_yn': 'N'}
    if response_package and 'body' in response_package:
        result['data'] = response_package['body']
        result['next_key'] = response_package['headers'].get('next-key')
        result['cont_yn'] = response_package['headers'].get('cont-yn', 'N')
        if response_package['body'].get('return_code') == 0:
            result['success'] = True
    return result

# 일별계좌수익률상세현황요청 (kt00016)
def get_daily_account_profit_loss(start_date: str, end_date: str, cont_yn: str = 'N', next_key: str = ''):
    api_path = "/api/dostk/acnt"
    http_method = "POST"
    api_identifier = "kt00016"
    request_body = {
        'fr_dt': start_date.replace("-", ""),
        'to_dt': end_date.replace("-", ""),
    }

    response_package = _kiwoom_fetch(
        path=api_path, method=http_method, api_id=api_identifier,
        body=request_body, cont_yn=cont_yn, next_key=next_key
    )

    result = {'success': False, 'data': None, 'next_key': None, 'cont_yn': 'N'}
    if response_package and 'body' in response_package:
        result['data'] = response_package['body']
        result['next_key'] = response_package['headers'].get('next-key')
        result['cont_yn'] = response_package['headers'].get('cont-yn', 'N')
        if response_package['body'].get('return_code') == 0:
            result['success'] = True
    return result

# 당일매매일지요청 (ka10170)
def get_daily_trading_log(base_date: str, ottks_type: str = '1', cash_credit_type: str = '0', cont_yn: str = 'N', next_key: str = ''):
    api_path = "/api/dostk/acnt"
    http_method = "POST"
    api_identifier = "ka10170"
    request_body = {
        'base_dt': base_date.replace("-", ""),
        'ottks_tp': ottks_type,
        'ch_crd_tp': cash_credit_type,
    }

    response_package = _kiwoom_fetch(
        path=api_path, method=http_method, api_id=api_identifier,
        body=request_body, cont_yn=cont_yn, next_key=next_key
    )

    result = {'success': False, 'data': None, 'next_key': None, 'cont_yn': 'N'}
    if response_package and 'body' in response_package:
        result['data'] = response_package['body']
        result['next_key'] = response_package['headers'].get('next-key')
        result['cont_yn'] = response_package['headers'].get('cont-yn', 'N')
        if response_package['body'].get('return_code') == 0:
            result['success'] = True
    return result
