# portfolio_performance.py (Refactored)
import pandas as pd
import gspread
import traceback
import time
import sys
from datetime import datetime
import portfolio_utils as utils
import portfolio_update as update_logic

# Windows Unicode Fix
sys.stdout.reconfigure(encoding='utf-8')

# --- 데이터 읽기 및 집계 ---
def read_and_aggregate_data(gc, sheet_names, date_col_idx, deposit_col_idx, withdrawal_col_idx, value_col_idx, start_date=None):
    if not gc: return None, None
    spreadsheet = utils.safe_execute_with_retry(gc.open, utils.GOOGLE_SHEET_NAME)
    all_data_list = []
    sheet_dfs = {}
    
    for sheet_name in sheet_names:
        try:
            time.sleep(1)
            worksheet = utils.safe_execute_with_retry(spreadsheet.worksheet, sheet_name)
            data = utils.safe_execute_with_retry(worksheet.get_all_values)
            if len(data) < 2: continue
            df_raw = pd.DataFrame(data[1:], columns=data[0])
            df_cleaned = pd.DataFrame({
                'Date': pd.to_datetime(df_raw.iloc[:, date_col_idx], errors='coerce'),
                'Deposit': utils.clean_numeric_column(df_raw.iloc[:, deposit_col_idx]),
                'Withdrawal': utils.clean_numeric_column(df_raw.iloc[:, withdrawal_col_idx]),
                'Value': utils.clean_numeric_column(df_raw.iloc[:, value_col_idx])
            }).dropna(subset=['Date']).drop_duplicates('Date').set_index('Date')
            
            if start_date:
                df_cleaned = df_cleaned[df_cleaned.index >= pd.to_datetime(start_date)]
            all_data_list.append(df_cleaned)
            sheet_dfs[sheet_name] = df_cleaned
        except Exception as e: print(f"⚠️ {sheet_name} 로딩 실패: {e}")

    if not all_data_list: return None, None
    combined = pd.concat(all_data_list).groupby(level=0).sum()
    combined['NetCashFlow'] = combined['Deposit'] - combined['Withdrawal']
    
    max_dates = [df[df['Value'] > 0].index.max() for df in sheet_dfs.values() if not df[df['Value'] > 0].empty]
    last_common_date = min(max_dates) if max_dates else combined.index.max()
    combined = combined[combined.index <= last_common_date]
    return combined[['Value', 'NetCashFlow']], last_common_date

# --- 성과 계산 로직 ---
def calculate_twr(df):
    if df is None or len(df) < 2: return None
    df = df.copy().sort_index()
    df['StartValue'] = df['Value'].shift(1)
    df = df.iloc[1:].copy()
    denom = df['StartValue'] + df['NetCashFlow']
    df['DailyFactor'] = 1.0
    valid = (df['StartValue'] > 0) & (denom.abs() > 1e-9)
    df.loc[valid, 'DailyFactor'] = df.loc[valid, 'Value'] / denom.loc[valid]
    df['TWR'] = (df['DailyFactor'].cumprod() - 1) * 100
    return df[['TWR']]

def load_and_process_dividends(gc):
    try:
        ws = gc.open(utils.GOOGLE_SHEET_NAME).worksheet(utils.DIVIDEND_SHEET_NAME)
        data = ws.get_all_values()
        if len(data) < 2: return None
        df = pd.DataFrame(data[1:], columns=data[0])
        df['Date'] = pd.to_datetime(df.iloc[:, utils.DIV_DATE_IDX], errors='coerce')
        df['Amount'] = utils.clean_numeric_column(df.iloc[:, utils.DIV_AMOUNT_IDX])
        df['Account'] = df.iloc[:, utils.DIV_ACCOUNT_IDX].str.strip()
        return df.dropna(subset=['Date', 'Account']).groupby(['Date', 'Account'])['Amount'].sum().reset_index()
    except: return None

def calculate_simple_profit(df, dividend_df, account_name):
    if df is None or df.empty: return None
    df = df.copy()
    
    # 1. 배당금 반영
    if dividend_df is not None and not dividend_df.empty:
        if 'Account' in dividend_df.columns:
            target_div = dividend_df[dividend_df['Account'] == account_name]
        else:
            target_div = pd.DataFrame()
            
        if not target_div.empty:
             target_div = target_div.set_index('Date')
             div_series = target_div['Amount']
             df = df.join(div_series.rename('Div'), how='left')
             df['Div'] = df['Div'].fillna(0)
             df['Value'] += df['Div']
    
    # 2. 누적 순입금(원금 변화) 계산
    if 'NetCashFlow' not in df.columns:
        df['NetCashFlow'] = 0
        
    df['CumNetFlow'] = df['NetCashFlow'].cumsum()
    
    # 3. 손익 계산
    if len(df) > 0:
        i_val = df['Value'].iloc[0]
        i_flow = df['NetCashFlow'].iloc[0]
        df['Profit'] = df['Value'] - (i_val + df['CumNetFlow'] - i_flow)
        return df['Profit']
    else:
        return None

