import gspread
import sys
from oauth2client.service_account import ServiceAccountCredentials

sys.stdout.reconfigure(encoding='utf-8')

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("../stock-auto-writer-44eaa06c140c.json", scope)
gc = gspread.authorize(creds)

sheet_key = '1gOJ_TK33MwvBOhh6ueFyiGxvl2r-ijRVQkxiKM3vLk4'
sh = gc.open_by_key(sheet_key)
ws = sh.worksheet('예산 및 설정')
rows = ws.get_all_values()

print("All rows in '예산 및 설정':")
for i, r in enumerate(rows):
    print(f"Row {i+1}: {r}")
