import subprocess
import os
import sys
from datetime import datetime

# --- 설정 ---
# Git 저장소 경로 (스크립트가 있는 폴더로 가정)
repo_path = os.path.dirname(os.path.abspath(__file__))
# 커밋/푸시 대상 파일 목록
files_to_add = ["twr_results.csv", "gain_loss.json"]
# 원격 저장소 이름 및 브랜치
remote_name = "origin"
branch_name = "master"
# --- ---

def run_git_command(command_list):
    """Git 명령어를 실행하고 결과를 반환하는 함수"""
    try:
        # 실행할 명령어 화면에 표시 (사용자 확인용)
        print(f"Executing: {' '.join(command_list)}")
        # stderr=subprocess.PIPE 추가하여 오류 메시지 캡처
        # encoding='utf-8' 추가하여 출력 결과가 깨지지 않도록 시도
        result = subprocess.run(command_list, cwd=repo_path, check=False, capture_output=True, text=True, encoding='utf-8', errors='replace')
        # 명령어 실행 후 Return Code 출력
        print(f"  Return Code: {result.returncode}")
        # 표준 출력(stdout)이 있으면 출력
        if result.stdout:
            print(f"  Stdout: {result.stdout.strip()}")
        # 표준 오류(stderr)가 있으면 출력 (오류 식별에 중요)
        if result.stderr:
            # 오류 메시지는 항상 출력 (오류 스트림으로)
            print(f"  Stderr: {result.stderr.strip()}", file=sys.stderr)
        return result
    except FileNotFoundError:
        # Git 명령어를 찾을 수 없는 경우 (Git 설치 또는 PATH 문제)
        print(f"ERROR: 'git' command not found. Is Git installed and in PATH?", file=sys.stderr)
        return None
    except Exception as e:
        # 기타 명령어 실행 중 예외 발생 시
        print(f"ERROR: Failed to run command {' '.join(command_list)} - {e}", file=sys.stderr)
        return None

def main():
    print("--- Starting Git Sync Script ---")

    # 1. 결과 파일 스테이징
    print("\nStep 1: Staging result files...")
    add_command = ["git", "add"] + files_to_add
    add_result = run_git_command(add_command)
    # git add 명령어는 보통 성공 시 returncode 0 반환
    if add_result is None or add_result.returncode != 0:
        print("ERROR: Failed to stage files.", file=sys.stderr)
        return # 오류 발생 시 스크립트 중단

    # 2. 변경 사항 확인
    print("\nStep 2: Checking for local changes...")
    # --quiet 옵션 제거하고 diff 결과를 직접 보거나 returncode만 사용
    diff_command = ["git", "diff", "--cached", "--quiet"] + files_to_add
    diff_result = run_git_command(diff_command)

    # diff --quiet는 변경 없으면 0, 변경 있으면 1, 오류 시 다른 값 반환
    if diff_result is None:
        print("ERROR: Failed to check for changes.", file=sys.stderr)
        return
    elif diff_result.returncode == 0:
        print("INFO: No local changes detected in result files. Skipping commit and push.")
        print("\n--- Git Sync Script Finished (No Changes) ---")
        return # 변경 없으면 스크립트 정상 종료
    elif diff_result.returncode == 1:
        print("INFO: Local changes detected. Proceeding...")
        # 변경사항 있을 때만 계속 진행
    else:
        # diff 명령어 자체가 실패한 경우
        print(f"ERROR: 'git diff' command failed with return code {diff_result.returncode}.", file=sys.stderr)
        return

    # --- ▼▼▼ 추가된 진단 부분 ▼▼▼ ---
    # 2.5: Deep Index/Status Check (Pull 전에 상태 상세 확인)
    print("\nStep 2.5: Deep status check before pull...")
    # Git이 현재 스테이징된 것으로 인식하는 것은? (--short는 간결한 출력)
    run_git_command(["git", "status", "--short"])
    # 스테이징 영역과 마지막 커밋(HEAD)의 차이점은? (방금 add한 파일만 나와야 함)
    run_git_command(["git", "diff", "--cached", "--name-status"])
    # 작업 폴더와 스테이징 영역의 차이점은? (추적되는 파일은 없어야 정상)
    run_git_command(["git", "diff", "--name-status"])
    # 혹시 아직 병합 진행 중 상태인가? MERGE_HEAD 파일 확인
    merge_head_path = os.path.join(repo_path, ".git", "MERGE_HEAD")
    if os.path.exists(merge_head_path):
        print(f"WARNING: {merge_head_path} file exists! Merge might still be in progress internally.")
    print("--- Finished deep status check ---")
    # --- ▲▲▲ 추가된 진단 부분
    # 
    # 
    # 
    # 
    #  ▲▲▲ ---

    # 3. 원격 저장소 변경 사항 가져오기 (Pull --rebase)
    print("\nStep 3: Pulling latest changes (Standard Merge)...")
    pull_command = ["git", "pull", remote_name, branch_name, "--no-edit"]
    pull_result = run_git_command(pull_command)
    # Pull 성공 여부 확인 (returncode 0 이어야 성공)
    if pull_result is None or pull_result.returncode != 0:
        print("ERROR: Git pull failed. Manual intervention might be needed.", file=sys.stderr)
        # Pull 실패 시 Stderr 내용을 이미 run_git_command 함수에서 출력했으므로 여기서는 추가 출력 생략 가능0
        0
        0
        0
        

        
        return

    # 4. 커밋
    print("\nStep 4: Committing local changes...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    commit_message = f"Automated update: performance results {timestamp}"
    print(f"Commit message: {commit_message}")
    commit_command = ["git", "commit", "-m", commit_message]
    commit_result = run_git_command(commit_command)

    # 커밋 성공 여부 확인
    if commit_result is None or commit_result.returncode != 0:
         # Rebase 후 변경사항이 없어져서 커밋할 게 없는 경우도 정상일 수 있음
        if commit_result and "nothing to commit" in (commit_result.stdout + commit_result.stderr):
             print("INFO: Nothing to commit after pull/rebase. Skipping push.")
             print("\n--- Git Sync Script Finished (No Effective Changes) ---")
             return
        else:
            # 실제 커밋 오류 발생
            print("ERROR: Git commit failed.", file=sys.stderr)
            run_git_command(["git", "status"]) # 실패 시 상태 출력
            return

    # 5. 푸시
    print("\nStep 5: Pushing changes...")
    push_command = ["git", "push", remote_name, branch_name]
    push_result = run_git_command(push_command)
    # 푸시 성공 여부 확인
    if push_result is None or push_result.returncode != 0:
        print("ERROR: Git push failed.", file=sys.stderr)
        return

    print("\n--- Git Sync Script Finished Successfully ---")

if __name__ == "__main__":
    # 스크립트가 있는 폴더로 작업 디렉토리 변경 (안정성 위해)
    # repo_path 설정이 이미 스크립트 경로 기준이므로 chdir 불필요할 수 있으나,
    # subprocess가 현재 디렉토리 영향을 받을 수 있으므로 명시적으로 변경
    try:
        os.chdir(repo_path)
        print(f"Changed working directory to: {os.getcwd()}")
        main()
    except Exception as e:
        print(f"FATAL ERROR: Unhandled exception in script execution - {e}", file=sys.stderr)
        import traceback
        traceback.print_exc() # 전체 스택 트레이스 출력

    # 자동 실행 시에는 아래 input 제거
    # input("Press Enter to exit...")