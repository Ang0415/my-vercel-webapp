
import os
import time
import smtplib
import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import make_msgid
import re
import traceback
from datetime import datetime
import sys
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io

# Windows 콘솔 인코딩 설정
sys.stdout.reconfigure(encoding='utf-8')

# --- 설정 ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get("EMAIL_ADDRESS")
SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD") # 앱 비밀번호
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

# 구글 시트 설정
JSON_KEYFILE = 'stock-auto-writer-44eaa06c140c.json'
GOOGLE_SHEET_NAME = 'KYI_자산배분'
DAILY_ASSET_SPREADSHEET_NAME = '성과_자산추이_Raw'
TWR_RAW_SHEET = '성과_TWR_Raw'
WEIGHTS_FILE_NAME = '일별비중_RAW'
WEIGHTS_SHEET_NAME = '일별비중_Raw'
SETTINGS_SHEET = '⚙️설정'

# --- 폰트 설정 ---
def configure_fonts():
    import platform
    os_name = platform.system()
    if os_name == 'Windows':
        font_family = 'Malgun Gothic'
    elif os_name == 'Darwin':
        font_family = 'AppleGothic'
    else:
        # Linux (GitHub Actions)
        # 나눔폰트가 설치되어 있다고 가정 (workflow에서 설치함)
        font_family = 'NanumGothic'
    
    plt.rc('font', family=font_family)
    plt.rc('axes', unicode_minus=False)

configure_fonts()

# --- 유틸리티 ---
def connect_google_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if os.path.exists(JSON_KEYFILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE, scope)
            gc = gspread.authorize(creds)
            return gc
        else:
            print(f"❌ '{JSON_KEYFILE}' 파일 없음")
            return None
    except Exception as e:
        print(f"❌ 구글 시트 연결 실패: {e}")
        return None

def safe_api_call(func, *args, **kwargs):
    for i in range(5):
        try: return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) or "Quota exceeded" in str(e): time.sleep(2 * (i + 1))
            else: raise e
    return None

def clean_numeric_value(value, type_func=int):
    if isinstance(value, (int, float)): return type_func(value)
    if not value: return type_func(0)
    try:
        cleaned = re.sub(r'[^\d.-]+', '', str(value))
        if not cleaned or cleaned in ['-', '.']: return type_func(0)
        return type_func(float(cleaned))
    except: return type_func(0)

# --- 데이터 로딩 ---

def load_total_asset(gc):
    try:
        sh = safe_api_call(gc.open, DAILY_ASSET_SPREADSHEET_NAME)
        ws = safe_api_call(sh.worksheet, 'Total')
        data = safe_api_call(ws.get_all_records)
        df = pd.DataFrame(data)
        if df.empty or 'Value' not in df.columns: return 0
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        latest = df.sort_values('Date').iloc[-1]
        return clean_numeric_value(latest['Value'], int)
    except: return 0

def calculate_total_principal(gc):
    ACCOUNT_SHEET_MAP = {'ISA': '📈ISA 수익률', 'IRP': '📈IRP 수익률', '연금': '📈연금 수익률', '금현물': '📈금현물 수익률'}
    total = 0
    try:
        sh = safe_api_call(gc.open, GOOGLE_SHEET_NAME)
        for acc, name in ACCOUNT_SHEET_MAP.items():
            try:
                ws = safe_api_call(sh.worksheet, name)
                vals = safe_api_call(ws.col_values, 2)
                for v in vals[1:]: total += clean_numeric_value(v, float)
            except: continue
    except: pass
    return int(total)

def load_latest_twr(gc):
    try:
        sh = safe_api_call(gc.open, TWR_RAW_SHEET)
        ws = safe_api_call(sh.worksheet, 'Total')
        data = safe_api_call(ws.get_all_records)
        df = pd.DataFrame(data)
        if df.empty or 'TWR' not in df.columns: return "N/A"
        date_col = 'Date' if 'Date' in df.columns else '날짜'
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        latest = df.sort_values(date_col).iloc[-1]
        return f"{float(latest['TWR']):.2f}%"
    except: return "N/A"

