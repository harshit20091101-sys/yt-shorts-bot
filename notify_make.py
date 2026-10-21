"""Optional: video ko repo me rakhta hai aur Make.com webhook ko link bhejta hai.
MAKE_WEBHOOK_URL secret nahi hai to kuch nahi karta."""
import os, sys, json, shutil, subprocess
import requests

hook = os.environ.get("MAKE_WEBHOOK_URL", "").strip()
if not hook or not os.path.exists("out/READY") or not os.path.exists("out/short_small.mp4"):
    print("Make step skip (webhook secret nahi, ya aaj video ready nahi)")
    sys.exit(0)


def sh(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r


try:
    meta = json.load(open("out/meta.json", encoding="utf-8"))
    os.makedirs("latest", exist_ok=True)
    shutil.copyfile("out/short_small.mp4", "latest/short.mp4")
    sh("git", "config", "user.name", "bot")
    sh("git", "config", "user.email", "bot@users.noreply.github.com")
    sh("git", "add", "latest/short.mp4")
    sh("git", "commit", "-m", "latest video")
    if sh("git", "push", check=False).returncode != 0:
        sh("git", "pull", "--rebase")
        sh("git", "push")
    sha = sh("git", "rev-parse", "HEAD").stdout.strip()
    repo = os.environ["GITHUB_REPOSITORY"]
    meta["video_url"] = f"https://raw.githubusercontent.com/{repo}/{sha}/latest/short.mp4"
    resp = requests.post(hook, json=meta, timeout=60)
    print("webhook:", resp.status_code, resp.text[:100])
    if resp.status_code >= 300:
        raise RuntimeError(f"webhook HTTP {resp.status_code}")
    print("Make ko bhej diya:", meta["video_url"])
except Exception as e:
    print(f"::warning title=Make webhook fail::{e}")
sys.exit(0)
