import gspread
import sys
from oauth2client.service_account import ServiceAccountCredentials

sys.stdout.reconfigure(encoding='utf-8')

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("../stock-auto-writer-44eaa06c140c.json", scope)
gc = gspread.authorize(creds)

sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
sh = gc.open_by_key(sheet_key)

search_terms = ["대출", "금리", "이자", "잔액"]

for ws in sh.worksheets():
    print(f"\nWorksheet: {ws.title}")
    data = ws.get_all_values()
    found = False
    for r_idx, row in enumerate(data):
        for c_idx, val in enumerate(row):
            for term in search_terms:
                if term in val:
                    # Print context
                    col_a1 = gspread.utils.rowcol_to_a1(r_idx+1, c_idx+1)
                    print(f"  {col_a1}: {val!r} (Row {r_idx+1}, Col {c_idx+1})")
                    found = True
                    break
    if not found:
        print("  (No matches)")