def load_historical_twr(gc):
    """TWR 전체 히스토리 로드"""
    try:
        sh = safe_api_call(gc.open, TWR_RAW_SHEET)
        ws = safe_api_call(sh.worksheet, 'Total')
        data = safe_api_call(ws.get_all_records)
        df = pd.DataFrame(data)
        if df.empty: return pd.DataFrame()
        
        date_col = 'Date' if 'Date' in df.columns else '날짜'
        df = df.rename(columns={date_col: 'Date'})
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df['TWR'] = pd.to_numeric(df['TWR'], errors='coerce')
        return df.sort_values('Date')
    except Exception as e:
        print(f"⚠️ TWR 히스토리 로딩 실패: {e}")
        return pd.DataFrame()

def load_historical_assets(gc):
    """자산 전체 히스토리 로드"""
    try:
        sh = safe_api_call(gc.open, DAILY_ASSET_SPREADSHEET_NAME)
        ws = safe_api_call(sh.worksheet, 'Total')
        data = safe_api_call(ws.get_all_records)
        df = pd.DataFrame(data)
        if df.empty: return pd.DataFrame()
        
        df = df.rename(columns={'Date': 'Date', 'Value': 'Value'}) # 이미 영어일 가능성 높음
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df['Value'] = pd.to_numeric(df['Value'], errors='coerce')
        return df.sort_values('Date')
    except Exception as e:
        print(f"⚠️ 자산 히스토리 로딩 실패: {e}")
        return pd.DataFrame()

# --- 차트 생성 ---

def create_twr_chart(df):
    """TWR 추이 라인 차트 생성"""
    if df.empty: return None
    
    plt.figure(figsize=(10, 5))
    plt.plot(df['Date'], df['TWR'], color='#2e86c1', linewidth=2)
    plt.title('시간가중수익률(TWR) 추이', fontsize=15, pad=20)
    plt.grid(True, linestyle='--', alpha=0.7)
    
    # X축 포맷
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    plt.gca().xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.xticks(rotation=45)
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf

def create_asset_chart(df):
    """월별 자산 추이 바 차트 (매월 말일 기준)"""
    if df.empty: return None
    
    # 월별 마지막 날 데이터 추출
    df['YearMonth'] = df['Date'].dt.to_period('M')
    monthly_df = df.groupby('YearMonth').apply(lambda x: x.iloc[-1]).reset_index(drop=True)
    
    # 최근 12개월만 표시 (너무 많으면 복잡)
    if len(monthly_df) > 13:
        monthly_df = monthly_df.iloc[-13:]
        
    plt.figure(figsize=(10, 5))
    
    # x축 레이블 생성
    x_labels = monthly_df['Date'].dt.strftime('%Y-%m')
    x_pos = range(len(x_labels))
    
    bars = plt.bar(x_pos, monthly_df['Value'], color='#27ae60', alpha=0.8)
    
    plt.title('월별 총 자산 추이 (최근 1년)', fontsize=15, pad=20)
    plt.xticks(x_pos, x_labels, rotation=45)
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    
    # 막대 위에 값 표시 (단위: 억/천만)
    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height,
                 f'{int(height/10000):,}만',
                 ha='center', va='bottom', fontsize=9)
                 
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf

# --- 리포트 생성 ---

