# kis_auth_pension.py
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
# 현재 파일(kis_auth_pension.py)의 디렉토리 경로 가져오기
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 설정 파일 및 토큰 파일 경로를 현재 디렉토리 기준으로 설정
CONFIG_PATH = os.path.join(CURRENT_DIR, 'kis_devlp.yaml')
ACCESS_TOKEN_PATH = os.path.join(CURRENT_DIR, 'access_token.txt') # 연금 토큰 파일 이름 확인
# --- ---

# --- 전역 변수 선언 ---
_TRENV = tuple()
_last_auth_time = datetime.now()
_autoReAuth = False # 자동 재인증 관련 (현재 사용되지 않는 것으로 보임)
_DEBUG = False
_isPaper = False # 모의투자 여부 (현재 사용되지 않는 것으로 보임)
_cfg = None # 설정을 저장할 전역 변수, 초기값 None
# --- ---

# --- 설정 파일 로드 ---
def getEnv():
    """
    YAML 설정 파일을 로드하여 파이썬 딕셔너리로 반환합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 CONFIG_PATH를 사용합니다.
    """
    try:
        with open(CONFIG_PATH, encoding='UTF-8') as f:
            config_data = yaml.load(f, Loader=yaml.FullLoader)
        # print(f"✅ [KIS Pension] 설정 로드 성공: {CONFIG_PATH}") # 성공 로그 (필요시 주석 해제)
        return config_data
    except FileNotFoundError:
        print(f"❌ [KIS Pension] 설정 파일({CONFIG_PATH})을 찾을 수 없습니다.")
        return None # 실패 시 None 반환
    except yaml.YAMLError as e:
        print(f"❌ [KIS Pension] 설정 파일({CONFIG_PATH}) 형식 오류: {e}")
        return None # YAML 파싱 오류
    except Exception as e:
        print(f"❌ [KIS Pension] 설정 파일 로드 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return None # 실패 시 None 반환

# 스크립트 로드 시 설정 파일 읽기 시도
_cfg = getEnv()
if not _cfg:
    print("🔥 [KIS Pension] 치명적 오류: 설정 파일을 로드할 수 없어 관련 기능이 작동하지 않을 수 있습니다.")
    # 필요시 여기서 프로그램 종료
    # sys.exit("설정 파일 로드 실패로 종료합니다.")
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
        print(f"❌ [KIS Pension] _setTRENV 오류: 설정 데이터에 필요한 키 없음 - {e}")
        _TRENV = tuple() # 오류 시 초기화
    except Exception as e:
        print(f"❌ [KIS Pension] _setTRENV 중 예상치 못한 오류: {e}")
        _TRENV = tuple() # 오류 시 초기화

def getTREnv():
    """현재 설정된 KIS 환경 정보(_TRENV)를 반환합니다."""
    # 필요하다면 여기서 _TRENV가 설정되었는지 확인하는 로직 추가 가능
    return _TRENV
# --- ---

# --- 토큰 파일 처리 ---
def save_token_to_file(token, expire_time_str):
    """
    액세스 토큰과 만료 시각 문자열을 파일에 저장합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 ACCESS_TOKEN_PATH를 사용합니다.
    """
    try:
        # 만료 시각 형식 검증 (YYYY-MM-DD HH:MM:SS 형태 예상)
        try:
            datetime.strptime(expire_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print(f"⚠️ [KIS Pension] 토큰 저장 경고: 만료 시각 형식이 예상과 다릅니다 ('{expire_time_str}'). 그대로 저장합니다.")

        with open(ACCESS_TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(f"token: {token}\n")
            f.write(f"valid-date: {expire_time_str}\n")
        # print(f"✅ [KIS Pension] 토큰 저장 완료: {ACCESS_TOKEN_PATH}") # 성공 로그 (필요시 주석 해제)
    except IOError as e:
        print(f"❌ [KIS Pension] 토큰 파일 쓰기 오류: {e}")
    except Exception as e:
        print(f"❌ [KIS Pension] 토큰 파일 저장 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc()

def read_token_from_file():
    """
    파일에서 액세스 토큰과 만료 시각 문자열을 읽어 반환합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 ACCESS_TOKEN_PATH를 사용합니다.
    파일이 없거나 오류 발생 시 (None, None)을 반환합니다.
    """
    try:
        with open(ACCESS_TOKEN_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if len(lines) >= 2:
                token = lines[0].strip().split(': ')[1]
                valid_date_str = lines[1].strip().split(': ')[1]
                # print(f"✅ [KIS Pension] 토큰 로드 완료: {ACCESS_TOKEN_PATH}") # 성공 로그 (필요시 주석 해제)
                return token, valid_date_str
            else:
                print(f"⚠️ [KIS Pension] 토큰 파일({ACCESS_TOKEN_PATH}) 형식이 올바르지 않습니다.")
                return None, None
    except FileNotFoundError:
        # print(f"ℹ️ [KIS Pension] 토큰 파일({ACCESS_TOKEN_PATH}) 없음. 새로 발급 필요.") # 정보 로그 (필요시 주석 해제)
        return None, None
    except IOError as e:
         print(f"❌ [KIS Pension] 토큰 파일 읽기 오류: {e}")
         return None, None
    except Exception as e:
        print(f"❌ [KIS Pension] 토큰 파일 로드 중 예상치 못한 오류 발생: {e}")
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
        # KIS API 응답의 만료 시각 형식 (%Y-%m-%d %H:%M:%S) 에 맞춰 파싱
        valid_date = datetime.strptime(valid_date_str, "%Y-%m-%d %H:%M:%S")
        is_valid = valid_date > datetime.now()
        # if not is_valid: # 만료 로그 (필요시 주석 해제)
        #     print(f"ℹ️ [KIS Pension] 토큰 만료됨 (만료: {valid_date_str}, 현재: {datetime.now()})")
        return is_valid
    except ValueError:
        print(f"❌ [KIS Pension] 토큰 유효성 검사 오류: 날짜 형식 오류 ('{valid_date_str}')")
        return False
    except Exception as e:
        print(f"❌ [KIS Pension] 토큰 유효성 검사 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc()
        return False
# --- ---

# --- 인증 함수 (토큰 발급/갱신) ---
def auth(svr="prod", product="22"):
    """
    KIS API 인증을 수행합니다.
    기존 토큰이 유효하면 재사용하고, 아니면 새로 발급받아 파일에 저장합니다.
    성공 시 True, 실패 시 False를 반환합니다.

    Args:
        svr (str): 접속 서버 ("prod": 실전, "vps": 모의)
        product (str): 계좌 상품 코드 (예: "01"-종합, "22"-연금 등)
    """
    global _cfg # 전역 설정 변수 사용 명시

    # 설정 파일 로드 재시도 (혹시 초기 로드 실패했을 경우)
    if not _cfg:
        print("🔄 [KIS Pension] 인증 시 설정 파일 재로드 시도...")
        _cfg = getEnv()
        if not _cfg:
            print("❌ [KIS Pension] 인증 실패: 설정 정보(_cfg)를 로드할 수 없습니다.")
            return False # 실패

    print(f"\n🔐 [KIS Pension] 토큰 발급/확인 시작 (서버: {svr}, 상품코드: {product})")

    # 1. 기존 토큰 확인 및 유효성 검사
    token, valid_date_str = read_token_from_file()
    if token and is_token_valid(valid_date_str):
        print("✅ [KIS Pension] 기존 유효 토큰 재사용.")
        try:
            # 유효한 토큰 사용 시 _TRENV 설정
            # URL 강제 설정 (svr='prod'일 경우 실전 도메인 사용)
            curr_url = 'https://openapi.koreainvestment.com:9443' if svr == 'prod' else _cfg.get(svr, 'https://openapivts.koreainvestment.com:29443')
            
            cfg_data = {
                'my_app': _cfg['my_app'],
                'my_sec': _cfg['my_sec'],
                'my_acct': _cfg['my_acct_pension'], 
                'my_prod': product,
                'my_token': f"Bearer {token}", 
                'my_url': curr_url
            }
            _setTRENV(cfg_data)
            return True # 성공
        except KeyError as e:
             print(f"❌ [KIS Pension] 기존 토큰 사용 위한 설정 키 오류: {e}")
             # 설정 오류 시 새로 발급 시도하도록 넘어감 (return 제거)
        except Exception as e:
             print(f"❌ [KIS Pension] 기존 토큰 사용 위한 환경 설정 중 오류: {e}")
             return False # 환경 설정 실패 시 인증 실패

    # 2. 새 토큰 발급 시도
    print("🔄 [KIS Pension] 새 토큰 발급 시도...")
    try:
        # 필요한 설정값 확인
        app_key = _cfg.get('my_app')
        app_secret = _cfg.get('my_sec')
        # URL 강제 설정
        base_url = 'https://openapi.koreainvestment.com:9443' if svr == 'prod' else _cfg.get(svr, 'https://openapivts.koreainvestment.com:29443')
        account_no = _cfg.get('my_acct_pension') # 연금 계좌번호

        if not all([app_key, app_secret, base_url, account_no]):
            missing = [k for k, v in {'my_app': app_key, 'my_sec': app_secret, svr: base_url, 'my_acct_pension': account_no}.items() if not v]
            print(f"❌ [KIS Pension] 토큰 발급 실패: 설정 파일에 필요한 키 없음 - {missing}")
            return False # 실패

        # API 요청 준비
        url = f"{base_url}/oauth2/tokenP" # 토큰 발급 URL
        headers = {
            "content-type": "application/json",
            "appkey": app_key,
            "appsecret": app_secret
        }
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret
        }

        # API 요청 실행
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10) # timeout 추가
        res.raise_for_status() # HTTP 오류 발생 시 예외 발생 (4xx, 5xx)

        # 응답 처리
        data = res.json()
        if 'access_token' in data and 'access_token_token_expired' in data:
            new_token = data['access_token']
            expire_time_str = data['access_token_token_expired'] # 만료 시각 문자열 (YYYY-MM-DD HH:MM:SS)

            # 토큰 저장 (파일에)
            save_token_to_file(new_token, expire_time_str)

            # 새 토큰으로 _TRENV 설정
            cfg_data = {
                'my_app': app_key,
                'my_sec': app_secret,
                'my_acct': account_no,
                'my_prod': product,
                'my_token': f"Bearer {new_token}", # Bearer 접두사 추가
                'my_url': base_url
            }
            _setTRENV(cfg_data)
            print("✅ [KIS Pension] 새 토큰 발급 및 저장 완료.")
            _last_auth_time = datetime.now() # 마지막 인증 시간 기록 (선택적)
            return True # 성공

        else:
            # 응답은 성공(200)이나 필요한 데이터가 없는 경우
            print(f"❌ [KIS Pension] 토큰 발급 응답 오류: 필요한 키('access_token', 'access_token_token_expired') 없음.")
            print(f"   응답 내용: {data}")
            return False # 실패

    except requests.exceptions.RequestException as e:
        # 네트워크 관련 오류 (연결, 타임아웃 등)
        print(f"❌ [KIS Pension] 토큰 발급 요청 중 네트워크 오류 발생: {e}")
        return False # 실패
    except json.JSONDecodeError as e:
        # 응답 본문이 JSON 형식이 아닐 경우
         print(f"❌ [KIS Pension] 토큰 발급 응답 JSON 파싱 오류: {e}")
         print(f"   원본 응답 내용: {res.text if 'res' in locals() else 'N/A'}")
         return False # 실패
    except Exception as e:
        # 기타 예상치 못한 오류
        print(f"❌ [KIS Pension] 토큰 발급 중 예상치 못한 예외 발생: {e}")
        traceback.print_exc()
        return False # 실패

# --- ---

# --- API 응답 처리 클래스 ---
def _getResultObject(json_data):
    """(내부 사용) JSON 데이터를 namedtuple 객체로 변환 (사용되지 않는 것으로 보임)"""
    # 이 함수는 현재 코드에서 호출되지 않으므로 유지 또는 제거 고려
    try:
        # JSON 키에 '-' 같은 문자가 있으면 namedtuple 필드명으로 부적합하므로 변환 필요
        # cleaned_keys = {k.replace('-', '_'): v for k, v in json_data.items()}
        # return namedtuple('res', cleaned_keys.keys())(**cleaned_keys)
        return json_data # 단순 딕셔너리로 반환하는 것이 더 안전할 수 있음
    except Exception as e:
        print(f"⚠️ _getResultObject 변환 오류: {e}")
        return json_data # 오류 시 원본 데이터 반환

class APIResp:
    """KIS API 응답을 래핑하는 클래스"""
    def __init__(self, resp: requests.Response):
        self._resp = resp # 원본 requests.Response 객체
        self._header = None
        self._body = None
        self._is_json = False

        # 응답 헤더 저장
        if resp is not None:
            self._header = resp.headers

            # 응답 본문 파싱 시도 (JSON)
            try:
                self._body = resp.json()
                self._is_json = True
            except json.JSONDecodeError:
                # JSON 파싱 실패 시 텍스트로 저장
                self._body = resp.text
                self._is_json = False
                # print(f"⚠️ API 응답이 JSON 형식이 아닙니다. Text: {self._body[:100]}...") # 로그 (필요시)

    def getHeader(self):
        """응답 헤더를 반환합니다."""
        return self._header

    def getBody(self):
        """
        응답 본문을 반환합니다.
        JSON 파싱 성공 시 딕셔너리, 실패 시 텍스트를 반환합니다.
        """
        return self._body

    def getResponse(self):
        """원본 requests.Response 객체를 반환합니다."""
        return self._resp

    def isOK(self):
        """
        API 응답 성공 여부를 반환합니다 (rt_cd == '0').
        JSON 응답이 아니거나 rt_cd 키가 없으면 False를 반환합니다.
        """
        if self._is_json and isinstance(self._body, dict):
            return self._body.get("rt_cd", "1") == "0" # rt_cd 없으면 기본값 '1'(실패)
        return False

    def getErrorCode(self):
        """
        API 오류 코드(msg_cd)를 반환합니다.
        JSON 응답이 아니거나 msg_cd 키가 없으면 빈 문자열을 반환합니다.
        """
        if self._is_json and isinstance(self._body, dict):
            return self._body.get("msg_cd", "")
        return ""

    def getErrorMessage(self):
        """
        API 오류 메시지(msg1)를 반환합니다.
        JSON 응답이 아니거나 msg1 키가 없으면 빈 문자열을 반환합니다.
        """
        if self._is_json and isinstance(self._body, dict):
            return self._body.get("msg1", "")
        return ""

# --- ---

# --- 공통 API 호출 함수 ---
def _url_fetch(api_url, tr_id, tr_cont, params):
    """
    지정된 KIS API 엔드포인트로 GET 요청을 보냅니다.
    자동 재인증 로직은 제거됨 (필요시 추가 구현).

    Args:
        api_url (str): API 엔드포인트 경로 (예: /uapi/domestic-stock/v1/...)
        tr_id (str): 거래 ID
        tr_cont (str): 연속 거래 구분 ("" or "N" or "F" or "M")
        params (dict): 요청 파라미터 딕셔너리

    Returns:
        APIResp: API 응답 객체 (오류 발생 시 None 반환 가능성 있음)
    """
    # 재인증 로직 제거됨 (호출 전에 auth()가 성공했음을 가정)
    current_env = getTREnv()
    if not current_env or not current_env.my_token:
         print(f"❌ [KIS Pension] API 호출 실패 ({api_url}): 인증 토큰 없음. auth()를 먼저 호출하세요.")
         # 빈 응답 객체 또는 None 반환 필요할 수 있음
         # return APIResp(None) # 예시: 빈 응답
         return None # 또는 그냥 None 반환

    # 요청 URL 및 헤더 준비
    url = f"{current_env.my_url}{api_url}"
    headers = {
        "authorization": current_env.my_token,
        "appkey": current_env.my_app,
        "appsecret": current_env.my_sec,
        "tr_id": tr_id,
        "custtype": "P", # 개인 고객 유형
        "tr_cont": tr_cont if tr_cont else "", # 연속 조회 헤더
        "Content-Type": "application/json; charset=utf-8" # UTF-8 명시
    }

    # API 요청 실행
    try:
        # print(f"🚀 [KIS Pension] API 요청: GET {url}") # 요청 로그 (필요시 주석 해제)
        # print(f"   - TR_ID: {tr_id}, TR_CONT: {tr_cont}")
        # print(f"   - Params: {params}")
        res = requests.get(url, headers=headers, params=params, timeout=15) # timeout 증가
        res.raise_for_status() # HTTP 오류 시 예외 발생

        return APIResp(res) # APIResp 객체로 래핑하여 반환

    except requests.exceptions.Timeout:
         print(f"❌ [KIS Pension] API 요청 시간 초과: GET {url}")
         return None # 타임아웃 시 None 반환 (또는 빈 APIResp)
    except requests.exceptions.RequestException as e:
         print(f"❌ [KIS Pension] API 요청 중 네트워크 오류 발생: {e}")
         # 필요시 원본 응답 내용 출력 (e.response)
         # print(f"   - 응답 상태 코드: {e.response.status_code if e.response else 'N/A'}")
         # print(f"   - 응답 내용: {e.response.text if e.response else 'N/A'}")
         return None # 오류 시 None 반환 (또는 빈 APIResp)
    except Exception as e:
         print(f"❌ [KIS Pension] API 요청 처리 중 예상치 못한 예외 발생: {e}")
         traceback.print_exc()
         return None # 오류 시 None 반환 (또는 빈 APIResp)

# --- ---

# --- 스크립트 직접 실행 시 테스트 ---
if __name__ == '__main__':
    print("--- KIS Pension Auth Module Test ---")
    # 인증 함수 테스트
    auth_result = auth(svr="prod", product="22") # 연금 상품코드 22

    if auth_result:
        print("\n✅ 인증 성공!")
        env_info = getTREnv()
        if env_info:
             print(f"   - 계정: {env_info.my_acct}")
             print(f"   - URL: {env_info.my_url}")
             print(f"   - 토큰: {env_info.my_token[:20]}...") # 토큰 일부만 출력
        else:
             print("   - ⚠️ 환경 정보(_TRENV)가 설정되지 않았습니다.")

        # 선택적: API 호출 테스트 (잔고 조회 예시)
        # print("\n--- API 호출 테스트 (잔고 조회) ---")
        # import kis_domstk_pension as kb # domstk 모듈 임포트
        # balance_obj = kb.get_inquire_balance_obj()
        # if balance_obj and balance_obj.get("rt_cd") == "0":
        #     print("   - 잔고 조회 API 호출 성공")
        #     # print(json.dumps(balance_obj, indent=2, ensure_ascii=False)) # 결과 출력
        # else:
        #     print("   - 잔고 조회 API 호출 실패 또는 오류")
        #     # print(balance_obj) # 실패 시 결과 출력
    else:
        print("\n🔥 인증 실패!")

    print("\n--- Test End ---")
# --- ---