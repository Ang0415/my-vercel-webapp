import gspread
import sys
from oauth2client.service_account import ServiceAccountCredentials

sys.stdout.reconfigure(encoding='utf-8')

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("../stock-auto-writer-44eaa06c140c.json", scope)
gc = gspread.authorize(creds)

sh = gc.open('KYI_자산배분')

print("Worksheet titles in KYI_자산배분:")
for ws in sh.worksheets():
    print(f" - {ws.title}")
