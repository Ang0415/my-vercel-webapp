# kiwoom_auth_isa.py
# (iCloud 등 환경 동기화 문제를 해결하기 위해 파일 경로를 상대 경로로 수정)

import requests
import json
import yaml
import os # os 모듈 임포트 확인
from datetime import datetime, timedelta
import traceback # 오류 상세 출력을 위해 추가
import sys # 시스템 기능 위해 추가

# --- 경로 설정 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 키움용 설정 파일 및 토큰 파일 경로
CONFIG_PATH = os.path.join(CURRENT_DIR, 'kiwoom_config.yaml')
ACCESS_TOKEN_PATH = os.path.join(CURRENT_DIR, 'access_kiwoom_token.txt') # 키움 토큰 파일명 확인
# --- ---

# --- 전역 변수 ---
_config = {} # YAML 설정 저장
_access_token_info = {} # 토큰 정보 저장 (토큰 값, 만료 시간 등)
# --- ---

# --- 설정 파일 로드 ---
def load_config():
    """kiwoom_config.yaml 파일 로드"""
    global _config
    try:
        with open(CONFIG_PATH, encoding='UTF-8') as f: # 수정된 CONFIG_PATH 사용
            _config = yaml.load(f, Loader=yaml.FullLoader)
        print(f"✅ [Kiwoom] 설정 로드 완료: {CONFIG_PATH}")
        return True # 성공 시 True 반환
    except FileNotFoundError:
        print(f"❌ [Kiwoom] 설정 파일({CONFIG_PATH})을 찾을 수 없습니다.")
        _config = {} # 실패 시 초기화
        return False # 실패 시 False 반환
    except yaml.YAMLError as e:
        print(f"❌ [Kiwoom] 설정 파일({CONFIG_PATH}) 형식 오류: {e}")
        _config = {} # 실패 시 초기화
        return False # 실패 시 False 반환
    except Exception as e:
        print(f"❌ [Kiwoom] 설정 파일 로드 중 오류 발생: {e}")
        _config = {} # 실패 시 초기화
        traceback.print_exc()
        return False # 실패 시 False 반환
# --- ---

