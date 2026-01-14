@echo off
chcp 65001
echo ==========================================
echo [1/3] 텔레그램 메시지 스캔 (금현물/매매일지)
echo ==========================================
python telegram_scan.py

echo.
echo ==========================================
echo [2/3] Daily Batch 실행 (잔고/보유량 업데이트)
echo ==========================================
python daily_batch.py

echo.
echo ==========================================
echo [3/3] Portfolio Performance 실행 (수익률/TWR 계산)
echo ==========================================
python "portfolio_performance_google sheet.py"

echo.
echo ==========================================
echo [완료] 모든 업데이트가 끝났습니다.
echo ==========================================
pause
