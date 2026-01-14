@echo OFF
REM 이 스크립트는 twr_results.csv와 gain_loss.json 파일의 변경사항을
REM 확인하고 GitHub에 자동으로 커밋 및 푸시합니다.
REM 실행 전에 portfolio_performance.py가 먼저 실행되어야 합니다.

REM 스크립트가 있는 폴더(KOR_invest)로 이동
cd /d "%~dp0"
echo Changed directory to: %CD%

echo.
echo Starting Git operations for result files...

REM 결과 파일이 존재하는지 기본적인 확인
if not exist "twr_results.csv" (
    echo ERROR: twr_results.csv not found! Ensure portfolio_performance.py ran first.
    pause
    goto :END_SCRIPT
)
if not exist "gain_loss.json" (
    echo ERROR: gain_loss.json not found! Ensure portfolio_performance.py ran first.
    pause
    goto :END_SCRIPT
)
echo Result files exist.

echo Staging result files...
git add twr_results.csv gain_loss.json
echo Files staged.

echo Checking for local changes in result files...
REM 스테이징된 파일에 변경사항이 있는지 조용히 확인
git diff --cached --quiet -- twr_results.csv gain_loss.json
set GIT_DIFF_EXIT_CODE=%errorlevel%
echo Git diff exit code: %GIT_DIFF_EXIT_CODE% (0 = no changes, 1 = changes)

REM 변경사항이 감지되었는지 확인 (errorlevel 1 = 변경됨)
if %GIT_DIFF_EXIT_CODE% equ 1 goto :COMMIT_CHANGES

REM 변경사항 없음
echo No local changes detected in result files. Skipping commit and push.
goto :END_SCRIPT


:COMMIT_CHANGES
echo Local changes detected. Proceeding with commit and push...

echo Pulling latest changes from remote repository (master branch) using rebase...
REM 커밋/푸시 전 항상 원격 저장소와 동기화 (충돌 방지)
git pull origin master --rebase
set PULL_EXIT_CODE=%errorlevel%

REM Pull/Rebase 실패 시 (예: 충돌) 스크립트 중지
if %PULL_EXIT_CODE% neq 0 (
    echo ERROR: Git pull --rebase failed (Exit Code: %PULL_EXIT_CODE%). Manual intervention might be needed.
    echo Please run 'git status' and resolve issues manually before running this script again.
    pause
    goto :END_SCRIPT
)
echo Git pull successful or no remote changes to pull.

echo Committing local changes...
REM 커밋 메시지에 사용할 현재 시간 (YYYYMMDD_HHMMSS 형식)
set CURRENT_DATETIME=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set COMMIT_MSG=Automated update: performance results %CURRENT_DATETIME%
echo Commit message: "%COMMIT_MSG%"
git commit -m "%COMMIT_MSG%"
set COMMIT_EXIT_CODE=%errorlevel%

REM 커밋 성공 여부 확인
if %COMMIT_EXIT_CODE% neq 0 (
    echo ERROR: Git commit failed (Exit Code: %COMMIT_EXIT_CODE%). Check 'git status'.
    git status
    pause
    goto :END_SCRIPT
)
echo Commit successful.

echo Pushing changes to master branch...
git push origin master
set PUSH_EXIT_CODE=%errorlevel%

REM 푸시 성공 여부 확인
if %PUSH_EXIT_CODE% neq 0 (
    echo ERROR: Git push failed (Exit Code: %PUSH_EXIT_CODE%). Check credentials/connection.
    pause
    goto :END_SCRIPT
)
echo Git push successful.
goto :END_SCRIPT


:END_SCRIPT
echo.
echo Git sync script finished.
REM 작업 스케줄러에서 portfolio_performance.py 실행 *후에* 이 스크립트를 실행할 때는 아래 pause를 제거하세요.
pause
