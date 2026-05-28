import gspread
import sys
import os
import json
from oauth2client.service_account import ServiceAccountCredentials

sys.stdout.reconfigure(encoding='utf-8')

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("../stock-auto-writer-44eaa06c140c.json", scope)
gc = gspread.authorize(creds)

print("=== 1. Inspecting '📈IRP 수익률' worksheet in 'KYI_자산배분' ===")
sh_asset = gc.open('KYI_자산배분')
ws_irp = sh_asset.worksheet('📈IRP 수익률')
rows_irp = ws_irp.get_all_values()

# Print header row
print("Header row:", rows_irp[0])
print("\nFirst 10 data rows in '📈IRP 수익률' (Cols A to J):")
for idx, r in enumerate(rows_irp[1:15]):
    # Pad columns if row is shorter
    padded = r + [''] * (12 - len(r))
    print(f"Row {idx+2}: {padded[:10]}")

print("\nLast 5 data rows in '📈IRP 수익률' (Cols A to J):")
for idx, r in enumerate(rows_irp[-5:]):
    padded = r + [''] * (12 - len(r))
    print(f"Row {len(rows_irp)-4+idx}: {padded[:10]}")

print("\n=== 2. Inspecting 'IRP' worksheet in '성과_TWR_Raw' ===")
sh_twr = gc.open('성과_TWR_Raw')
ws_twr_irp = sh_twr.worksheet('IRP')
rows_twr_irp = ws_twr_irp.get_all_values()

print("Header row:", rows_twr_irp[0])
print("\nFirst 5 data rows in '성과_TWR_Raw' (IRP):")
for idx, r in enumerate(rows_twr_irp[1:6]):
    print(f"Row {idx+2}: {r}")

print("\nLast 5 data rows in '성과_TWR_Raw' (IRP):")
for idx, r in enumerate(rows_twr_irp[-5:]):
    print(f"Row {len(rows_twr_irp)-4+idx}: {r}")
