# main_irp.py

import kis_auth_irp as ka
import kis_domstk_irp as kb
import pandas as pd

# ✅ 토큰 발급
ka.auth(svr="prod", product="29")  # IRP 계좌 상품코드 29

# ✅ STEP 1: IRP 단순 잔고 조회 (기존 코드 유지)
# TR ID: TTTC2208R (퇴직연금 잔고조회)
print("\n📊 [STEP 1] IRP 단순 잔고 조회")
df_bal_simple = kb.get_inquire_irp_balance_lst() # 변수 이름 변경 (df_bal -> df_bal_simple)
if not df_bal_simple.empty:
    # 단순 잔고 조회 결과에서 주요 정보만 간략히 출력 (예시)
    print("--- 단순 잔고 요약 ---")
    if "ovrs_pdno" in df_bal_simple.columns: # 컬럼 존재 여부 확인
        # 예시: 상품명, 평가금액, 수익률 등 필요한 컬럼만 선택
        display_cols_simple = ["prdt_name", "evlu_amt", "evlu_pfls_rt"]
        available_cols_simple = [col for col in display_cols_simple if col in df_bal_simple.columns]
        if available_cols_simple:
             print(df_bal_simple[available_cols_simple])
        else:
             print(df_bal_simple) # 필요한 컬럼 없으면 전체 출력
    else: # 해외상품이 없는 경우 (예: 컬럼명이 다를 수 있음)
        # 국내 상품 위주 컬럼 예시
        display_cols_simple = ["prdt_name", "evlu_amt", "evlu_pfls_rt", "hldg_qty"]
        available_cols_simple = [col for col in display_cols_simple if col in df_bal_simple.columns]
        if available_cols_simple:
             print(df_bal_simple[available_cols_simple])
        else:
             print(df_bal_simple) # 전체 출력
else:
    print("❗ IRP 단순 잔고 데이터가 없습니다.")


# ✅ STEP 2: IRP 체결기준 잔고 조회 (수정된 부분)
# TR ID: TTTC2202R (퇴직연금 체결기준잔고)
print("\n📊 [STEP 2] IRP 체결기준 잔고 조회 (현재 보유 현황)")
# kis_domstk_irp 모듈의 get_inquire_present_balance_irp 함수 호출
df_present_balance = kb.get_inquire_present_balance_irp()

if df_present_balance is not None and not df_present_balance.empty:
    print("--- 현재 보유 종목 상세 ---")
    # 체결기준잔고 조회 결과에서 주요 정보 출력 (API 문서 참고)
    display_cols_present = [
        "prdt_name",       # 상품명
        "hldg_qty",        # 보유수량
        "pchs_avg_pric",   # 매입평균가격
        "prpr",            # 현재가
        "evlu_amt",        # 평가금액
        "evlu_pfls_amt",   # 평가손익금액
        "evlu_pfls_rt",    # 평가손익율
        "pchs_amt",        # 매입금액
        "cblc_weit"        # 잔고비중
    ]
    # 실제 응답에 존재하는 컬럼만 선택하여 오류 방지
    available_cols_present = [col for col in display_cols_present if col in df_present_balance.columns]
    if available_cols_present:
        # 숫자 컬럼 포맷팅 (예: 소수점, 쉼표 등) - 필요시 주석 해제 및 수정
        # pd.options.display.float_format = '{:,.2f}'.format # 예시: 소수점 2자리
        # df_present_balance['hldg_qty'] = pd.to_numeric(df_present_balance['hldg_qty'], errors='coerce')
        # ... 다른 숫자 컬럼들도 필요시 변환 ...
        print(df_present_balance[available_cols_present].to_string(index=False)) # to_string으로 더 깔끔하게 출력
    else:
        print("❗ 조회된 체결기준잔고에 필요한 컬럼이 없습니다.")
        print(df_present_balance) # 전체 데이터프레임 출력
else:
    print("❗ IRP 체결기준 잔고 데이터가 없습니다.")

# --- (선택사항) 체결기준잔고 요약 정보(output2) 출력 ---
# get_inquire_present_balance_irp 함수가 output2도 반환하도록 수정했다면 아래 코드 사용 가능
# 현재 kis_domstk_irp.py의 해당 함수는 output1(DataFrame)만 반환하므로 아래는 주석 처리
# summary_present = kb.get_inquire_present_balance_summary() # 별도 함수 또는 기존 함수 수정 필요
# if summary_present:
#     print("\n--- 체결기준잔고 요약 ---")
#     print(f"💰 총 평가금액: {summary_present.get('evlu_amt_smtl_amt', 'N/A')}")
#     print(f"💵 총 매입금액: {summary_present.get('pchs_amt_smtl_amt', 'N/A')}")
#     print(f"📈 총 평가손익: {summary_present.get('evlu_pfls_smtl_amt', 'N/A')}")
#     print(f"📊 수익률: {summary_present.get('pftrt', 'N/A')}%")