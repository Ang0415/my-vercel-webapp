
try:
    with open('run_daily_update.bat', 'r', encoding='cp949') as f:
        print("--- CONTENT START ---")
        print(f.read())
        print("--- CONTENT END ---")
except Exception as e:
    print(f"Failed with cp949: {e}")

try:
    with open('run_daily_update.bat', 'r', encoding='utf-8') as f:
        print("--- UTF8 CHECK ---")
        # Just read a bit to see if it fails
        f.read() 
        print("UTF-8 read success (unexpected if garbled)")
except Exception as e:
    print(f"UTF-8 read failed as expected: {e}")
