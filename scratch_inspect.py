import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Load credentials
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("../stock-auto-writer-44eaa06c140c.json", scope)
gc = gspread.authorize(creds)

sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
sh = gc.open_by_key(sheet_key)

ws = sh.worksheet('예산 및 설정')
rows = ws.get_all_values()

print("Row 1 (Headers):", rows[0] if len(rows) > 0 else "Empty")
print("Row 2:", rows[1] if len(rows) > 1 else "Empty")
print("Row 3:", rows[2] if len(rows) > 2 else "Empty")
print("Row 4:", rows[3] if len(rows) > 3 else "Empty")
print("Row 5:", rows[4] if len(rows) > 4 else "Empty")

print("\nAll columns in row 1:")
for idx, col in enumerate(rows[0]):
    print(f"Col {idx} ({gspread.utils.rowcol_to_a1(1, idx+1)[:1]}): {col}")

print("\nFirst 10 rows, columns J to O if they exist:")
for r_idx, row in enumerate(rows[:10]):
    print(f"Row {r_idx+1}: {row[9:15] if len(row) > 9 else 'no col J+'}")
