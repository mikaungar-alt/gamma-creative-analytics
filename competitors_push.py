#!/usr/bin/env python3
"""Runs on Kfir, not in GitHub: add today's SocialPeta lists to the Competitors tab (Mika 24.11).

SocialPeta has no API and opens only in Mika's logged-in Chrome, so the morning briefing scrapes it there
and hands this script one JSON file:
  {"date": "2026-11-24", "filter": "...", "top": [ad, ...], "notai": [ad, ...]}
  ad = {app, advertiser, drama, ai ("AI" | "Real actors" | "Not labeled"), len, text, days, last_seen, impr, video, thumb}

The history lives on Kfir (HISTORY); each run replaces the same date, keeps the rest, encrypts everything with
the team password (same lock as data.enc) and force-pushes one commit to the `competitors` branch.
The page reads it from there, so the nightly Meta refresh never has to know about it.

Usage: GCA_DASH_PASSWORD=... python3 competitors_push.py today.json
"""
import base64, gzip, json, os, subprocess, sys, tempfile
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

REPO = "https://github.com/mikaungar-alt/gamma-creative-analytics.git"
HISTORY = os.path.expanduser("~/Library/Application Support/claude-work/socialpeta/competitors-history.json")
FIELDS = ["app", "advertiser", "drama", "ai", "len", "text", "days", "last_seen", "impr", "video", "thumb"]


def main():
    day = json.load(open(sys.argv[1]))
    assert len(day["date"]) == 10 and day.get("top"), "need a date and a non-empty top list"
    for k in ("top", "notai"):
        day[k] = [{f: a.get(f) for f in FIELDS} for a in day.get(k, [])][:10]
    hist = json.load(open(HISTORY)) if os.path.exists(HISTORY) else {"days": []}
    hist["days"] = [d for d in hist["days"] if d["date"] != day["date"]] + [day]
    hist["days"].sort(key=lambda d: d["date"])
    hist["updated"] = day["date"]
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    json.dump(hist, open(HISTORY, "w"), ensure_ascii=False, indent=1)

    pw = os.environ["GCA_DASH_PASSWORD"]
    raw = gzip.compress(json.dumps(hist, separators=(",", ":"), ensure_ascii=False).encode(), 9)
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(hashes.SHA256(), 32, salt, 250000).derive(pw.encode())
    enc = {"v": 1, "iter": 250000, "salt": base64.b64encode(salt).decode(),
           "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(AESGCM(key).encrypt(iv, raw, None)).decode()}

    t = tempfile.mkdtemp()
    json.dump(enc, open(os.path.join(t, "competitors.enc"), "w"))
    git = lambda *a: subprocess.run(["git", "-C", t, *a], check=True, capture_output=True)
    git("init", "-q"); git("checkout", "-q", "-b", "competitors"); git("add", "competitors.enc")
    git("-c", "user.name=Kfir", "-c", "user.email=kfir@localhost", "commit", "-qm", f"Competitors {day['date']}")
    git("push", "-qf", REPO, "competitors")
    print(f"pushed {day['date']}: top={len(day['top'])} notai={len(day['notai'])} days={len(hist['days'])}")


if __name__ == "__main__":
    main()
