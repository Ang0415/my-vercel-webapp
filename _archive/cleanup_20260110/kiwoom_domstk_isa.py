# -*- coding: utf-8 -*-
# kiwoom_domstk_isa.py: 키움증권 REST API 호출 함수 모음 (kt00018, kt00016, ka10170 포함)

import requests
import json
import time
from datetime import datetime, date # date 추가
import traceback # 오류 상세 출력을 위해 추가
import pandas as pd # Pandas 추가
# 인증 모듈 임포트 (파일명 확인: kiwoom_auth_isa.py 사용)
import kiwoom_auth_isa as auth

# --- 기본 API 요청 함수 (api-id, cont-yn, next-key 지원, 자동 재인증) ---
def _kiwoom_fetch(path: str, method: str = "GET", api_id: str = None, params: dict = None, body: dict = None, cont_yn: str = 'N', next_key: str = ''):
    """키움증권 REST API 공통 요청 함수"""
    config = auth.get_config()
    token_header = auth.get_token_header() # 예: "Bearer eyJ..."

    if not config or 'base_url' not in config:
        print("❌ API 요청 실패: 설정 파일의 'base_url'을 찾을 수 없습니다.")
        return None
    if not token_header:
        # 토큰 없거나 만료 시 재인증 시도
        print("⚠️ 유효한 토큰 없음. 재인증 시도...")
        if not auth.authenticate():
            print("❌ API 요청 실패: 인증에 실패했습니다.")
            return None
        token_header = auth.get_token_header() # 새 토큰 헤더 가져오기
        if not token_header: # 재인증 후에도 토큰 없으면 실패
            print("❌ API 요청 실패: 재인증 후에도 토큰을 가져올 수 없습니다.")
            return None

    base_url = config['base_url']
    url = f"{base_url}{path}" # 전체 URL 조합

    headers = {
        "authorization": token_header, # 인증 모듈에서 가져온 전체 헤더 (Bearer 포함)
        "appkey": config.get('appkey', ''),
        "appsecret": config.get('secretkey', ''),
        "Content-Type": "application/json; charset=utf-8",
        # --- kt00018/kt00016/ka10170 예제 기반 헤더 ---
        "cont-yn": cont_yn if cont_yn else 'N',
        "next-key": next_key if next_key else '',
    }
    # api-id가 있으면 헤더에 추가
    if api_id:
        headers["api-id"] = api_id

    print(f"\n🚀 API 요청: {method} {url}")
    # 헤더 출력 시 secretkey 노출 주의!
    # print(f"   - 헤더: { {k: (v[:10] + '...' if k == 'authorization' else v) for k, v in headers.items()} }")
    if params: print(f"   - 파라미터(URL): {params}")
    if body: print(f"   - 바디(JSON): {json.dumps(body, ensure_ascii=False)}")

    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=body)
        else:
            print(f"❌ 지원하지 않는 HTTP 메소드: {method}")
            return None

        print(f"🚦 응답 상태 코드: {response.status_code}")
        # 응답 헤더에서 연속 조회 정보 추출
        response_headers = response.headers
        next_key_from_header = response_headers.get('next-key', '')
        cont_yn_from_header = response_headers.get('cont-yn', 'N')
        print(f"   - 응답 헤더 (cont-yn): {cont_yn_from_header}")
        print(f"   - 응답 헤더 (next-key): {next_key_from_header}")

        # 응답 본문 처리
        response_package = {'headers': {'next-key': next_key_from_header, 'cont-yn': cont_yn_from_header}}
        if response.status_code == 200:
            response_data = response.json()
            response_package['body'] = response_data # body 추가
            # 응답 본문에서 return_code 확인 (키움 API 성공 시 0)
            return_code = response_data.get('return_code', -1) # 기본값 -1 (오류)
            if return_code == 0:
                print("✅ API 호출 성공 (return_code: 0)")
            else:
                print(f"❌ API 오류 응답 (return_code: {return_code})")
                print(f"   메시지: {response_data.get('return_msg', '메시지 없음')}")
        else: # HTTP 상태 코드가 200이 아닌 경우
            print(f"❌ API 호출 실패! (HTTP Status: {response.status_code}) 응답 내용:")
            try:
                error_response = response.json()
                print(json.dumps(error_response, indent=4, ensure_ascii=False))
                response_package['body'] = error_response
            except json.JSONDecodeError:
                error_text = response.text
                print(error_text)
                response_package['body'] = {'error': 'Non-JSON Response', 'status_code': response.status_code, 'text': error_text}

        return response_package # 응답 헤더와 body 포함하여 반환

    except requests.exceptions.RequestException as e:
        print(f"❌ API 요청 중 네트워크 오류 발생: {e}")
        return None
    except Exception as e:
        print(f"❌ API 요청 처리 중 예외 발생: {e}")
        traceback.print_exc()
        return None

