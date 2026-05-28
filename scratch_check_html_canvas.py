import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

CURRENT_DIR = r"E:\Ang\iCloudDrive\python\KOR_invest\Vercel"

def check_html_canvas():
    f_path = os.path.join(CURRENT_DIR, 'index.html')
    with open(f_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    lines = content.splitlines()
    print("--- VERCEL HTML LINES 120-170 ---")
    for idx in range(119, min(170, len(lines))):
        print(f"Line {idx+1}: {lines[idx]}")

if __name__ == "__main__":
    check_html_canvas()
