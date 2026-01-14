# kis_auth_irp.py
# (iCloud 등 환경 동기화 문제를 해결하기 위해 파일 경로를 상대 경로로 수정)

import time
import copy
import yaml
import requests
import json
import os # os 모듈 임포트 확인
import pandas as pd
from collections import namedtuple
from datetime import datetime
import traceback # 오류 상세 출력을 위해 추가
import sys # 프로그램 종료 등 시스템 기능 위해 추가

# --- 경로 설정 ---
# 현재 파일(kis_auth_irp.py)의 디렉토리 경로 가져오기
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# IRP용 설정 파일 및 토큰 파일 경로를 현재 디렉토리 기준으로 설정
CONFIG_PATH = os.path.join(CURRENT_DIR, 'kis_devlp_irp.yaml') # IRP용 YAML 파일 이름
ACCESS_TOKEN_PATH = os.path.join(CURRENT_DIR, 'access_token_irp.txt') # IRP용 토큰 파일 이름
# --- ---

# --- 전역 변수 선언 ---
_TRENV = tuple()
_last_auth_time = datetime.now()
_autoReAuth = False
_DEBUG = False
_isPaper = False
_cfg = None # 설정을 저장할 전역 변수, 초기값 None
# --- ---

# --- 설정 파일 로드 ---
def getEnv():
    """
    IRP용 YAML 설정 파일을 로드하여 파이썬 딕셔너리로 반환합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 CONFIG_PATH를 사용합니다.
    """
    try:
        with open(CONFIG_PATH, encoding='UTF-8') as f: # IRP용 CONFIG_PATH 사용
            config_data = yaml.load(f, Loader=yaml.FullLoader)
        # print(f"✅ [KIS IRP] 설정 로드 성공: {CONFIG_PATH}") # 성공 로그 (필요시 주석 해제)
        return config_data
    except FileNotFoundError:
        print(f"❌ [KIS IRP] 설정 파일({CONFIG_PATH})을 찾을 수 없습니다.")
        return None # 실패 시 None 반환
    except yaml.YAMLError as e:
        print(f"❌ [KIS IRP] 설정 파일({CONFIG_PATH}) 형식 오류: {e}")
        return None # YAML 파싱 오류
    except Exception as e:
        print(f"❌ [KIS IRP] 설정 파일 로드 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return None # 실패 시 None 반환

# 스크립트 로드 시 설정 파일 읽기 시도
_cfg = getEnv()
if not _cfg:
    print("🔥 [KIS IRP] 치명적 오류: 설정 파일을 로드할 수 없어 관련 기능이 작동하지 않을 수 있습니다.")
    # 필요시 여기서 프로그램 종료
    # sys.exit("IRP 설정 파일 로드 실패로 종료합니다.")
# --- ---

# --- KIS 환경 정보 구조체 및 관리 함수 ---
KISEnv = namedtuple('KISEnv', ['my_app', 'my_sec', 'my_acct', 'my_prod', 'my_token', 'my_url'])

def _setTRENV(cfg_data):
    """전역 _TRENV 변수에 KISEnv 튜플을 설정합니다."""
    global _TRENV
    try:
        _TRENV = KISEnv(
            my_app=cfg_data['my_app'],
            my_sec=cfg_data['my_sec'],
            my_acct=cfg_data['my_acct'], # 인증 시 전달된 계좌번호
            my_prod=cfg_data['my_prod'], # 인증 시 전달된 상품코드
            my_token=cfg_data['my_token'], # Bearer 포함된 토큰
            my_url=cfg_data['my_url']   # 실전/모의투자 URL
        )
    except KeyError as e:
        print(f"❌ [KIS IRP] _setTRENV 오류: 설정 데이터에 필요한 키 없음 - {e}")
        _TRENV = tuple() # 오류 시 초기화
    except Exception as e:
        print(f"❌ [KIS IRP] _setTRENV 중 예상치 못한 오류: {e}")
        _TRENV = tuple() # 오류 시 초기화

def getTREnv():
    """현재 설정된 KIS 환경 정보(_TRENV)를 반환합니다."""
    return _TRENV
# --- ---

# --- 토큰 파일 처리 ---
def save_token_to_file(token, expire_time_str):
    """
    IRP용 액세스 토큰과 만료 시각 문자열을 파일에 저장합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 ACCESS_TOKEN_PATH를 사용합니다.
    """
    try:
        # 만료 시각 형식 검증 (YYYY-MM-DD HH:MM:SS 형태 예상)
        try:
            datetime.strptime(expire_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print(f"⚠️ [KIS IRP] 토큰 저장 경고: 만료 시각 형식이 예상과 다릅니다 ('{expire_time_str}'). 그대로 저장합니다.")

        with open(ACCESS_TOKEN_PATH, 'w', encoding='utf-8') as f: # IRP용 ACCESS_TOKEN_PATH 사용
            f.write(f"token: {token}\n")
            f.write(f"valid-date: {expire_time_str}\n")
        # print(f"✅ [KIS IRP] 토큰 저장 완료: {ACCESS_TOKEN_PATH}") # 성공 로그 (필요시 주석 해제)
    except IOError as e:
        print(f"❌ [KIS IRP] 토큰 파일 쓰기 오류: {e}")
    except Exception as e:
        print(f"❌ [KIS IRP] 토큰 파일 저장 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc()

def read_token_from_file():
    """
    IRP용 파일에서 액세스 토큰과 만료 시각 문자열을 읽어 반환합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 ACCESS_TOKEN_PATH를 사용합니다.
    파일이 없거나 오류 발생 시 (None, None)을 반환합니다.
    """
    try:
        with open(ACCESS_TOKEN_PATH, 'r', encoding='utf-8') as f: # IRP용 ACCESS_TOKEN_PATH 사용
            lines = f.readlines()
            if len(lines) >= 2:
                token = lines[0].strip().split(': ')[1]
                valid_date_str = lines[1].strip().split(': ')[1]
                # print(f"✅ [KIS IRP] 토큰 로드 완료: {ACCESS_TOKEN_PATH}") # 성공 로그 (필요시 주석 해제)
                return token, valid_date_str
            else:
                print(f"⚠️ [KIS IRP] 토큰 파일({ACCESS_TOKEN_PATH}) 형식이 올바르지 않습니다.")
                return None, None
    except FileNotFoundError:
        # print(f"ℹ️ [KIS IRP] 토큰 파일({ACCESS_TOKEN_PATH}) 없음. 새로 발급 필요.") # 정보 로그 (필요시 주석 해제)
        return None, None
    except IOError as e:
         print(f"❌ [KIS IRP] 토큰 파일 읽기 오류: {e}")
         return None, None
    except Exception as e:
        print(f"❌ [KIS IRP] 토큰 파일 로드 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc()
        return None, None
# --- ---

# --- 토큰 유효성 검사 ---
def is_token_valid(valid_date_str):
    """
    만료 시각 문자열(YYYY-MM-DD HH:MM:SS)을 기반으로 토큰 유효성을 검사합니다.
    만료 시각이 현재 시각보다 미래이면 True, 아니면 False를 반환합니다.
    형식 오류 시 False를 반환합니다.
    """
    if not valid_date_str:
        return False
    try:
        valid_date = datetime.strptime(valid_date_str, "%Y-%m-%d %H:%M:%S")
        is_valid = valid_date > datetime.now()
        # if not is_valid: # 만료 로그 (필요시 주석 해제)
        #     print(f"ℹ️ [KIS IRP] 토큰 만료됨 (만료: {valid_date_str}, 현재: {datetime.now()})")
        return is_valid
    except ValueError:
        print(f"❌ [KIS IRP] 토큰 유효성 검사 오류: 날짜 형식 오류 ('{valid_date_str}')")
        return False
    except Exception as e:
        print(f"❌ [KIS IRP] 토큰 유효성 검사 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc()
        return False
# --- ---

# --- 인증 함수 (토큰 발급/갱신) ---
def auth(svr="prod", product="29"): # IRP 기본 상품코드 29
    """
    KIS API IRP 계좌 인증을 수행합니다.
    기존 토큰이 유효하면 재사용하고, 아니면 새로 발급받아 파일에 저장합니다.
    성공 시 True, 실패 시 False를 반환합니다.

    Args:
        svr (str): 접속 서버 ("prod": 실전, "vps": 모의)
        product (str): 계좌 상품 코드 (IRP는 보통 "29")
    """
    global _cfg # 전역 설정 변수 사용 명시

    # 설정 파일 로드 재시도
    if not _cfg:
        print("🔄 [KIS IRP] 인증 시 설정 파일 재로드 시도...")
        _cfg = getEnv()
        if not _cfg:
            print("❌ [KIS IRP] 인증 실패: 설정 정보(_cfg)를 로드할 수 없습니다.")
            return False # 실패

    print(f"\n🔐 [KIS IRP] 토큰 발급/확인 시작 (서버: {svr}, 상품코드: {product})")

    # 1. 기존 토큰 확인 및 유효성 검사
    token, valid_date_str = read_token_from_file()
    if token and is_token_valid(valid_date_str):
        print("✅ [KIS IRP] 기존 유효 토큰 재사용.")
        try:
            # 유효한 토큰 사용 시 _TRENV 설정
            # URL 강제 설정
            curr_url = 'https://openapi.koreainvestment.com:9443' if svr == 'prod' else _cfg.get(svr, 'https://openapivts.koreainvestment.com:29443')
            
            cfg_data = {
                'my_app': _cfg['my_app'],
                'my_sec': _cfg['my_sec'],
                'my_acct': _cfg['my_acct_irp'], 
                'my_prod': product,
                'my_token': f"Bearer {token}", 
                'my_url': curr_url
            }
            _setTRENV(cfg_data)
            return True # 성공
        except KeyError as e:
             print(f"❌ [KIS IRP] 기존 토큰 사용 위한 설정 키 오류: {e}")
        except Exception as e:
             print(f"❌ [KIS IRP] 기존 토큰 사용 위한 환경 설정 중 오류: {e}")
             return False # 환경 설정 실패 시 인증 실패

    # 2. 새 토큰 발급 시도
    print("🔄 [KIS IRP] 새 토큰 발급 시도...")
    try:
        # 필요한 설정값 확인
        app_key = _cfg.get('my_app')
        app_secret = _cfg.get('my_sec')
        # URL 강제 설정
        base_url = 'https://openapi.koreainvestment.com:9443' if svr == 'prod' else _cfg.get(svr, 'https://openapivts.koreainvestment.com:29443')
        account_no = _cfg.get('my_acct_irp') # IRP 계좌번호 키 확인!

        if not all([app_key, app_secret, base_url, account_no]):
            missing = [k for k, v in {'my_app': app_key, 'my_sec': app_secret, svr: base_url, 'my_acct_irp': account_no}.items() if not v]
            print(f"❌ [KIS IRP] 토큰 발급 실패: 설정 파일에 필요한 키 없음 - {missing}")
            return False # 실패

        # API 요청 준비
        url = f"{base_url}/oauth2/tokenP" # 토큰 발급 URL
        headers = {"content-type": "application/json", "appkey": app_key, "appsecret": app_secret}
        payload = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}

        # API 요청 실행
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        res.raise_for_status() # HTTP 오류 시 예외 발생

        # 응답 처리
        data = res.json()
        if 'access_token' in data and 'access_token_token_expired' in data:
            new_token = data['access_token']
            expire_time_str = data['access_token_token_expired']
            save_token_to_file(new_token, expire_time_str) # 파일에 저장

            # 새 토큰으로 _TRENV 설정
            cfg_data = {
                'my_app': app_key,
                'my_sec': app_secret,
                'my_acct': account_no, # IRP 계좌번호
                'my_prod': product,
                'my_token': f"Bearer {new_token}",
                'my_url': base_url
            }
            _setTRENV(cfg_data)
            print("✅ [KIS IRP] 새 토큰 발급 및 저장 완료.")
            _last_auth_time = datetime.now()
            return True # 성공
        else:
            print(f"❌ [KIS IRP] 토큰 발급 응답 오류: 필요한 키 없음.")
            print(f"   응답 내용: {data}")
            return False # 실패

    except requests.exceptions.RequestException as e:
        print(f"❌ [KIS IRP] 토큰 발급 요청 중 네트워크 오류 발생: {e}")
        return False # 실패
    except json.JSONDecodeError as e:
         print(f"❌ [KIS IRP] 토큰 발급 응답 JSON 파싱 오류: {e}")
         print(f"   원본 응답 내용: {res.text if 'res' in locals() else 'N/A'}")
         return False # 실패
    except Exception as e:
        print(f"❌ [KIS IRP] 토큰 발급 중 예상치 못한 예외 발생: {e}")
        traceback.print_exc()
        return False # 실패
# --- ---

# --- API 응답 처리 클래스 ---
class APIResp:
    """KIS API 응답을 래핑하는 클래스"""
    def __init__(self, resp: requests.Response):
        self._resp = resp
        self._header = None
        self._body = None
        self._is_json = False
        if resp is not None:
            self._header = resp.headers
            try:
                self._body = resp.json()
                self._is_json = True
            except json.JSONDecodeError:
                self._body = resp.text
                self._is_json = False

    def getHeader(self): return self._header
    def getBody(self): return self._body
    def getResponse(self): return self._resp
    def isOK(self):
        if self._is_json and isinstance(self._body, dict):
            return self._body.get("rt_cd", "1") == "0"
        return False
    def getErrorCode(self):
        if self._is_json and isinstance(self._body, dict):
            return self._body.get("msg_cd", "")
        return ""
    def getErrorMessage(self):
        if self._is_json and isinstance(self._body, dict):
            return self._body.get("msg1", "")
        return ""
# --- ---

# --- 공통 API 호출 함수 ---
def _url_fetch(api_url, tr_id, tr_cont, params):
    """
    지정된 KIS API 엔드포인트로 GET 요청을 보냅니다.
    """
    current_env = getTREnv()
    if not current_env or not current_env.my_token:
         print(f"❌ [KIS IRP] API 호출 실패 ({api_url}): 인증 토큰 없음. auth()를 먼저 호출하세요.")
         return None

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
        # print(f"🚀 [KIS IRP] API 요청: GET {url}") # 요청 로그
        res = requests.get(url, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        return APIResp(res)
    except requests.exceptions.Timeout:
         print(f"❌ [KIS IRP] API 요청 시간 초과: GET {url}")
         return None
    except requests.exceptions.RequestException as e:
         print(f"❌ [KIS IRP] API 요청 중 네트워크 오류 발생: {e}")
         return None
    except Exception as e:
         print(f"❌ [KIS IRP] API 요청 처리 중 예상치 못한 예외 발생: {e}")
         traceback.print_exc()
         return None
# --- ---

# --- 스크립트 직접 실행 시 테스트 ---
if __name__ == '__main__':
    print("--- KIS IRP Auth Module Test ---")
    auth_result = auth(svr="prod", product="29") # IRP 상품코드 29

    if auth_result:
        print("\n✅ 인증 성공!")
        env_info = getTREnv()
        if env_info:
             print(f"   - 계정: {env_info.my_acct}")
             print(f"   - URL: {env_info.my_url}")
             print(f"   - 토큰: {env_info.my_token[:20]}...")
        else:
             print("   - ⚠️ 환경 정보(_TRENV)가 설정되지 않았습니다.")

        # 선택적: API 호출 테스트 (IRP 잔고 조회 예시)
        # print("\n--- API 호출 테스트 (IRP 잔고 조회) ---")
        # import kis_domstk_irp as kb # domstk 모듈 임포트
        # df_balance = kb.get_inquire_irp_balance_lst() # 단순 잔고 조회 함수 호출
        # if isinstance(df_balance, pd.DataFrame) and not df_balance.empty:
        #      print("   - IRP 단순 잔고 조회 API 호출 성공")
        #      # print(df_balance.head()) # 결과 일부 출력
        # else:
        #      print("   - IRP 단순 잔고 조회 API 호출 실패 또는 데이터 없음")

    else:
        print("\n🔥 인증 실패!")

    print("\n--- Test End ---")
# --- ---