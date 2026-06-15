import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

CURRENT_DIR = r"E:\Ang\iCloudDrive\python\KOR_invest\Vercel"

def check_vercel_html():
    f_path = os.path.join(CURRENT_DIR, 'index.html')
    with open(f_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    print("Vercel/index.html length:", len(content))
    # Search for twr or charts in Vercel HTML
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if 'TWR' in line or '수익률' in line or 'chart' in line or 'iframe' in line or 'streamlit' in line:
            print(f"Line {idx+1}: {line.strip()[:120]}")

if __name__ == "__main__":
    check_vercel_html()
