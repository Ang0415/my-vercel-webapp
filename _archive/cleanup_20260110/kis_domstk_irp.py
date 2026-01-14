# -*- coding: utf-8 -*-
"""
Created on 2025-04-12
IRP 연금 전용 잔고 및 체결내역 조회
"""

import pandas as pd
import time
from datetime import datetime
import kis_auth_irp as kis  # IRP 인증 모듈 import

# API 요청 함수 (kis_auth_irp 모듈의 _url_fetch 함수 사용)
_url_fetch = kis._url_fetch

# IRP 단순 잔고 조회 (정책: /trading/pension/inquire-balance)
def get_inquire_irp_balance_lst():
    """
    IRP 계좌의 단순 잔고 목록을 조회합니다.
    """
    print("\n📊 [STEP 1] IRP 단순 잔고 조회")
    url = "/uapi/domestic-stock/v1/trading/pension/inquire-balance" # KIS API 엔드포인트
    tr_id = "TTTC2208R" # KIS API TR_ID (IRP 단순 잔고 조회)
    params = {
        "CANO": kis.getTREnv().my_acct,         # 계좌번호 (kis_auth_irp 모듈에서 가져옴)
        "ACNT_PRDT_CD": kis.getTREnv().my_prod, # 상품코드 (kis_auth_irp 모듈에서 가져옴)
        "ACCA_DVSN_CD": "00", # 계좌구분코드 (00: 전체)
        "INQR_DVSN": "00",    # 조회구분 (00: 단순 잔고 조회)
        "CTX_AREA_FK100": "", # 연속조회 키
        "CTX_AREA_NK100": ""  # 연속조회 키
    }

    print("\n📤 [잔고 요청 파라미터 확인]")
    for k, v in params.items():
        print(f"  {k}: {v}")

    try: # API 호출 및 응답 처리 예외 처리 추가
        res = _url_fetch(url, tr_id, "", params) # API 호출
        if res is None or res.getResponse().status_code != 200:
             print(f"❌ API 호출 실패! HTTP Status: {res.getResponse().status_code if res else 'N/A'}")
             return pd.DataFrame() # 호출 실패 시 빈 DF 반환

        print("\n📥 [잔고 응답 헤더]")
        for k, v in res.getHeader().items():
            print(f"  {k}: {v}")

        body = res.getBody()
        rt_cd = body.get("rt_cd", "1")
        if rt_cd != "0":
            print(f"❌ API 오류! (rt_cd: {rt_cd}, msg_cd: {body.get('msg_cd')}) msg: {body.get('msg1')}")
            return pd.DataFrame() # API 오류 시 빈 DF 반환

        output1 = body.get("output1", None) # 응답 바디에서 output1 추출

        if output1 is None or not isinstance(output1, list):
            print("❗ output1이 존재하지 않거나 리스트가 아님")
            print("❗ IRP 잔고가 없습니다.")
            return pd.DataFrame() # 빈 DataFrame 반환

        # 결과가 있으면 DataFrame으로 변환하여 반환
        df = pd.DataFrame(output1)
        return df
    except Exception as e:
        print(f"❌ get_inquire_irp_balance_lst 함수 실행 중 예외 발생: {e}")
        import traceback
        traceback.print_exc() # 상세 오류 출력
        return pd.DataFrame() # 예외 발생 시 빈 DF 반환

