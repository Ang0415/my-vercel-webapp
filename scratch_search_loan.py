import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

search_dir = ".."
query = "보금자리론"

print(f"Searching for '{query}' in {search_dir}...")
for root, dirs, files in os.walk(search_dir):
    # Skip Vercel/api/__pycache__ and Vercel/.git etc
    if ".git" in root or "__pycache__" in root or ".gemini" in root:
        continue
    for file in files:
        if file.endswith((".py", ".html", ".css", ".md", ".json")):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if query in content:
                        print(f"Found in: {path}")
                        # Print matching lines
                        for line_no, line in enumerate(content.splitlines(), 1):
                            if query in line:
                                print(f"  Line {line_no}: {line.strip()}")
            except Exception as e:
                # Try with cp949 or other encoding
                try:
                    with open(path, "r", encoding="cp949") as f:
                        content = f.read()
                        if query in content:
                            print(f"Found in (cp949): {path}")
                            for line_no, line in enumerate(content.splitlines(), 1):
                                if query in line:
                                    print(f"  Line {line_no}: {line.strip()}")
                except Exception:
                    pass
