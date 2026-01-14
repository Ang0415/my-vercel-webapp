
import kis_auth_isa as ka
import kis_domstk_isa as kb
import json

# ✅ [STEP 1] 토큰 발급
print("🔐 [STEP 1] 토큰 발급 중...")
ka.auth(svr="prod", product="22")  # 연금계좌용: 상품코드 22
print("✅ 토큰 발급 완료")

# ✅ [STEP 2] 주식잔고조회 (요약)
print("\n📊 [STEP 2] 주식잔고조회 (잔고현황)")
summary = kb.get_inquire_balance_obj()
if "output2" in summary:
    acc_info = summary["output2"][0]
    print(f"💰 총 평가금액: {int(acc_info['tot_evlu_amt']):,}원")
    print(f"💵 총 입금금액: {int(acc_info['pchs_amt_smtl_amt']):,}원")
    print(f"📉 총 평가손익: {int(acc_info['evlu_pfls_smtl_amt']):,}원")
else:
    print("❗ 잔고 요약 정보를 불러오지 못했습니다.")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

# ✅ [STEP 3] 주식잔고조회 (보유종목리스트)
print("\n📊 [STEP 3] 주식잔고조회 (보유종목리스트)")
df = kb.get_inquire_balance_lst()
if not df.empty:
    display_cols = ["pdno", "prdt_name", "hldg_qty", "pchs_avg_pric", "prpr", "evlu_amt", "evlu_pfls_amt"]
    print(df[display_cols])
else:
    print("❗ 보유 종목이 없습니다.")

# ✅ [STEP 4] 최근 3개월 체결 내역 조회
print("📊 최근 3개월 체결 내역")
df = kb.get_inquire_daily_ccld_lst(dv="01")  # 날짜 생략하면 자동 설정
print(df)