# --- 결과 저장 로직 ---
def save_results_to_sheets(gc, twr_results, gain_loss_results, last_common_date):
    try:
        # 1. TWR 데이터 저장
        print(f"💾 TWR 데이터를 '{utils.TWR_RAW_SHEET}' 파일에 저장 중...")
        for acc_name, df in twr_results.items():
            time.sleep(2)
            if df is not None and not df.empty:
                save_df = df.copy()
                save_df['Account'] = acc_name
                if 'Date' not in save_df.columns: save_df = save_df.reset_index()
                
                if 'Date' in save_df.columns: save_df = save_df.rename(columns={'Date': '날짜', 'Account': '계좌명'})
                elif 'index' in save_df.columns: save_df = save_df.rename(columns={'index': '날짜', 'Account': '계좌명'})
                
                if 'TWR' in save_df.columns: save_df['TWR'] = save_df['TWR'].apply(lambda x: round(x, 2))

                required_cols = ['날짜', '계좌명', 'TWR']
                if all(col in save_df.columns for col in required_cols):
                    final_df = save_df[required_cols]
                    if utils.write_to_google_sheet(gc, acc_name, final_df, spreadsheet_name=utils.TWR_RAW_SHEET):
                        print(f"  - '{acc_name}' 시트 저장 완료.")
                    else:
                        print(f"  - ⚠️ '{acc_name}' 시트 저장 실패.")

        # 2. 단순손익 데이터 저장
        print(f"💾 단순 손익 데이터를 '{utils.GAIN_LOSS_RAW_SHEET}' 파일에 저장 중... (계좌별 시트)")
        for acc_name, series in gain_loss_results.items():
            if series is not None and not series.empty:
                temp_df = series.reset_index()
                temp_df.columns = ['날짜', '단순손익']
                temp_df['계좌명'] = acc_name
                
                temp_df['날짜'] = temp_df['날짜'].dt.strftime('%Y-%m-%d')
                temp_df['단순손익'] = temp_df['단순손익'].fillna(0).astype(int)
                
                save_df = temp_df[['날짜', '계좌명', '단순손익']]
                if utils.write_to_google_sheet(gc, acc_name, save_df, spreadsheet_name=utils.GAIN_LOSS_RAW_SHEET):
                    print(f"  - '{acc_name}' 시트 저장 완료.")
                else:
                    print(f"  - ⚠️ '{acc_name}' 시트 저장 실패.")

        # [일별 비중 데이터는 별도 함수로 마지막에 호출]
        return True

    except Exception as e:
        print(f"❌ 시트 저장 중 오류: {e}")
        traceback.print_exc()
        return False

def save_daily_asset_trend(gc, account_dfs):
    try:
        print(f"\n📤 데일리 자산 추이 업로드 중... (파일: '{utils.DAILY_ASSET_SPREADSHEET_NAME}')")
        
        # 1. Total 합산
        total_df = None
        if account_dfs:
            valid_dfs = [df for df in account_dfs.values() if df is not None and not df.empty]
            if valid_dfs:
                total_df = pd.concat(valid_dfs).groupby(level=0).sum()
        
        # 2. Total 업로드
        if total_df is not None and not total_df.empty:
            d_df = total_df[['Value']].copy().reset_index()
            if 'index' in d_df.columns: d_df = d_df.rename(columns={'index':'Date'})
            
            print(f"  - 'Total' 시트 업로드...")
            utils.write_to_google_sheet(gc, 'Total', d_df, spreadsheet_name=utils.DAILY_ASSET_SPREADSHEET_NAME)

        # 3. 개별 계좌 업로드
        for acc_k, acc_d in account_dfs.items():
             if acc_d is not None and not acc_d.empty:
                 d_df = acc_d[['Value']].copy().reset_index()
                 if 'index' in d_df.columns: d_df = d_df.rename(columns={'index':'Date'})
                 
                 print(f"  - '{acc_k}' 시트 업로드...")
                 utils.write_to_google_sheet(gc, acc_k, d_df, spreadsheet_name=utils.DAILY_ASSET_SPREADSHEET_NAME)
        return True
    
    except Exception as e:
        print(f"❌ 자산 추이 저장 실패: {e}")
        return False