def generate_html_report(total_asset, principal, twr, profit, profit_rate, date_str, twr_cid, asset_cid):
    
    total_asset_str = f"{total_asset:,.0f} 원"
    principal_str = f"{principal:,.0f} 원"
    profit_str = f"{profit:,.0f} 원"
    profit_color = "red" if profit >= 0 else "blue"
    profit_rate_str = f"{profit_rate:+.2f}%"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; color: #333; }}
            .container {{ max_width: 800px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
            .title {{ color: #2e86c1; margin: 0; }}
            .date {{ color: #666; font-size: 0.9em; }}
            .summary-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; margin-bottom: 40px; }}
            .card {{ background: white; padding: 15px; border: 1px solid #eee; border-radius: 8px; text-align: center; }}
            .card-title {{ font-size: 0.9em; color: #666; margin-bottom: 5px; }}
            .card-value {{ font-size: 1.2em; font-weight: bold; }}
            
            .section-title {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 40px; margin-bottom: 20px; }}
            .chart-box {{ text-align: center; margin-bottom: 30px; border: 1px solid #f1f1f1; padding: 10px; border-radius: 8px; }}
            .chart-img {{ max-width: 100%; height: auto; }}
            
            .footer {{ margin-top: 50px; font-size: 0.8em; color: #999; text-align: center; border-top: 1px solid #eee; padding-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2 class="title">📊 주간 투자 자산 리포트</h2>
                <p class="date">기준일: {date_str}</p>
            </div>
            
            <div class="summary-grid">
                <div class="card">
                    <div class="card-title">💰 총 평가액</div>
                    <div class="card-value">{total_asset_str}</div>
                </div>
                <div class="card">
                    <div class="card-title">💼 투자 원금 (추정)</div>
                    <div class="card-value">{principal_str}</div>
                </div>
                <div class="card">
                    <div class="card-title">💸 단순 손익</div>
                    <div class="card-value" style="color: {profit_color}">
                        {profit_str} ({profit_rate_str})
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">📈 시간가중 수익률 (TWR)</div>
                    <div class="card-value">{twr}</div>
                </div>
            </div>
            
            <h3 class="section-title">📈 성과 분석</h3>
            
            <div class="chart-box">
                <h4>📊 시간가중수익률(TWR) 추이</h4>
                <img src="cid:{twr_cid}" class="chart-img" alt="TWR Chart">
            </div>
            
            <div class="chart-box">
                <h4>📅 월별 총 자산 추이</h4>
                <img src="cid:{asset_cid}" class="chart-img" alt="Asset Chart">
            </div>
            
            <div class="footer">
                <p>본 메일은 자동 발송된 리포트입니다.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

def send_email(html_body, twr_img_buf, asset_img_buf):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        return

    msg = MIMEMultipart('related')
    msg['Subject'] = f'📊 [주간 리포트] 투자 자산 성과 ({datetime.now().strftime("%Y-%m-%d")})'
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    
    # 1. HTML 본문 (Alternative)
    msg_alt = MIMEMultipart('alternative')
    msg.attach(msg_alt)
    
    msg_alt.attach(MIMEText(html_body, 'html'))
    
    # 2. 이미지 첨부
    # TWR Chart
    if twr_img_buf:
        twr_part = MIMEImage(twr_img_buf.getvalue())
        twr_part.add_header('Content-ID', '<twr_chart>')
        twr_part.add_header('Content-Disposition', 'inline')
        msg.attach(twr_part)
        
    # Asset Chart
    if asset_img_buf:
        asset_part = MIMEImage(asset_img_buf.getvalue())
        asset_part.add_header('Content-ID', '<asset_chart>')
        asset_part.add_header('Content-Disposition', 'inline')
        msg.attach(asset_part)
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            # server.send_message(msg) # TEST MODE
            # print("📨 [TEST MODE] 메일 발송 skip")
            server.send_message(msg) # 실제 발송
            
        print(f"✅ 이메일 처리 완료 ({RECIPIENT_EMAIL})")
    except Exception as e:
        print(f"❌ 이메일 발송 오류: {e}")

def main():
    print("🚀 주간 리포트(차트 버전) 생성 시작...")
    
    gc = connect_google_sheets()
    if not gc: return
    
    # 1. 데이터 로드
    print("1️⃣ 데이터 로딩 중...")
    total_asset = load_total_asset(gc)
    principal = calculate_total_principal(gc)
    twr = load_latest_twr(gc)
    
    twr_hist = load_historical_twr(gc)
    asset_hist = load_historical_assets(gc)
    
    date_str = datetime.now().strftime('%Y-%m-%d')
    profit = total_asset - principal
    profit_rate = (profit / principal * 100) if principal > 0 else 0
    
    print(f"   - 총 자산: {total_asset:,.0f}")
    
    # 2. 차트 생성
    print("2️⃣ 차트 생성 중...")
    twr_buf = create_twr_chart(twr_hist)
    asset_buf = create_asset_chart(asset_hist)
    
    # 3. HTML 생성
    # CID는 꺽쇠 제외한 이름으로 지정 (generate_html 내부에서 사용), 첨부 시에는 꺽쇠 포함
    twr_cid = "twr_chart"
    asset_cid = "asset_chart"
    
    html = generate_html_report(total_asset, principal, twr, profit, profit_rate, date_str, twr_cid, asset_cid)
    
    # 4. 이메일 전송
    print("3️⃣ 이메일 전송 중...")
    send_email(html, twr_buf, asset_buf)
    
    print("🏁 작업 완료")

if __name__ == "__main__":
    main()
