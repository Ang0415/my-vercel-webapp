import json
import gspread
import sys
from oauth2client.service_account import ServiceAccountCredentials

# Force UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

# Load credentials
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("../stock-auto-writer-44eaa06c140c.json", scope)
gc = gspread.authorize(creds)

sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
sh = gc.open_by_key(sheet_key)

ws = sh.worksheet('예산 및 설정')
rows = ws.get_all_values()

print("Headers (Row 1):")
for idx, col in enumerate(rows[0]):
    print(f"  Col {idx} ({gspread.utils.rowcol_to_a1(1, idx+1)}): {repr(col)}")

print("\nRows (from index 1 to 10):")
for r_idx, row in enumerate(rows[1:15]):
    print(f"Row {r_idx+2}: {row}")