# --- 데이터 클리닝 함수 ---
def clean_num_str(num_str, type_func=int):
    """문자열 형태의 숫자를 실제 숫자 타입으로 변환 (부호 및 0 처리)"""
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
    """계좌평가잔고내역요청 (kt00018) API 호출"""
    print(f"\n📊 계좌 평가 잔고 내역 요청 (qry_tp: {query_type}, dmst_stex_tp: {exchange_type})")
    api_path = "/api/dostk/acnt"
    http_method = "POST"
    api_identifier = "kt00018"
    request_body = {'qry_tp': query_type, 'dmst_stex_tp': exchange_type}

    response_package = _kiwoom_fetch(
        path=api_path, method=http_method, api_id=api_identifier,
        body=request_body, cont_yn=cont_yn, next_key=next_key
    )

    # 결과 포맷팅
    result = {'success': False, 'data': None, 'next_key': None, 'cont_yn': 'N'}
    if response_package and 'body' in response_package:
        result['data'] = response_package['body']
        result['next_key'] = response_package['headers'].get('next-key')
        result['cont_yn'] = response_package['headers'].get('cont-yn', 'N')
        if response_package['body'].get('return_code') == 0:
            result['success'] = True
            print("📊 잔고 조회 응답 수신 (성공)")
        else:
            print("📊 잔고 조회 응답 수신 (API 오류)")
            # 오류 시 body 내용 출력은 _kiwoom_fetch에서 하므로 여기선 생략
    else:
        print("❌ 잔고 조회 실패 (네트워크/HTTP 오류 등)")

    return result

# 일별계좌수익률상세현황요청 (kt00016)
def get_daily_account_profit_loss(start_date: str, end_date: str, cont_yn: str = 'N', next_key: str = ''):
    """일별계좌수익률상세현황요청 (kt00016) API 호출"""
    print(f"\n📊 일별 계좌 수익률 상세 현황 요청 (기간: {start_date} ~ {end_date})")
    api_path = "/api/dostk/acnt"
    http_method = "POST"
    api_identifier = "kt00016"
    request_body = {
        'fr_dt': start_date.replace("-", ""), #<y_bin_46>MMDD
        'to_dt': end_date.replace("-", ""),   #<y_bin_46>MMDD
    }

    response_package = _kiwoom_fetch(
        path=api_path, method=http_method, api_id=api_identifier,
        body=request_body, cont_yn=cont_yn, next_key=next_key
    )

    # 결과 포맷팅
    result = {'success': False, 'data': None, 'next_key': None, 'cont_yn': 'N'}
    if response_package and 'body' in response_package:
        result['data'] = response_package['body']
        result['next_key'] = response_package['headers'].get('next-key')
        result['cont_yn'] = response_package['headers'].get('cont-yn', 'N')
        if response_package['body'].get('return_code') == 0:
            result['success'] = True
            print("📊 일별 수익률 조회 응답 수신 (성공)")
        else:
            print("📊 일별 수익률 조회 응답 수신 (API 오류)")
    else:
        print("❌ 일별 수익률 조회 실패 (네트워크/HTTP 오류 등)")

    return result

# *** 추가된 함수: 당일매매일지요청 (ka10170) ***
def get_daily_trading_log(base_date: str, ottks_type: str = '1', cash_credit_type: str = '0', cont_yn: str = 'N', next_key: str = ''):
    """당일매매일지요청 (ka10170) API 호출"""
    print(f"\n📊 {base_date} 매매일지 요청 (ottks_tp: {ottks_type}, ch_crd_tp: {cash_credit_type})")

    # --- ka10170 예제 기반 정보 ---
    api_path = "/api/dostk/acnt" # 이전과 동일 경로 사용
    http_method = "POST"
    api_identifier = "ka10170"   # api-id 변경

    # 요청 Body (JSON) 구성
    request_body = {
        'base_dt': base_date.replace("-", ""), #<y_bin_46>MMDD 형식
        'ottks_tp': ottks_type,
        'ch_crd_tp': cash_credit_type,
    }
    # -----------------------------

    # API 호출
    response_package = _kiwoom_fetch(
        path=api_path,
        method=http_method,
        api_id=api_identifier, # api-id 전달
        body=request_body,
        cont_yn=cont_yn,       # cont-yn 헤더 전달
        next_key=next_key      # next-key 헤더 전달
    )

    # 결과 포맷팅
    result = {'success': False, 'data': None, 'next_key': None, 'cont_yn': 'N'}
    if response_package and 'body' in response_package:
        result['data'] = response_package['body']
        result['next_key'] = response_package['headers'].get('next-key')
        result['cont_yn'] = response_package['headers'].get('cont-yn', 'N')
        if response_package['body'].get('return_code') == 0:
            result['success'] = True
            print(f"📊 {base_date} 매매일지 조회 응답 수신 (성공)")
        else:
            print(f"📊 {base_date} 매매일지 조회 응답 수신 (API 오류)")
    else:
        print(f"❌ {base_date} 매매일지 조회 실패 (네트워크/HTTP 오류 등)")

    return result


