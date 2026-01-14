# -*- coding: utf-8 -*-
"""
예제 기반 kis_domstk.py 전체 코드
"""

import pandas as pd
import time
from datetime import datetime
import kis_auth_pension as kis

# 공통 fetch 함수
_url_fetch = kis._url_fetch


##################################################
# ✅ [STEP 2] 잔고 조회
##################################################

# [1] 주식잔고조회 (요약 Object)
def get_inquire_balance_obj():
    url = "/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "TTTC8434R"
    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "00",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    res = _url_fetch(url, tr_id, "", params)
    body = res.getBody()

    # 요약 출력
    try:
        output2 = body["output2"][0]
        print("\n📊 [STEP 2] 주식잔고조회 (잔고현황)")
        print(f"💰 총 평가금액: {int(output2['tot_evlu_amt']):,}원")
        print(f"💵 총 입금금액: {int(output2['pchs_amt_smtl_amt']):,}원")
        print(f"📉 총 평가손익: {int(output2['evlu_pfls_smtl_amt']):,}원")
    except Exception as e:
        print("❗ 잔고 요약 정보가 없습니다.", e)

    return body


# [2] 주식잔고조회 (보유종목 리스트)
def get_inquire_balance_lst():
    body = get_inquire_balance_obj()
    try:
        output1 = body["output1"]
        if output1 and isinstance(output1, list):
            df = pd.DataFrame(output1)
            df = df[["pdno", "prdt_name", "hldg_qty", "pchs_avg_pric", "prpr", "evlu_amt", "evlu_pfls_amt"]]
            print("\n📊 [STEP 3] 주식잔고조회 (보유종목리스트)")
            print(df)
            return df
        else:
            print("❗ 보유 종목이 없습니다.")
            return pd.DataFrame()
    except Exception as e:
        print(f"❗ output1이 존재하지 않습니다. {e}")
        return pd.DataFrame()


##################################################
# ✅ [STEP 4] 체결 내역 조회 (최근 3개월)
##################################################

# [3] 주식일별주문체결 (페이징 지원)
def get_inquire_daily_ccld_lst(dv="01", inqr_strt_dt="", inqr_end_dt="", tr_cont="", FK100="", NK100="", dataframe=None):
    url = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'
    tr_id = "TTTC8001R" if dv == "01" else "CTSC9115R"

    if inqr_strt_dt == "":
        inqr_strt_dt = (datetime.today().replace(day=1)).strftime("%Y%m%d")
    if inqr_end_dt == "":
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "INQR_STRT_DT": inqr_strt_dt,
        "INQR_END_DT": inqr_end_dt,
        "SLL_BUY_DVSN_CD": "00",
        "INQR_DVSN": "01",
        "PDNO": "",
        "CCLD_DVSN": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": FK100,
        "CTX_AREA_NK100": NK100
    }

    print("\n📤 [요청 파라미터 확인]")
    for k, v in params.items():
        print(f"  {k}: {v}")

    res = _url_fetch(url, tr_id, tr_cont, params)
    header = res.getHeader()
    body = res.getBody()

    print("\n📥 [응답 헤더]")
    for k, v in header.items():
        print(f"  {k}: {v}")

    print("\n📥 [응답 바디]")

    output1 = body.get("output1", None)
    if not output1 or not isinstance(output1, list):
        print("❗ output1이 존재하지 않거나 리스트가 아님")
        return pd.DataFrame()

    df = pd.DataFrame(output1)
    df = df[["ord_dt", "prdt_name", "sll_buy_dvsn_cd_name", "ord_qty", "ord_unpr", "tot_ccld_amt"]]

    dataframe = pd.concat([dataframe, df], ignore_index=True) if dataframe is not None else df

    tr_cont = header.get("tr_cont", "")
    FK100 = body.get("ctx_area_fk100", "")
    NK100 = body.get("ctx_area_nk100", "")

    if tr_cont in ["F", "M"]:
        print("📥 다음 페이지 요청 중...")
        time.sleep(0.2)
        return get_inquire_daily_ccld_lst(dv, inqr_strt_dt, inqr_end_dt, "N", FK100, NK100, dataframe)

    return dataframe