# --- 토큰 파일 처리 ---
def save_token_to_file(token_data):
    """
    발급받은 키움 토큰 정보를 파일에 저장 (키움 응답 형식에 맞게 처리)
    파일 경로는 스크립트 위치 기준으로 설정된 ACCESS_TOKEN_PATH를 사용합니다.
    """
    global _access_token_info
    try:
        # 키움 응답의 'token' 키 사용 (실제 토큰 값)
        access_token = token_data.get('token')
        if not access_token:
            print("❌ [Kiwoom] 토큰 저장 실패: 응답에 'token' 키가 없습니다.")
            return # 저장 불가

        # 키움 응답의 'expires_dt' 파싱 (YYYYMMDDHHMMSS 형식)
        expires_dt_str = token_data.get('expires_dt')
        expire_time = None
        if expires_dt_str:
            try:
                # 문자열을 datetime 객체로 변환
                expire_time = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
            except ValueError:
                print(f"❌ [Kiwoom] 토큰 저장 오류: 날짜 형식 오류 ('expires_dt': {expires_dt_str}).")
                # 형식 오류 시 유효 시간 추정 불가, 저장은 하되 만료 시간은 None 처리 또는 기본값 사용
                expire_time = None # 또는 기본값 설정 (예: datetime.now() + timedelta(hours=24))
        else:
            print("⚠️ [Kiwoom] 토큰 저장 경고: 응답에 'expires_dt' 키가 없습니다. 만료 시간 확인 불가.")
            # 만료 시간 없으면 저장은 하되 유효성 검사 어려움

        # 만료 1분 전으로 버퍼 설정 (선택적)
        expire_time_buffered_str = None
        if expire_time:
            expire_time_buffered = expire_time - timedelta(seconds=60)
            expire_time_buffered_str = expire_time_buffered.strftime("%Y-%m-%d %H:%M:%S") # 저장할 형식

        # 저장할 토큰 정보 딕셔너리 생성
        _access_token_info = {
            'access_token': access_token, # 실제 토큰 값
            'token_type': token_data.get('token_type', 'Bearer'), # 기본값 Bearer
            'expires_at': expire_time_buffered_str # 계산된 만료 시각 문자열 (None일 수 있음)
        }

        # 파일에 JSON 형태로 저장 (ACCESS_TOKEN_PATH 사용)
        with open(ACCESS_TOKEN_PATH, 'w', encoding='utf-8') as f:
            json.dump(_access_token_info, f, indent=4)
        print(f"✅ [Kiwoom] 토큰 저장 완료: {ACCESS_TOKEN_PATH} (만료 예정: {expire_time_buffered_str or '확인 불가'})")

    except IOError as e:
        print(f"❌ [Kiwoom] 토큰 파일 쓰기 오류: {e}")
    except Exception as e:
        print(f"❌ [Kiwoom] 토큰 파일 저장 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc()

def read_token_from_file():
    """
    키움 토큰 파일에서 토큰 정보를 읽어 전역 변수 _access_token_info에 저장합니다.
    파일 경로는 스크립트 위치 기준으로 설정된 ACCESS_TOKEN_PATH를 사용합니다.
    성공 시 True, 실패 시 False를 반환합니다.
    """
    global _access_token_info
    try:
        with open(ACCESS_TOKEN_PATH, 'r', encoding='utf-8') as f: # 수정된 ACCESS_TOKEN_PATH 사용
            _access_token_info = json.load(f)
        print(f"✅ [Kiwoom] 토큰 로드 완료: {ACCESS_TOKEN_PATH}")
        # 로드 후 필요한 키 있는지 추가 확인 가능 (선택적)
        if 'access_token' not in _access_token_info or 'expires_at' not in _access_token_info:
             print(f"⚠️ [Kiwoom] 로드된 토큰 정보에 필수 키가 없습니다: {_access_token_info}")
             # 필수 키 없으면 유효하지 않은 것으로 간주 가능
             # return False
        return True # 성공
    except FileNotFoundError:
        # print(f"ℹ️ [Kiwoom] 토큰 파일({ACCESS_TOKEN_PATH}) 없음. 새로 발급 필요.") # 정보 로그
        _access_token_info = {} # 파일 없으면 초기화
        return False # 실패
    except json.JSONDecodeError as e:
        print(f"❌ [Kiwoom] 토큰 파일({ACCESS_TOKEN_PATH}) JSON 파싱 오류: {e}")
        _access_token_info = {} # 파싱 실패 시 초기화
        return False # 실패
    except IOError as e:
        print(f"❌ [Kiwoom] 토큰 파일 읽기 오류: {e}")
        _access_token_info = {} # 읽기 실패 시 초기화
        return False # 실패
    except Exception as e:
        print(f"❌ [Kiwoom] 토큰 파일 로드 중 예상치 못한 오류 발생: {e}")
        _access_token_info = {} # 실패 시 초기화
        traceback.print_exc()
        return False # 실패
# --- ---

# --- 토큰 유효성 검사 ---
def is_token_valid():
    """
    현재 로드된 _access_token_info의 만료 시간(expires_at)을 기준으로 유효성을 검사합니다.
    만료 시간이 없거나, 형식 오류거나, 현재 시간보다 과거이면 False를 반환합니다.
    """
    if not _access_token_info or 'expires_at' not in _access_token_info:
        # print("ℹ️ [Kiwoom] 토큰 정보 또는 만료 시간 없음.") # 정보 로그
        return False

    expire_time_str = _access_token_info.get('expires_at')
    if not expire_time_str: # 만료 시간이 None 이나 빈 문자열일 경우
        print("⚠️ [Kiwoom] 토큰 만료 시간이 기록되어 있지 않아 유효성 판단 불가.")
        return False # 유효하지 않다고 간주

    try:
        # 저장된 형식 (YYYY-MM-DD HH:MM:SS) 으로 파싱 시도
        expire_time = datetime.strptime(expire_time_str, "%Y-%m-%d %H:%M:%S")
        is_valid = expire_time > datetime.now()
        # if not is_valid: # 만료 로그
        #     print(f"ℹ️ [Kiwoom] 토큰 만료됨 (만료: {expire_time_str}, 현재: {datetime.now()})")
        return is_valid
    except ValueError:
        print(f"❌ [Kiwoom] 토큰 유효성 검사 오류: 날짜 형식 오류 ('{expire_time_str}')")
        return False
    except Exception as e:
        print(f"❌ [Kiwoom] 토큰 유효성 검사 중 오류: {e}")
        traceback.print_exc()
        return False
# --- ---

# --- 토큰 발급 요청 ---
def issue_token():
    """
    키움 API 서버에 접근 토큰 발급을 요청하고, 성공 시 파일에 저장합니다.
    성공 시 True, 실패 시 False를 반환합니다.
    """
    global _access_token_info # 전역 변수 수정 명시

    # 설정 로드 확인
    if not _config:
        print("❌ [Kiwoom] 토큰 발급 실패: 설정 정보(_config)가 없습니다. load_config()를 먼저 호출하세요.")
        return False

    try:
        # 필요한 설정값 확인
        host = _config.get('base_url')
        app_key = _config.get('appkey')
        secret_key = _config.get('secretkey')

        if not all([host, app_key, secret_key]):
            missing = [k for k, v in {'base_url': host, 'appkey': app_key, 'secretkey': secret_key}.items() if not v]
            print(f"❌ [Kiwoom] 토큰 발급 실패: 설정 파일에 필요한 키 없음 - {missing}")
            return False

        # API 요청 준비
        endpoint = '/oauth2/token'
        url = host + endpoint
        headers = {'Content-Type': 'application/json;charset=UTF-8'}
        data = {
            'grant_type': 'client_credentials',
            'appkey': app_key,
            'secretkey': secret_key,
        }

        # API 요청 실행
        print(f"🚀 [Kiwoom] 토큰 발급 요청: {url}")
        response = requests.post(url, headers=headers, json=data, timeout=10) # timeout 추가
        print(f"🚦 [Kiwoom] 응답 상태 코드: {response.status_code}")
        response.raise_for_status() # HTTP 오류 시 예외 발생

        # 응답 처리
        token_data = response.json()
        print('💾 [Kiwoom] 응답 Body (토큰 정보):')
        print(json.dumps(token_data, indent=4, ensure_ascii=False))

        # 키움 API 성공 코드(0) 확인
        if token_data.get("return_code") == 0:
             save_token_to_file(token_data) # 성공 시 파일에 저장 (내부에서 _access_token_info 업데이트)
             return True # 성공
        else:
             # API 레벨 오류 (ex: 키 오류 등)
             print(f"❌ [Kiwoom] API 오류 응답 (return_code: {token_data.get('return_code')})")
             print(f"   메시지: {token_data.get('return_msg')}")
             _access_token_info = {} # 오류 시 토큰 정보 초기화
             return False # 실패

    except requests.exceptions.Timeout:
        print(f"❌ [Kiwoom] 토큰 발급 요청 시간 초과: {url}")
        _access_token_info = {} # 오류 시 토큰 정보 초기화
        return False # 실패
    except requests.exceptions.RequestException as e:
        # 네트워크 관련 오류 또는 HTTP 오류 (raise_for_status)
        print(f"❌ [Kiwoom] 토큰 발급 요청 중 오류 발생: {e}")
        if e.response is not None:
             print(f"   - 응답 상태 코드: {e.response.status_code}")
             try:
                  print(f"   - 응답 내용: {e.response.json()}")
             except json.JSONDecodeError:
                  print(f"   - 응답 내용 (텍스트): {e.response.text}")
        _access_token_info = {} # 오류 시 토큰 정보 초기화
        return False # 실패
    except json.JSONDecodeError as e:
        # 응답 본문 JSON 파싱 오류
         print(f"❌ [Kiwoom] 토큰 발급 응답 JSON 파싱 오류: {e}")
         print(f"   원본 응답 내용: {response.text if 'response' in locals() else 'N/A'}")
         _access_token_info = {} # 오류 시 토큰 정보 초기화
         return False # 실패
    except Exception as e:
         # 기타 예상치 못한 오류
         print(f"❌ [Kiwoom] 토큰 발급 중 예외 발생: {e}")
         traceback.print_exc()
         _access_token_info = {} # 오류 시 토큰 정보 초기화
         return False # 실패
# --- ---

# --- 메인 인증 함수 ---
def authenticate():
    """
    키움증권 REST API 인증을 수행합니다.
    설정 로드 -> 토큰 로드 -> 유효성 검사 -> (필요시) 토큰 발급 순서로 진행합니다.
    최종 인증 성공 시 True, 실패 시 False를 반환합니다.
    """
    print("\n🔐 [Kiwoom] REST API 인증 시작...")

    # 1. 설정 로드
    if not load_config():
        print("🔥 [Kiwoom] 인증 실패: 설정 파일 로드 실패.")
        return False # 설정 없으면 진행 불가

    # 2. 토큰 로드 및 유효성 검사
    if read_token_from_file() and is_token_valid():
        print("✅ [Kiwoom] 유효한 기존 토큰 사용.")
        return True # 유효하면 바로 성공 처리

    # 3. 기존 토큰 없거나 만료 시 새로 발급 시도
    print("🔄 [Kiwoom] 새 토큰 발급 시도...")
    if issue_token():
        # issue_token 내부에서 성공 시 _access_token_info 업데이트 및 파일 저장 함
        return True # 발급 성공
    else:
        print("🔥 [Kiwoom] 인증 실패: 새 토큰 발급 실패.")
        return False # 발급 실패
# --- ---

# --- 외부 사용 함수 ---
def get_config():
    """로드된 설정 정보(_config) 반환"""
    # 호출 전에 load_config()가 성공했는지 확인하는 것이 좋음
    if not _config:
        print("⚠️ [Kiwoom] 설정 정보가 로드되지 않았습니다. 먼저 authenticate()를 호출하세요.")
        # 필요시 여기서 load_config() 재시도 가능
    return _config

def get_access_token():
    """유효한 접근 토큰 값 반환 (토큰 자체만)"""
    # 토큰 반환 전 유효성 다시 한번 체크 (선택적이지만 안전함)
    if not is_token_valid():
        # print("⚠️ [Kiwoom] 현재 토큰이 유효하지 않습니다. authenticate()를 호출하여 갱신하세요.")
        return None
    return _access_token_info.get('access_token')

def get_token_header():
    """API 요청 시 사용할 Authorization 헤더 문자열 반환 (타입 포함)"""
    token = get_access_token() # 내부적으로 유효성 검사 포함 가능
    if not token:
        return None # 유효한 토큰 없으면 None 반환

    token_type = _access_token_info.get('token_type', 'Bearer') # 기본값 Bearer
    # 토큰 타입이 응답에 없을 경우 대비
    if not token_type:
        token_type = 'Bearer'

    return f"{token_type} {token}" # 예: "Bearer eyJ0eXAiOiJKV1..."
# --- ---

# --- 스크립트 직접 실행 시 테스트 ---
if __name__ == '__main__':
    print("--- Kiwoom Auth Module Test ---")
    auth_result = authenticate() # 인증 시도 및 결과(True/False) 확인

    if auth_result:
        print("\n🎉 Kiwoom 인증 테스트 성공!")
        # 성공 시 설정 및 토큰 정보 확인
        current_config = get_config()
        if current_config:
             app_key = current_config.get('appkey', '키 없음')
             print(f"   - 설정 앱키: {app_key[:5]}...") # 일부만 출력
        else:
             print("   - 설정 정보 없음")

        current_token = get_access_token()
        if current_token:
            print(f"   - 접근 토큰: {current_token[:10]}...") # 일부만 출력
        else:
            print("   - 접근 토큰: 없음 (오류 또는 만료)")

        current_header = get_token_header()
        if current_header:
            print(f"   - 인증 헤더: {current_header[:20]}...") # 일부만 출력
        else:
            print("   - 인증 헤더: 없음 (오류 또는 만료)")
    else:
        print("\n🔥 Kiwoom 인증 테스트 실패!")

    print("\n--- Test End ---")
# --- ---