# --- 실행 테스트 구간 ---
if __name__ == '__main__':
    print(">>> kiwoom_domstk_isa.py 직접 실행 테스트 <<<")
    import pandas as pd
    from datetime import date

    # --- 데이터 클리닝 함수 (이전과 동일) ---
    def clean_num_str(num_str, type_func=int):
        if not num_str: return type_func(0)
        try:
            cleaned = num_str.lstrip('-').lstrip('0')
            if not cleaned: return type_func(0)
            value = type_func(cleaned)
            return -value if num_str.startswith('-') else value
        except (ValueError, TypeError):
            return type_func(0)

    # 1. 인증 수행
    if not auth.authenticate():
        print("🔥 인증 실패! API 테스트를 진행할 수 없습니다.")
    else:
        print("\n✅ 인증 성공! API 테스트 시작...")

        # =======================================
        # [테스트 1] 계좌 평가 잔고 조회 (kt00018)
        # =======================================
        # (이전과 동일 - 코드 생략하지 않음)
        print("\n" + "="*50)
        print(" [테스트 1] 계좌 평가 잔고 조회 (kt00018)")
        print("="*50)
        balance_result = get_account_evaluation_balance(query_type='1', exchange_type='KRX')
        if balance_result and balance_result.get('success'):
            print("\n✅ 잔고 조회 테스트 성공!")
            api_data = balance_result.get('data', {})
            summary_data = api_data
            stock_list = api_data.get('acnt_evlt_remn_indv_tot', [])
            print("\n--- 💰 계좌 요약 정보 ---")
            try:
                tot_pur = clean_num_str(summary_data.get('tot_pur_amt', '0'))
                tot_evlt = clean_num_str(summary_data.get('tot_evlt_amt', '0'))
                tot_pl = clean_num_str(summary_data.get('tot_evlt_pl', '0'))
                tot_rt = summary_data.get('tot_prft_rt', '0.0')
                prsm_asset = clean_num_str(summary_data.get('prsm_dpst_aset_amt', '0'))
                print(f"  총 매입 금액   : {tot_pur:>15,} 원")
                print(f"  총 평가 금액   : {tot_evlt:>15,} 원")
                print(f"  총 평가 손익   : {tot_pl:>+15,} 원")
                print(f"  총 수익률      : {float(tot_rt):>15.2f} %")
                print(f"  추정 예탁 자산 : {prsm_asset:>15,} 원")
            except Exception as e: print(f"  ❌ 요약 정보 처리 중 오류: {e}")
            print("\n--- 📈 보유 종목 상세 ---")
            if stock_list:
                try:
                    df = pd.DataFrame(stock_list)
                    df_display = df[['stk_nm', 'stk_cd', 'rmnd_qty', 'pur_pric','cur_prc', 'evlt_amt', 'evltv_prft', 'prft_rt', 'poss_rt']].copy()
                    df_display.rename(columns={'stk_nm': '종목명', 'stk_cd': '종목코드', 'rmnd_qty': '보유수량','pur_pric': '매입단가', 'cur_prc': '현재가', 'evlt_amt': '평가금액','evltv_prft': '평가손익', 'prft_rt': '수익률(%)', 'poss_rt': '보유비중(%)'}, inplace=True)
                    numeric_cols_int = ['보유수량', '매입단가', '현재가', '평가금액', '평가손익']
                    numeric_cols_float = ['수익률(%)', '보유비중(%)']
                    for col in numeric_cols_int: df_display[col] = df_display[col].apply(lambda x: clean_num_str(x, int))
                    for col in numeric_cols_float:
                         try: df_display[col] = pd.to_numeric(df_display[col], errors='coerce').fillna(0.0)
                         except Exception: df_display[col] = 0.0
                    pd.options.display.float_format = '{:,.2f}'.format
                    print(df_display.to_string(index=False))
                except Exception as e:
                     print(f"  ❌ 보유 종목 리스트 처리 중 오류: {e}")
                     traceback.print_exc()
            else: print("  ℹ️ 보유 중인 종목이 없습니다.")
            next_key_bal = balance_result.get('next_key')
            cont_yn_bal = balance_result.get('cont_yn')
            if cont_yn_bal == 'Y' and next_key_bal: print(f"\n🔄 잔고 연속 조회가 필요합니다. (next-key: {next_key_bal})")
            else: print("\nℹ️ 잔고 연속 조회 필요 없음.")
        else:
            print("\n❌ 잔고 조회 테스트 실패.")
            if balance_result and 'data' in balance_result: print("--- 실패 응답 데이터 (잔고) ---"); print(json.dumps(balance_result.get('data'), indent=4, ensure_ascii=False))


        # ==================================================
        # [테스트 2] 특정일 계좌 수익률 요약 조회 (kt00016)
        # ==================================================
        # (이전과 동일 - 코드 생략하지 않음)
        print("\n" + "="*50)
        print(" [테스트 2] 특정일 계좌 수익률 요약 조회 (kt00016)")
        print("="*50)
        target_dates = ["2025-04-10", "2025-04-11"]
        for target_date in target_dates:
            print(f"\n--- 📅 {target_date} 조회 시작 ---")
            daily_summary_result = get_daily_account_profit_loss(start_date=target_date, end_date=target_date)
            if daily_summary_result and daily_summary_result.get('success'):
                print(f"✅ {target_date} 요약 조회 테스트 성공!")
                summary_pl_data = daily_summary_result.get('data', {})
                print(f"--- ⏳ {target_date} 계좌 수익률 요약 ---")
                try:
                    tot_amt_start = clean_num_str(summary_pl_data.get('tot_amt_fr', '0'))
                    tot_amt_end = clean_num_str(summary_pl_data.get('tot_amt_to', '0'))
                    period_pl = clean_num_str(summary_pl_data.get('evltv_prft', '0'))
                    period_rt = summary_pl_data.get('prft_rt', '0.0')
                    invt_base = clean_num_str(summary_pl_data.get('invt_bsamt', '0'))
                    print(f"  *조회일* : {target_date}")
                    print(f"  시작 총자산 ('fr'): {tot_amt_start:>15,} 원")
                    print(f"  종료 총자산 ('to'): {tot_amt_end:>15,} 원")
                    print(f"  평가 손익        : {period_pl:>+15,} 원")
                    print(f"  수익률          : {float(period_rt):>15.2f} %")
                    print(f"  투자 원금        : {invt_base:>15,} 원")
                except Exception as e:
                    print(f"  ❌ {target_date} 요약 정보 처리 중 오류: {e}")
                    traceback.print_exc()
                    print(f"  --- 원본 응답 데이터 ({target_date}) ---"); print(json.dumps(summary_pl_data, indent=4, ensure_ascii=False))
                next_key_pl = daily_summary_result.get('next_key')
                cont_yn_pl = daily_summary_result.get('cont_yn')
                if cont_yn_pl == 'Y' and next_key_pl: print(f"  (ℹ️ 특이: 단일 날짜 조회인데 연속 필요? next-key: {next_key_pl})")
            else:
                print(f"\n❌ {target_date} 요약 조회 테스트 실패.")
                if daily_summary_result and 'data' in daily_summary_result: print(f"--- 실패 응답 데이터 ({target_date}) ---"); print(json.dumps(daily_summary_result.get('data'), indent=4, ensure_ascii=False))


        # =======================================
        # [테스트 3] 당일 매매일지 조회 (ka10170)
        # =======================================
        print("\n" + "="*50)
        print(" [테스트 3] 당일 매매일지 조회 (ka10170)")
        print("="*50)

        trade_log_date = "2025-04-07"

        # ottks_tp='1' 또는 '2' 중 어떤 것이 매수내역을 보여줬는지 확인 필요
        # 일단 이전 성공 로그 기준으로 '2'를 가정하나, 안되면 '1'로 다시 시도
        # 또는 문서에서 '전체'를 의미하는 값을 찾아야 할 수 있음
        trade_log_result = get_daily_trading_log(
            base_date=trade_log_date,
            ottks_type='2', # 또는 '1'
            cash_credit_type='0'
        )

        if trade_log_result and trade_log_result.get('success'):
            print(f"\n✅ {trade_log_date} 매매일지 조회 테스트 성공!")

            full_response_data = trade_log_result.get('data', {})
            # 전체 응답은 디버깅 시 필요하면 주석 해제
            # print("\n--- 전체 응답 데이터 (매매일지) ---")
            # print(json.dumps(full_response_data, indent=4, ensure_ascii=False))

            # --- 수정: 실제 리스트 키로 변경 ---
            trade_list_key = 'tdy_trde_diary' # !!! 키 이름 수정 !!!
            trade_list = full_response_data.get(trade_list_key, [])
            # ---------------------------------

            if trade_list:
                try:
                    df_trades = pd.DataFrame(trade_list)

                    # --- 수정: 실제 응답 컬럼 기준으로 변경 ---
                    required_trade_cols = [
                        'stk_nm', 'stk_cd', 'buy_qty', 'buy_avg_pric', 'buy_amt',
                        'sell_qty', 'sel_avg_pric', 'sell_amt', 'pl_amt', 'prft_rt', 'cmsn_alm_tax'
                    ]
                    trade_col_rename_map = {
                        'stk_nm': '종목명', 'stk_cd': '종목코드',
                        'buy_qty': '매수수량', 'buy_avg_pric': '매수단가', 'buy_amt': '매수금액',
                        'sell_qty': '매도수량', 'sel_avg_pric': '매도단가', 'sell_amt': '매도금액',
                        'pl_amt': '손익금액', 'prft_rt': '수익률(%)', 'cmsn_alm_tax': '수수료/세금'
                    }
                    # ---------------------------------------

                    available_trade_cols = [col for col in required_trade_cols if col in df_trades.columns]

                    if not available_trade_cols:
                        # 이 경우는 거의 없겠지만 방어 코드
                        print(f"❌ 응답 데이터 리스트 ('{trade_list_key}') 내에 필요한 컬럼이 없습니다.")
                        print(json.dumps(trade_list[:1], indent=4, ensure_ascii=False))
                    else:
                        df_display_trades = df_trades[available_trade_cols].copy()
                        df_display_trades.rename(columns=trade_col_rename_map, inplace=True)

                        # --- 수정: 컬럼에 맞게 숫자 변환 ---
                        numeric_trade_cols_int = [
                            '매수수량', '매수단가', '매수금액', '매도수량',
                            '매도단가', '매도금액', '손익금액', '수수료/세금'
                        ]
                        numeric_trade_cols_float = ['수익률(%)']

                        for col in numeric_trade_cols_int:
                            if col in df_display_trades.columns:
                                df_display_trades[col] = df_display_trades[col].apply(lambda x: clean_num_str(x, int))
                        for col in numeric_trade_cols_float:
                            if col in df_display_trades.columns:
                                 try:
                                     df_display_trades[col] = pd.to_numeric(df_display_trades[col], errors='coerce').fillna(0.0)
                                 except Exception: df_display_trades[col] = 0.0
                        # ---------------------------------

                        print(f"\n--- 📜 {trade_log_date} 매매일지 ---")
                        pd.options.display.float_format = '{:,.2f}'.format # 수익률 위해 소수점 표시
                        pd.set_option('display.max_rows', 100)
                        print(df_display_trades.to_string(index=False))
                        pd.reset_option('display.max_rows')
                        pd.reset_option('display.float_format')

                except Exception as e:
                    print(f"  ❌ 매매일지 데이터 처리 중 오류: {e}")
                    traceback.print_exc()
                    # print("  --- 원본 리스트 데이터 (매매일지 일부) ---")
                    # print(json.dumps(trade_list[:2], indent=4, ensure_ascii=False))
            else:
                 # API는 성공했으나, list key ('tdy_trde_diary') 아래에 데이터가 없는 경우
                 # (예: 해당일에 실제 거래가 없었거나, 파라미터 조건에 맞는 거래가 없는 경우)
                print(f"  ℹ️ {trade_log_date} 에 해당하는 '{trade_list_key}' 매매 내역이 없습니다.")


            # 매매일지 연속 조회 처리
            next_key_trade = trade_log_result.get('next_key')
            cont_yn_trade = trade_log_result.get('cont_yn')
            if cont_yn_trade == 'Y' and next_key_trade:
                print(f"\n🔄 매매일지 연속 조회가 필요합니다. (next-key: {next_key_trade})")
            else:
                print("\nℹ️ 매매일지 연속 조회 필요 없음.")

        else:
            print(f"\n❌ {trade_log_date} 매매일지 조회 테스트 실패.")
            if trade_log_result and 'data' in trade_log_result:
                 print("--- 실패 응답 데이터 (매매일지) ---")
                 print(json.dumps(trade_log_result.get('data'), indent=4, ensure_ascii=False))