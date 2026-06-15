"""
Push modified Vercel files (index.html, api/twr.py) to the root of
the my-vercel-webapp GitHub repository using the GitHub Contents API.
"""
import os, json, base64
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_OWNER = "Ang0415"
GITHUB_REPO  = "my-vercel-webapp"

# Map: local path -> path in the GitHub repo (root-level)
FILES = {
    r"e:\Ang\iCloudDrive\python\KOR_invest\Vercel\index.html": "index.html",
    r"e:\Ang\iCloudDrive\python\KOR_invest\Vercel\api\twr.py":  "api/twr.py",
}

def get_token():
    t = os.environ.get("GITHUB_PAT", "")
    if not t:
        t = input("Enter your GitHub PAT: ").strip()
    return t

def upload(token, local_path, repo_path):
    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode()

    api = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{repo_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-Uploader",
    }

    # Get existing SHA
    sha = None
    try:
        req = Request(api, headers=headers)
        with urlopen(req) as r:
            sha = json.loads(r.read())["sha"]
            print(f"  Existing SHA: {sha[:7]}")
    except HTTPError as e:
        if e.code == 404:
            print("  New file")
        else:
            print(f"  Error: {e.code}"); return False

    payload = {"message": f"Update {repo_path} - add TWR vs MWR chart", "content": content_b64}
    if sha:
        payload["sha"] = sha

    try:
        req = Request(api, data=json.dumps(payload).encode(), headers=headers, method="PUT")
        with urlopen(req) as r:
            if r.status in (200, 201):
                print(f"  ✅ Uploaded {repo_path}")
                return True
    except HTTPError as e:
        print(f"  ❌ {e.code}: {e.read().decode()[:200]}")
    return False

if __name__ == "__main__":
    token = get_token()
    for local, remote in FILES.items():
        print(f"\n>> {remote}")
        upload(token, local, remote)
    print("\nDone! Vercel will redeploy in ~1 min.")