# --- 메인 실행 함수 ---
def main():
    gc = utils.connect_google_sheets()
    if not gc: return False
    
    # 0. 휴장일 데이터 자동 삭제
    update_logic.remove_holiday_data(gc)

    # 1. 수량 데이터 업데이트 (매매일지 기반, 선행 작업)
    update_logic.update_daily_quantities(gc)

    # 2. 일별 평가액 계산 및 업데이트 (ISA, IRP, 연금, 금현물)
    update_logic.calculate_and_update_account_values(gc)

    # 3. 데이터 읽기 & 성과 계산
    isa_sheets = [utils.ACCOUNT_SHEETS['ISA']]
    irp_sheets = [utils.ACCOUNT_SHEETS['IRP']]
    pension_sheets = [utils.ACCOUNT_SHEETS['연금']]
    gold_sheets = [utils.ACCOUNT_SHEETS['금현물']] 
    
    dividend_df = load_and_process_dividends(gc)
    
    isa_df, _ = read_and_aggregate_data(gc, isa_sheets, utils.DATE_COL_IDX, utils.DEPOSIT_COL_IDX, utils.WITHDRAWAL_COL_IDX, utils.VALUE_COL_IDX)
    irp_df, _ = read_and_aggregate_data(gc, irp_sheets, utils.DATE_COL_IDX, utils.DEPOSIT_COL_IDX, utils.WITHDRAWAL_COL_IDX, utils.VALUE_COL_IDX)
    pension_df, _ = read_and_aggregate_data(gc, pension_sheets, utils.DATE_COL_IDX, utils.DEPOSIT_COL_IDX, utils.WITHDRAWAL_COL_IDX, utils.VALUE_COL_IDX)
    gold_df, _ = read_and_aggregate_data(gc, gold_sheets, utils.DATE_COL_IDX, utils.DEPOSIT_COL_IDX, utils.WITHDRAWAL_COL_IDX, utils.VALUE_COL_IDX)

    isa_twr = calculate_twr(isa_df) if isa_df is not None else None
    isa_gl = calculate_simple_profit(isa_df, dividend_df, 'ISA') if isa_df is not None else None
    
    irp_twr = calculate_twr(irp_df) if irp_df is not None else None
    irp_gl = calculate_simple_profit(irp_df, dividend_df, 'IRP') if irp_df is not None else None

    pension_twr = calculate_twr(pension_df) if pension_df is not None else None
    pension_gl = calculate_simple_profit(pension_df, dividend_df, '연금') if pension_df is not None else None
         
    gold_twr = calculate_twr(gold_df) if gold_df is not None else None
    gold_gl = calculate_simple_profit(gold_df, dividend_df, '금현물') if gold_df is not None else None

    # 4. 데일리 자산 추이 저장
    account_dfs = {
        'ISA': isa_df,
        'IRP': irp_df,
        '연금': pension_df,
        '금현물': gold_df
    }
    save_daily_asset_trend(gc, account_dfs)
    
    # 5. 전체 합산 TWR 및 손익 계산
    total_twr = None; total_gl = None
    if account_dfs:
        valid_dfs = [df for df in account_dfs.values() if df is not None and not df.empty]
        if valid_dfs:
            total_df = pd.concat(valid_dfs).groupby(level=0).sum()
            if not total_df.empty:
                total_twr = calculate_twr(total_df)
                total_gl = calculate_simple_profit(total_df, dividend_df, 'Total') 
                
    # 6. 결과 저장
    twr_results = {
        'Total': total_twr,
        'ISA': isa_twr,
        'IRP': irp_twr,
        '연금': pension_twr,
        '금현물': gold_twr
    }
    gain_loss_results = {
        'Total': total_gl,
        'ISA': isa_gl,
        'IRP': irp_gl,
        '연금': pension_gl,
        '금현물': gold_gl
    }
    
    last_calc_date = datetime.now() 
    all_indices = []
    for df in [isa_twr, irp_twr, pension_twr, gold_twr]:
        if df is not None and not df.empty:
            all_indices.append(df.index.max())
    if all_indices: last_calc_date = max(all_indices)

    save_results_to_sheets(gc, twr_results, gain_loss_results, last_calc_date)
    
    # 7. 일별 비중 기록
    update_logic.record_daily_weights(gc)
    
    # 8. 휴일 데이터 검사
    update_logic.check_holiday_data(gc)
    
    print("\n🎉 모든 작업 완료.")
    return True

if __name__ == '__main__':
    start_time = time.time()
    try:
        if main(): 
            print(f"✅ 성공 ({time.time()-start_time:.1f}s)")
        else: print(f"⚠️ 실패")
    except Exception as e: print(f"🔥 오류:\n{traceback.format_exc()[:500]}")