# IRP 체결기준 잔고 조회 (/trading/pension/inquire-present-balance)
def get_inquire_present_balance_irp():
    """
    IRP 계좌의 체결 기준 잔고 목록을 조회합니다. (참고용, 현재 main_irp.py에서는 사용 안 함)
    """
    print("\n📊 [참고] IRP 체결기준 잔고 조회")
    url = "/uapi/domestic-stock/v1/trading/pension/inquire-present-balance" # KIS API 엔드포인트
    tr_id = "TTTC2202R" # KIS API TR_ID (IRP 체결 기준 잔고 조회)
    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "USER_DVSN_CD": "00", # 사용자구분코드
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }
    try: # 예외 처리 추가
        # ... (이하 로직은 get_inquire_irp_balance_lst와 유사하게 try...except 추가 권장) ...
        res = _url_fetch(url, tr_id, "", params)
        # ... (오류 처리 및 반환 로직) ...
        # 여기서는 간단히 기존 코드 유지 (필요시 위 함수처럼 수정)
        body = res.getBody()
        output1 = body.get("output1", None)

        if output1 is None or not isinstance(output1, list):
            print("❗ output1이 존재하지 않거나 리스트가 아님")
            print("❗ IRP 체결기준 잔고가 없습니다.")
            return pd.DataFrame()

        df = pd.DataFrame(output1)
        return df
    except Exception as e:
        print(f"❌ get_inquire_present_balance_irp 함수 실행 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

##################################################
# ✅ 체결 내역 조회 (최근 기간) - IRP 용으로 추가됨
##################################################

def get_inquire_daily_ccld_lst(dv="01", inqr_strt_dt="", inqr_end_dt="", tr_cont="", FK100="", NK100="", dataframe=None):
    """
    IRP 계좌의 지정된 기간 동안의 일별 주문 체결 내역을 조회합니다 (페이징 지원).
    dv: 조회구분 ('01': 정순, '00': 역순)
    inqr_strt_dt: 조회시작일자 (YYYYMMDD), 미입력 시 3개월 전
    inqr_end_dt: 조회종료일자 (YYYYMMDD), 미입력 시 오늘
    """
    # ⚠️ 중요: IRP 계좌의 '주식일별주문체결조회'에 해당하는 정확한 TR_ID 확인 필요!
    tr_id = "TTTC8001R" # <<< ⚠️ 반드시 IRP 계좌용 TR_ID로 확인 및 수정하세요!
    url = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld' # 엔드포인트도 IRP용으로 다를 수 있는지 확인 필요

    # 날짜 미지정 시 기본값 설정
    if not inqr_strt_dt:
        today = datetime.today()
        three_months_ago = today - pd.DateOffset(months=3)
        inqr_strt_dt = three_months_ago.strftime("%Y%m%d")
    if not inqr_end_dt:
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

    # API 요청 파라미터 설정
    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "INQR_STRT_DT": inqr_strt_dt,
        "INQR_END_DT": inqr_end_dt,
        "SLL_BUY_DVSN_CD": "00",
        "INQR_DVSN": dv,
        "PDNO": "",
        "CCLD_DVSN": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": FK100,
        "CTX_AREA_NK100": NK100
    }

    print(f"\n📤 [체결내역 요청] TR_ID: {tr_id}, 기간: {inqr_strt_dt}~{inqr_end_dt}")

    try: # **** 함수 메인 로직에 try...except 추가 ****
        # API 호출
        res = _url_fetch(url, tr_id, tr_cont, params)
        if res is None or res.getResponse().status_code != 200:
             print(f"❌ API 호출 실패! HTTP Status: {res.getResponse().status_code if res else 'N/A'}")
             # 기존 데이터프레임이 있으면 그것을 반환, 없으면 빈 데이터프레임 반환
             return dataframe if dataframe is not None else pd.DataFrame()

        header = res.getHeader()
        body = res.getBody()

        # API 응답 코드 확인 (rt_cd)
        rt_cd = body.get("rt_cd", "1")
        if rt_cd != "0":
            # ⚠️ 오류 메시지를 더 상세히 출력하여 원인 파악 용이하게 함
            print(f"❌ API 오류 발생! (rt_cd: {rt_cd}, msg_cd: {body.get('msg_cd')})")
            print(f"   오류 메시지(msg1): {body.get('msg1')}")
            print(f"   >>>>> TR_ID({tr_id})가 IRP 계좌에 맞는지 확인하세요! <<<<<")
            return dataframe if dataframe is not None else pd.DataFrame()

        # 정상 응답 시 데이터 처리
        output1 = body.get("output1", None)
        if not output1 or not isinstance(output1, list):
            print("ℹ️ 해당 기간의 체결내역(output1)이 없습니다.")
            return dataframe if dataframe is not None else pd.DataFrame()

        df = pd.DataFrame(output1)
        print(f"✅ 체결내역 {len(df)}건 수신")

        # 필요한 컬럼만 선택
        required_cols = ["ord_dt", "prdt_name", "pdno", "sll_buy_dvsn_cd_name", "ord_qty", "ord_unpr", "tot_ccld_amt"]
        available_cols = [col for col in required_cols if col in df.columns]

        if not available_cols:
             print(f"⚠️ 응답 데이터에 필요한 컬럼이 하나도 없습니다! API 응답 확인 필요.")
             print("   전체 응답 컬럼:", df.columns)
             return dataframe if dataframe is not None else pd.DataFrame()
        elif len(available_cols) < len(required_cols):
             print(f"⚠️ 필요한 컬럼 중 일부가 누락되었습니다. 사용 가능한 컬럼: {available_cols}")
             df = df[available_cols]
        else:
             df = df[available_cols]

        # DataFrame 누적
        current_dataframe = pd.concat([dataframe, df], ignore_index=True) if dataframe is not None else df

        # 연속 조회 처리 (페이징)
        tr_cont = header.get("tr_cont", "")
        FK100 = body.get("ctx_area_fk100", "")
        NK100 = body.get("ctx_area_nk100", "")

        if tr_cont in ["F", "M"]:
            print("... 다음 페이지 데이터 조회 중 ...")
            time.sleep(0.2)
            # 재귀 호출 시 누적된 current_dataframe 전달
            return get_inquire_daily_ccld_lst(dv=dv, inqr_strt_dt=inqr_strt_dt, inqr_end_dt=inqr_end_dt, tr_cont="N", FK100=FK100, NK100=NK100, dataframe=current_dataframe)
        else:
            print(f"✅ 체결내역 조회 완료 (총 {len(current_dataframe)}건)")
            return current_dataframe # 최종 누적 결과 반환

    except Exception as e: # **** 예상치 못한 예외 발생 시 처리 ****
        print(f"❌ get_inquire_daily_ccld_lst 함수 실행 중 예외 발생: {e}")
        import traceback
        traceback.print_exc() # 상세 오류 스택 출력
        # 예외 발생 시에도 기존 데이터프레임 또는 빈 데이터프레임 반환하여 NoneType 오류 방지
        return dataframe if dataframe is not None else pd.DataFrame()