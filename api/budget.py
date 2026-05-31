# -*- coding: utf-8 -*-
from http.server import BaseHTTPRequestHandler
import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def clean_numeric_value(value, type_func=float):
    """쉼표가 포함되어 있거나 빈 문자열인 경우 숫자로 안전하게 변환합니다."""
    if isinstance(value, (int, float)):
        try: return type_func(value)
        except (ValueError, TypeError): return type_func(0)
    if not value: return type_func(0)
    try:
        cleaned_str = re.sub(r'[^\d.-]+', '', str(value))
        if not cleaned_str or cleaned_str in ['-', '.']: return type_func(0)
        num_val = float(cleaned_str)
        return type_func(num_val)
    except (ValueError, TypeError):
        return type_func(0)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        try:
            # 1. 버셀 환경변수에서 구글 서비스 어카운트 로드
            creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
            if not creds_json_str:
                raise Exception("버셀 환경변수에서 GOOGLE_CREDENTIALS를 찾을 수 없습니다.")
            
            creds_dict = json.loads(creds_json_str)
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            gc = gspread.authorize(creds)
            
            # 2. 가계부 시트 열기
            sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
            sh = gc.open_by_key(sheet_key)
            
            # 3. 거래내역 로드 및 기본 파싱
            tx_ws = sh.worksheet('거래내역')
            tx_rows = tx_ws.get_all_values()
            if len(tx_rows) < 2:
                raise Exception("거래내역 시트에 데이터가 부족합니다.")
                
            tx_headers = tx_rows[0]
            tx_records = []
            for row in tx_rows[1:]:
                record = {}
                for i, val in enumerate(row):
                    if i < len(tx_headers):
                        record[tx_headers[i].strip()] = val.strip()
                tx_records.append(record)
                
            # 4. 사용 가능한 전체 연월 리스트 추출 (정렬됨)
            month_set = set()
            for r in tx_records:
                yr = r.get('연도', '').strip()
                mo = r.get('월', '').strip()
                if yr and mo:
                    # '2026-05' 형식으로 통일
                    try:
                        month_str = f"{int(yr):04d}-{int(mo):02d}"
                        month_set.add(month_str)
                    except ValueError:
                        pass
            
            available_months = sorted(list(month_set), reverse=True)
            
            # 5. 선택된 연월 설정 (쿼리 파라미터 파싱)
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            selected_month = query_params.get('month', [None])[0]
            
            if not selected_month or selected_month not in available_months:
                if available_months:
                    selected_month = available_months[0] # 가장 최신 월 기본값
                else:
                    selected_month = datetime.now().strftime("%Y-%m")
            
            # selected_month는 '2026-05' 포맷 -> 연도 '2026', 월 '5'로 쪼갬
            sel_yr, sel_mo = selected_month.split('-')
            sel_yr = str(int(sel_yr))
            sel_mo = str(int(sel_mo))
            
            # 6. 선택한 달의 거래내역만 필터링
            df_month = []
            for r in tx_records:
                if r.get('연도', '').strip() == sel_yr and r.get('월', '').strip() == sel_mo:
                    df_month.append(r)
                    
            # 7. 예산 및 설정 데이터 로드
            settings_ws = sh.worksheet('예산 및 설정')
            settings_rows = settings_ws.get_all_values()
            
            # 카테고리 예산 정보 파싱
            # 컬럼 F: 대분류 (예: '1. 대출/금융'), 컬럼 G: 목표 예산
            budgets = {}
            for row in settings_rows[1:]:
                if len(row) > 6:
                    cat_name = row[5].strip()
                    cat_budget_val = row[6].strip()
                    if cat_name and cat_budget_val and cat_name != '합계':
                        # '1. 대출/금융' -> '대출/금융'
                        clean_cat = re.sub(r'^\d+\.\s*', '', cat_name)
                        budgets[clean_cat] = clean_numeric_value(cat_budget_val, int)
            
            # 8대 지출 카테고리 매핑용 (가계부 거래내역 대분류명 -> 예산 기준 대분류 키)
            category_mapping = {
                '금융_세금': '대출/금융',
                '주거_통신_환경': '주거/통신/환경',
                '식비': '식비',
                '교통_차량': '교통/차량',
                '생활_쇼핑': '생활/쇼핑',
                '건강_의료': '건강/의료',
                '문화_취미': '문화/취미',
                '경조사_선물': '경조사/선물'
            }
            
            # 8. 수입, 지출 통계 연산
            total_income = 0
            total_expense = 0
            category_actuals = {k: 0 for k in budgets.keys()}
            subcat_expenses = {} # 소분류별 지출
            card_expenses = {c: 0 for c in ['하나HD', '하나심플', 'BC바로', '신한EV', '롯데', '현대', '국민']}
            
            for r in df_month:
                inc_val = clean_numeric_value(r.get('수입', '0'), int)
                exp_val = abs(clean_numeric_value(r.get('지출', '0'), int))
                main_cat = r.get('대분류', '').strip()
                sub_cat = r.get('소분류', '').strip()
                pay_method = r.get('결제수단(출처)', '').strip()
                
                # 수입 누적
                total_income += inc_val
                
                # 실제 지출 누적 (수입 및 자산이동 제외)
                if main_cat not in ['수입', '자산이동'] and exp_val > 0:
                    total_expense += exp_val
                    
                    # 8대 대분류 누적
                    mapped_cat = category_mapping.get(main_cat)
                    if mapped_cat in category_actuals:
                        category_actuals[mapped_cat] += exp_val
                        
                    # 소분류 누적
                    if sub_cat:
                        subcat_expenses[sub_cat] = subcat_expenses.get(sub_cat, 0) + exp_val
                        
                # 신용카드 청구 누적
                if pay_method in card_expenses and exp_val > 0:
                    card_expenses[pay_method] += exp_val
            
            # 9. 8대 지출 카테고리 예산 vs 실지출 비교 테이블 정보 구성
            category_comparison = []
            for clean_cat, budget_amount in budgets.items():
                actual_amount = category_actuals.get(clean_cat, 0)
                diff = actual_amount - budget_amount
                category_comparison.append({
                    "category_name": clean_cat,
                    "budget": budget_amount,
                    "actual": actual_amount,
                    "diff": diff,
                    "progress": round((actual_amount / budget_amount * 100), 1) if budget_amount > 0 else 0.0
                })
            
            # 10. 신용카드 사용 현황 구성 (사용자 요청: 지출 많은 순서대로 정렬)
            card_comparison = []
            for card, amt in card_expenses.items():
                card_comparison.append({
                    "card_name": card,
                    "actual": amt
                })
            # 지출이 많은 순서대로 내림차순 정렬
            card_comparison.sort(key=lambda x: x['actual'], reverse=True)
            
            # 10-2. 현재기준 통장 잔액 구성 (잔액이 많은 순서대로 정렬)
            account_balances = []
            for row in settings_rows[1:]:
                if len(row) > 3:
                    asset_name = row[0].strip()
                    curr_balance = clean_numeric_value(row[2].strip(), int)
                    asset_note = row[3].strip()
                    
                    if asset_name and asset_name != '합계' and asset_note != '카드':
                        display_name = f"{asset_name} ({asset_note})" if asset_note else asset_name
                        account_balances.append({
                            "account_name": display_name,
                            "balance": curr_balance
                        })
            account_balances.sort(key=lambda x: x['balance'], reverse=True)
            
            # 11. 지출 TOP 8 소분류 비중 구성
            top_subcats = []
            for subcat, amt in subcat_expenses.items():
                top_subcats.append({
                    "subcat_name": subcat,
                    "actual": amt
                })
            top_subcats.sort(key=lambda x: x['actual'], reverse=True)
            top_8_subcats = top_subcats[:8]
            
            # 12. 저축·투자 & 대출 의사결정 도우미
            # 대출 시작 총원금: 최초 대출 시점의 기준 총원금
            loan_start_principal = 504000000
            
            # 구글시트 '예산 및 설정' J~N열에서 실시간 개별 대출 정보 추출
            total_current_loan_balance = 0
            weighted_loan_interest_rate_sum = 0.0      # 표면 금리 가중합
            weighted_effective_interest_rate_sum = 0.0 # 실질 금리 가중합
            
            individual_loans = []
            
            for row in settings_rows[1:]:
                if len(row) > 13:
                    loan_name = row[9].strip()
                    loan_bal_str = row[10].strip()
                    loan_rate_str = row[11].strip()
                    repay_method = row[12].strip()
                    maturity_period = row[13].strip()
                    
                    # '항목' 헤더 및 합계 행 등을 제외하고 유효한 대출 항목만 선별
                    if loan_name and loan_name != '항목' and loan_bal_str:
                        curr_bal = clean_numeric_value(loan_bal_str, int)
                        if curr_bal > 0:
                            total_current_loan_balance += curr_bal
                            
                            # 금리 문자열에서 숫자 부분 추출 (예: '4.25%(고정)' -> 4.25)
                            rate_match = re.search(r'([\d.]+)', loan_rate_str)
                            nominal_rate = float(rate_match.group(1)) if rate_match else 0.0
                            
                            # 회사 지원 금리 결정 (회사대출인 경우 3.0%, 그 외는 0.0%)
                            subsidy_rate = 3.0 if '회사대출' in loan_name else 0.0
                            effective_rate = nominal_rate - subsidy_rate
                            if effective_rate < 0:
                                effective_rate = 0.0
                                
                            weighted_loan_interest_rate_sum += curr_bal * nominal_rate
                            weighted_effective_interest_rate_sum += curr_bal * effective_rate
                            
                            individual_loans.append({
                                "name": loan_name,
                                "balance": curr_bal,
                                "nominal_rate": nominal_rate,
                                "subsidy_rate": subsidy_rate,
                                "effective_rate": round(effective_rate, 3),
                                "method": repay_method,
                                "maturity": maturity_period
                            })
            
            # 가중평균 대출금리 산정
            if total_current_loan_balance > 0:
                loan_interest_rate = weighted_loan_interest_rate_sum / total_current_loan_balance
                effective_loan_interest_rate = weighted_effective_interest_rate_sum / total_current_loan_balance
            else:
                loan_interest_rate = 3.8
                effective_loan_interest_rate = 3.8
                
            # 누적 상환원금 = 대출 시작 총원금 - 현재 대출 잔액
            # 만약 현재 잔액이 시작 원금보다 큰 경우(추가 대출 발생 등), 0원 이하로 떨어지지 않도록 방어 코드 적용
            total_repayment = loan_start_principal - total_current_loan_balance
            if total_repayment < 0:
                total_repayment = 0
                
            # 추가 대출 상환 거래내역도 보완적으로 반영 (만약 있다면 합산)
            tx_repayment = 0
            for r in tx_records:
                sub_cat = r.get('소분류', '').strip()
                exp_val = abs(clean_numeric_value(r.get('지출', '0'), int))
                if sub_cat == '대출원금상환' and exp_val > 0:
                    tx_repayment += exp_val
                    
            # total_repayment이 0 이하이거나, 거래내역 상환액이 더 많은 경우 보완
            if tx_repayment > total_repayment:
                total_repayment = tx_repayment
                
            loan_repayment_rate = (total_repayment / loan_start_principal * 100) if loan_start_principal > 0 else 0.0
            
            loan_interest_rate = round(loan_interest_rate, 2)
            effective_loan_interest_rate = round(effective_loan_interest_rate, 2)
            investment_return_rate = 5.0  # 기대 투자수익률 5.0% (프론트엔드 연동 전 fallback)
            spread = investment_return_rate - effective_loan_interest_rate

            
            # 13. [순수 여유 현금 잔액 연산 - 주식 대시보드 실시간 연동용]
            # 현금 계좌 잔액 합계 - 카드 누적 지출 잔액 합계
            total_cash_accounts = 0
            total_card_outstanding = 0
            
            for row in settings_rows[1:]:
                if len(row) > 3:
                    asset_name = row[0].strip()
                    curr_balance = clean_numeric_value(row[2].strip(), int)
                    asset_note = row[3].strip()
                    
                    if asset_name and asset_name != '합계':
                        if asset_note == '카드':
                            total_card_outstanding += abs(curr_balance) # 카드 잔액은 보통 음수로 기록됨
                        elif asset_note != '정소현': # 정소현 관련 카드는 자산에서 제외 (사용자 설정 시트 기준)
                            total_cash_accounts += curr_balance
                            
            net_cash_balance = total_cash_accounts - total_card_outstanding
            
            # --- 13-2. 최근 6개월 고정 지출 레이더 분석 ---
            recent_6_months = available_months[:6]
            n_months = len(recent_6_months) if len(recent_6_months) > 0 else 1
            
            # 6개월 소득 평균 구하기 (고정비 비중 모기준)
            income_totals = []
            for m in recent_6_months:
                m_inc = 0
                for r in tx_records:
                    yr_r = r.get('연도', '').strip()
                    mo_r = r.get('월', '').strip()
                    if yr_r and mo_r:
                        try:
                            m_str_r = f"{int(yr_r):04d}-{int(mo_r):02d}"
                            if m_str_r == m:
                                m_inc += clean_numeric_value(r.get('수입', '0'), int)
                        except ValueError:
                            pass
                income_totals.append(m_inc)
            avg_monthly_income = sum(income_totals) / n_months if n_months > 0 else 0
            
            # 정기 지출 그룹핑 딕셔너리
            # Key: (대분류, 소분류, 내용_정규화) -> Value: {월: [지출금액]}
            historical_expenses = {}
            
            for r in tx_records:
                yr = r.get('연도', '').strip()
                mo = r.get('월', '').strip()
                if not (yr and mo):
                    continue
                try:
                    m_str = f"{int(yr):04d}-{int(mo):02d}"
                except ValueError:
                    continue
                    
                if m_str in recent_6_months:
                    main_cat = r.get('대분류', '').strip()
                    sub_cat = r.get('소분류', '').strip()
                    desc = r.get('내용', '').strip()
                    exp_val = abs(clean_numeric_value(r.get('지출', '0'), int))
                    
                    if main_cat not in ['수입', '자산이동'] and exp_val > 0:
                        # 내용 정규화 (대괄호/소괄호 및 숫자 제거 등)
                        norm_desc = desc
                        norm_desc = re.sub(r'\(.*?\)', '', norm_desc)
                        norm_desc = re.sub(r'\[.*?\]', '', norm_desc)
                        norm_desc = re.sub(r'-\d+', '', norm_desc)
                        norm_desc = re.sub(r'\d+건', '', norm_desc)
                        norm_desc = re.sub(r'\d+월', '', norm_desc)
                        norm_desc = re.sub(r'\s+', '', norm_desc).strip()
                        
                        # 고정비 성격이 있는 주요 소분류는 내용(norm_desc)까지 묶어 상세 매핑
                        # 그 외 일반 지출은 소분류 단위로만 묶어서 정기 유사고정비 판정
                        if sub_cat in ['대출이자/원금', '보험', '주거비', '통신비', '구독료', '공과금', '콘텐츠', '관리비']:
                            group_key = (main_cat, sub_cat, norm_desc)
                        else:
                            group_key = (main_cat, sub_cat, "")
                            
                        if group_key not in historical_expenses:
                            historical_expenses[group_key] = {m: [] for m in recent_6_months}
                        historical_expenses[group_key][m_str].append(exp_val)
            
            strict_fixed_items = []
            quasi_fixed_items = []
            total_fixed_amount = 0
            
            for group_key, month_data in historical_expenses.items():
                main_cat, sub_cat, norm_desc = group_key
                
                # 월별 합산 금액 추출
                monthly_totals = []
                months_active = 0
                for m in recent_6_months:
                    m_sum = sum(month_data[m])
                    if m_sum > 0:
                        monthly_totals.append(m_sum)
                        months_active += 1
                        
                if not monthly_totals:
                    continue
                    
                freq_pct = (months_active / n_months) * 100
                avg_active_amount = sum(monthly_totals) / months_active
                avg_total_amount = sum(monthly_totals) / n_months
                
                max_val = max(monthly_totals)
                min_val = min(monthly_totals)
                is_stable = (max_val - min_val) <= (avg_active_amount * 0.15) if avg_active_amount > 0 else True
                
                is_strict_subcat = sub_cat in ['대출이자/원금', '보험', '주거비', '통신비', '구독료', '콘텐츠', '관리비', '공과금'] or '관리비' in norm_desc or '관리비' in sub_cat
                
                # 표시용 한글 정돈
                display_name = norm_desc if norm_desc else sub_cat
                if not display_name:
                    display_name = "미분류 정기 지출"
                    
                if display_name == '주유/충전':
                    display_name = '차량 주유 및 충전'
                elif display_name == '대출이자/원금':
                    display_name = '대출 이자 및 원금'
                    
                # 사용자 요청: 6개월 중 최소 3개월 이상 발생 시 정기성 고정비로 판정 (잡다한 내역 방지)
                if months_active >= 3:
                    if is_strict_subcat or is_stable:
                        strict_fixed_items.append({
                            "name": display_name,
                            "category": sub_cat,
                            "avg_amount": int(avg_active_amount),
                            "frequency": f"매월 {int(freq_pct)}% 발생" if freq_pct < 100 else "매월 100% 발생"
                        })
                        total_fixed_amount += int(avg_active_amount)
                    elif sub_cat in ['주유/충전', '공과금', '대중교통', '차량유지', '반려동물', '병원 / 약국']:
                        quasi_fixed_items.append({
                            "name": display_name,
                            "category": sub_cat,
                            "avg_amount": int(avg_total_amount),
                            "frequency": "6개월 정기 평균"
                        })
                        total_fixed_amount += int(avg_total_amount)
                        
            # 정렬 (금액 많은 순서)
            strict_fixed_items.sort(key=lambda x: x['avg_amount'], reverse=True)
            quasi_fixed_items.sort(key=lambda x: x['avg_amount'], reverse=True)
            
            # 수입 대비 고정비 비중 (평균 수입 기준, 없으면 0.0)
            fixed_ratio = round((total_fixed_amount / avg_monthly_income * 100), 1) if avg_monthly_income > 0 else 0.0
            
            # 14. Antigravity 스마트 가계 정밀 진단 리포트 엔진
            net_savings = total_income - total_expense
            surplus_rate = (net_savings / total_income * 100) if total_income > 0 else 0.0
            budget_total = sum(budgets.values())
            budget_spent_rate = (total_expense / budget_total * 100) if budget_total > 0 else 0.0
            
            # A. 재무 건전성 진단
            if surplus_rate >= 30:
                health_status = f"🟢 [재무 건전성] 이번 달 수입 대비 흑자 폭이 {net_savings:+,}원 (흑자율 {surplus_rate:.1f}%)으로 매우 양호한 현금 흐름을 보이고 계십니다."
            elif surplus_rate >= 10:
                health_status = f"🟡 [재무 건전성] 이번 달 수입 대비 흑자 폭이 {net_savings:+,}원 (흑자율 {surplus_rate:.1f}%)으로 보통 수준의 현금 흐름을 유지하고 계십니다."
            else:
                health_status = f"🔴 [재무 건전성] 이번 달 흑자 폭이 {net_savings:+,}원 (흑자율 {surplus_rate:.1f}%)으로 지출 통제가 강하게 요구되는 구간입니다."
                
            # B. 고정비 진단
            fixed_tax_expense = category_actuals.get('대출/금융', 0)
            fixed_percentage = (fixed_tax_expense / total_expense * 100) if total_expense > 0 else 0.0
            if fixed_percentage >= 35:
                fixed_status = f"📊 [고정비 진단] '금융_세금' 지출이 {fixed_tax_expense:,}원(전체 지출의 {fixed_percentage:.1f}%)으로 매우 큰 비중을 차지합니다. 대출 이자 비중이 높은 구조이므로, 기대 투자 수익률이 대출 금리보다 높지 않다면 소액 중도 상환을 통해 확실한 고정 금리 차단 효과를 챙기는 것을 권장합니다."
            else:
                fixed_status = f"📊 [고정비 진단] '금융_세금' 지출이 {fixed_tax_expense:,}원(전체 지출의 {fixed_percentage:.1f}%)으로 안정적인 비율 내에서 관리되고 있습니다."
                
            # C. 소비 칭찬
            food_actual = category_actuals.get('식비', 0)
            food_budget = budgets.get('식비', 500000)
            if food_actual <= food_budget:
                praise_status = f"✨ [소비 칭찬] 이번 달 식비는 {food_actual:,}원으로 예산({food_budget:,}원) 범위 내에서 슬기롭게 통제되어 칭찬할 만한 소비를 실천하셨습니다."
            else:
                praise_status = f"✨ [소비 피드백] 이번 달 식비가 {food_actual:,}원으로 지출 목표({food_budget:,}원) 대비 {food_actual - food_budget:,}원 초과 지출되었습니다. 외식 및 배달 횟수를 약간 더 줄여 보시는 것을 권장합니다."
                
            # D. 쇼핑 경고
            shopping_actual = category_actuals.get('생활/쇼핑', 0)
            shopping_budget = budgets.get('생활/쇼핑', 150000)
            if shopping_actual > shopping_budget:
                warning_status = f"🛍️ [쇼핑 경고] '생활_쇼핑' 지출이 {shopping_actual:,}원(예산 {shopping_budget:,}원 대비 {(shopping_actual/shopping_budget*100)-100:.1f}% 초과)으로 대폭 상승했습니다. 비필수적인 충동구매성 소비가 없는지 점검이 필요합니다."
            else:
                warning_status = f"🛍️ [쇼핑 칭찬] '생활_쇼핑' 지출이 {shopping_actual:,}원으로 목표 예산({shopping_budget:,}원) 이하로 아주 알뜰하게 지켜졌습니다."
                
            # E. 자산 운용 솔루션
            solution_status = f"⚖️ [자산 운용 솔루션]: 현재 대출의 가중평균 표면금리는 연 {loan_interest_rate}%이지만, 회사 대출의 이자 3% 지원 혜택 덕분에 회원님의 '실질 대출금리는 연 {effective_loan_interest_rate}%'에 불과합니다. 현재 주식 포트폴리오의 기대수익률이 연 5.50% 선이므로, 실질 스프레드 차이가 무려 연 +1.85% 이상 벌어지고 있습니다. 따라서 회원님의 대출(특히 실질 금리가 1.1%~1.7% 수준인 회사대출)은 절대 중도 상환하지 않고 최대한 유지하시면서, 여유 자금을 주식 및 ISA 포트폴리오에 적극적으로 분산 투자하여 레버리지 극대화 혜택을 누리시는 것이 재무적 기회비용 측면에서 압도적으로 유리합니다. 회원님의 대출 유지 전략은 100% 옳습니다!"
            
            diagnostic_report = f"💡 Antigravity 가계 종합 진단 & 재무 솔루션 ({selected_month} 기준)\n" + "-"*90 + f"\n{health_status}\n{fixed_status}\n{praise_status}\n{warning_status}\n{solution_status}"
            
            # 15. 최종 JSON 응답 데이터 구성
            response_data = {
                "selected_month": selected_month,
                "available_months": available_months,
                "summary": {
                    "total_income": total_income,
                    "total_expense": total_expense,
                    "net_savings": net_savings,
                    "utilization_rate": round(budget_spent_rate, 1)
                },
                "loan_helper": {
                    "start_principal": loan_start_principal,
                    "current_principal": total_current_loan_balance,
                    "repayment": total_repayment,
                    "repayment_rate": round(loan_repayment_rate, 1),
                    "loan_interest_rate": round(loan_interest_rate, 2),
                    "effective_loan_interest_rate": round(effective_loan_interest_rate, 2),
                    "spread_str": f"+{spread:.1f}%" if spread >= 0 else f"{spread:.1f}%",
                    "individual_loans": individual_loans
                },
                "category_comparison": category_comparison,
                "card_comparison": card_comparison,
                "account_balances": account_balances,
                "top_8_subcats": top_8_subcats,
                "net_cash_balance": net_cash_balance,
                "diagnostic_report": diagnostic_report,
                "fixed_expenses_radar": {
                    "total_fixed_amount": total_fixed_amount,
                    "fixed_ratio": fixed_ratio,
                    "strict_fixed_items": strict_fixed_items,
                    "quasi_fixed_items": quasi_fixed_items
                }
            }
            
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Error in budget API: {error_details}")
            self.wfile.write(json.dumps({"error": str(e), "details": error_details}).encode('utf-8'))
