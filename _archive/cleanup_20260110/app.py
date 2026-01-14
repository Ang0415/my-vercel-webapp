import streamlit as st
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
from datetime import datetime
import matplotlib.dates as mdates

# 폰트 설정 (Windows 환경을 기준으로 'Malgun Gothic' 폰트 사용)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False # 그래프에서 '-' 부호 깨짐 방지

# Streamlit 앱 제목 설정
st.title("📈 코스피200 & S&P500 지수 시간가중수익률 (TWR)")
st.markdown("Yahoo Finance에서 과거 주가 데이터를 가져와 시간가중수익률(TWR)을 계산하고 그래프로 보여줍니다.")

# 1. 지수 티커 설정 (사용자가 선택할 수 있도록 Streamlit 위젯 사용)
st.subheader("지수 선택")
kospi_ticker = st.selectbox("코스피200 티커:", ["^KS200"], index=0) # 기본값으로 코스피200 선택
sp500_ticker = st.selectbox("S&P500 티커:", ["^GSPC"], index=0) # 기본값으로 S&P500 선택

# 2. 기간 설정 (사용자가 날짜를 선택할 수 있도록 Streamlit 위젯 사용)
st.subheader("기간 설정")
start_date = st.date_input("시작 날짜:", datetime(2024, 3, 28))
end_date = st.date_input("종료 날짜:", datetime.today())

# 3. 데이터 다운로드 함수 정의
@st.cache_data # Streamlit 캐싱 기능으로 데이터 다운로드 효율성 향상
def download_stock_data(ticker, start, end):
    print(f"📥 Yahoo Finance에서 {ticker} 데이터 다운로드 중...")
    data = yf.download(ticker, start=start, end=end)
    return data

# 데이터 다운로드
kospi_data = download_stock_data(kospi_ticker, start_date, end_date)
sp500_data = download_stock_data(sp500_ticker, start_date, end_date)

# 4. TWR 계산 함수 정의
def calculate_twr(df, ticker_name):
    if df.empty:
        st.warning(f"⚠️ {ticker_name} 데이터를 다운로드하지 못했습니다.")
        return pd.DataFrame()

    data = df[["Close"]].copy()
    data = data.reset_index()
    data["cashflow"] = 0
    data["value"] = data["Close"]
    data["date"] = pd.to_datetime(data["Date"])
    data = data[["date", "value", "cashflow"]].copy()

    twr = 1.0
    returns = []
    for i in range(1, len(data)):
        previous_value = data.loc[i - 1, "value"].item()
        current_cashflow = data.loc[i, "cashflow"].item()
        denominator = previous_value + current_cashflow
        if pd.isna(denominator) or denominator == 0:
            continue
        numerator = data.loc[i, "value"].item()
        r = numerator / denominator
        twr *= r
        returns.append({
            "date": data.loc[i, "date"].item(),
            "twr": (twr - 1) * 100
        })
    twr_df = pd.DataFrame(returns)
    return twr_df

# TWR 계산
kospi_twr_df = calculate_twr(kospi_data, "코스피200")
sp500_twr_df = calculate_twr(sp500_data, "S&P500")

# 5. 그래프 출력
st.subheader("📈 시간가중수익률 (TWR) 그래프")

# Matplotlib 그래프를 Streamlit에 표시
fig, ax = plt.subplots(figsize=(12, 6))

if not kospi_twr_df.empty:
    ax.plot(kospi_twr_df["date"], kospi_twr_df["twr"], linestyle=(0, (1, 1)), color='red', linewidth=2, label='코스피200 TWR')

if not sp500_twr_df.empty:
    ax.plot(sp500_twr_df["date"], sp500_twr_df["twr"], linestyle=(0, (1, 1)), color='blue', linewidth=2, label='S&P500 TWR')

# x축 눈금 간격 설정 (1개월)
month_interval = 1
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=month_interval))

# x축 눈금 포맷 설정 (년-월)
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

plt.title("코스피200 & S&P500 지수 시간가중수익률 (TWR)", fontsize=14)
plt.xlabel("날짜")
plt.ylabel("수익률 (%)")
plt.grid(True)
plt.legend()
plt.tight_layout()

st.pyplot(fig) # Streamlit으로 Matplotlib 그래프 표시

# 추가 정보 또는 메시지 표시
st.markdown("---")
st.info("💡 그래프를 통해 선택한 기간 동안의 코스피200과 S&P500 지수의 시간가중수익률 변화를 확인할 수 있습니다.")
st.info("⚠️ 데이터는 Yahoo Finance에서 제공받으며, 데이터의 정확성은 보장되지 않습니다.")