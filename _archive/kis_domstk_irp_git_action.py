# -*- coding: utf-8 -*-
"""
Created on 2025-04-12
IRP 연금 전용 잔고 및 체결내역 조회 (GitHub Action용)
"""

import pandas as pd
import time
from datetime import datetime
try:
    import kis_auth_irp_git_action as kis
except ModuleNotFoundError:
    try:
        import kis_auth_irp as kis
    except ModuleNotFoundError:
        print("❌ 인증 모듈(kis_auth_irp)을 찾을 수 없습니다.")
        raise

# API 요청 함수 (kis_auth_irp 모듈의 _url_fetch 함수 사용)
_url_fetch = kis._url_fetch

# IRP 단순 잔고 조회 (정책: /trading/pension/inquire-balance)
def get_inquire_irp_balance_lst():
    """
    IRP 계좌의 단순 잔고 목록을 조회합니다.
    """
    print("\n📊 [STEP 1] IRP 단순 잔고 조회 (GitAction)")
    url = "/uapi/domestic-stock/v1/trading/pension/inquire-balance" # KIS API 엔드포인트
    tr_id = "TTTC2208R" # KIS API TR_ID (IRP 단순 잔고 조회)
    params = {
        "CANO": kis.getTREnv().my_acct,         # 계좌번호
        "ACNT_PRDT_CD": kis.getTREnv().my_prod, # 상품코드
        "ACCA_DVSN_CD": "00", # 계좌구분코드 (00: 전체)
        "INQR_DVSN": "00",    # 조회구분 (00: 단순 잔고 조회)
        "CTX_AREA_FK100": "", # 연속조회 키
        "CTX_AREA_NK100": ""  # 연속조회 키
    }

    try: 
        res = _url_fetch(url, tr_id, "", params) # API 호출
        if res is None or res.getResponse().status_code != 200:
             print(f"❌ API 호출 실패! Status: {res.getResponse().status_code if res else 'N/A'}")
             return pd.DataFrame()

        body = res.getBody()
        rt_cd = body.get("rt_cd", "1")
        if rt_cd != "0":
            print(f"❌ API 오류! (rt_cd: {rt_cd}) msg: {body.get('msg1')}")
            return pd.DataFrame()

        output1 = body.get("output1", None)

        if output1 is None or not isinstance(output1, list):
            print("❗ IRP 잔고가 없습니다.")
            return pd.DataFrame()

        df = pd.DataFrame(output1)
        return df
    except Exception as e:
        print(f"❌ get_inquire_irp_balance_lst 오류: {e}")
        return pd.DataFrame()

# IRP 체결기준 잔고 조회 (/trading/pension/inquire-present-balance)
def get_inquire_present_balance_irp():
    """
    IRP 계좌의 체결 기준 잔고 목록을 조회합니다.
    """
    # print("\n📊 [참고] IRP 체결기준 잔고 조회")
    url = "/uapi/domestic-stock/v1/trading/pension/inquire-present-balance"
    tr_id = "TTTC2202R"
    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "USER_DVSN_CD": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }
    try: 
        res = _url_fetch(url, tr_id, "", params)
        if not res: return pd.DataFrame()
        body = res.getBody()
        output1 = body.get("output1", None)

        if output1 is None or not isinstance(output1, list):
            return pd.DataFrame()

        df = pd.DataFrame(output1)
        return df
    except Exception as e:
        print(f"❌ get_inquire_present_balance_irp 오류: {e}")
        return pd.DataFrame()

def get_inquire_daily_ccld_lst(dv="01", inqr_strt_dt="", inqr_end_dt="", tr_cont="", FK100="", NK100="", dataframe=None):
    """
    IRP 계좌의 지정된 기간 동안의 일별 주문 체결 내역을 조회합니다 (페이징 지원).
    """
    tr_id = "TTTC8001R" 
    url = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld' 

    if not inqr_strt_dt:
        today = datetime.today()
        three_months_ago = today - pd.DateOffset(months=3)
        inqr_strt_dt = three_months_ago.strftime("%Y%m%d")
    if not inqr_end_dt:
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

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

    try:
        res = _url_fetch(url, tr_id, tr_cont, params)
        if res is None or res.getResponse().status_code != 200:
             return dataframe if dataframe is not None else pd.DataFrame()

        header = res.getHeader()
        body = res.getBody()

        rt_cd = body.get("rt_cd", "1")
        if rt_cd != "0":
            return dataframe if dataframe is not None else pd.DataFrame()

        output1 = body.get("output1", None)
        if not output1 or not isinstance(output1, list):
            return dataframe if dataframe is not None else pd.DataFrame()

        df = pd.DataFrame(output1)
        required_cols = ["ord_dt", "prdt_name", "pdno", "sll_buy_dvsn_cd_name", "ord_qty", "ord_unpr", "tot_ccld_amt"]
        available_cols = [col for col in required_cols if col in df.columns]
        
        if available_cols:
             df = df[available_cols]

        current_dataframe = pd.concat([dataframe, df], ignore_index=True) if dataframe is not None else df

        tr_cont = header.get("tr_cont", "")
        FK100 = body.get("ctx_area_fk100", "")
        NK100 = body.get("ctx_area_nk100", "")

        if tr_cont in ["F", "M"]:
            time.sleep(0.2)
            return get_inquire_daily_ccld_lst(dv=dv, inqr_strt_dt=inqr_strt_dt, inqr_end_dt=inqr_end_dt, tr_cont="N", FK100=FK100, NK100=NK100, dataframe=current_dataframe)
        else:
            return current_dataframe 

    except Exception as e:
        print(f"❌ get_inquire_daily_ccld_lst 오류: {e}")
        return dataframe if dataframe is not None else pd.DataFrame()
