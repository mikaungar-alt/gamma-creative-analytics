#!/usr/bin/env python3
"""Runs on Kfir, not in GitHub: list every video in the creative folders of the Gamma Drive
and write an encrypted file name -> Drive file id map (site/../drive.enc) for fetch.py.

Reads a copy of Google Drive for Desktop's local index, so it needs no Google login.
Key: DRIVE_KEY (base64, 32 bytes) — same value as the GitHub secret DRIVE_KEY.
"""
import base64, gzip, json, os, re, shutil, sqlite3, sys, tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))
DRIVEFS = os.path.expanduser("~/Library/Application Support/Google/DriveFS/112078562847122475762")


def norm(name):
    """Same rule as fetch.py: file name and ad name meet on this key (Mika, 20.10)."""
    n = name.strip().lower()
    n = re.sub(r"\.(mp4|mov)$", "", n)
    n = re.sub(r"^(copy of\s+)+", "", n)
    n = re.sub(r"(\s*[-–]\s*copy(\s*\d+)?)+$", "", n)
    n = re.sub(r"\s*\(\d+\)$", "", n)
    n = n.replace(" x ", "__")
    n = re.sub(r"[^a-z0-9]", "_", n)
    # rank in front, with _ or a space, sometimes twice: 00_18_BOYFRIEND_..., "19 Fboy_..." (Mika, 26.10)
    return re.sub(r"^(\d{1,3}_)+", "", n).rstrip("_")


def main():
    key = base64.b64decode(os.environ["DRIVE_KEY"])
    tmp = tempfile.mkdtemp()
    try:  # never query the live index
        for f in ("metadata_sqlite_db", "metadata_sqlite_db-wal"):
            if os.path.exists(os.path.join(DRIVEFS, f)):
                shutil.copy(os.path.join(DRIVEFS, f), os.path.join(tmp, f.replace("metadata_sqlite_db", "db")))
        db = sqlite3.connect(os.path.join(tmp, "db"))
        roots = ",".join("'%s'" % f["id"] for f in CONFIG["drive_folders"])
        rows = db.execute(f"""
            with recursive t(sid) as (
              select stable_id from items where id in ({roots})
              union select p.item_stable_id from stable_parents p join t on p.parent_stable_id = t.sid)
            select i.id, i.local_title, coalesce(i.modified_date, 0) from t join items i on i.stable_id = t.sid
            where i.mime_type like 'video/%' and i.trashed = 0 and i.is_tombstone = 0""").fetchall()
        db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    best = {}
    for fid, title, mod in rows:  # the same file sits in several folders: keep the newest copy
        k = norm(title)
        if k and (k not in best or mod > best[k][1]):
            best[k] = (fid, mod)
    if len(best) < 1000:
        sys.exit(f"only {len(best)} files — Drive index looks broken, keeping the old map")
    raw = gzip.compress(json.dumps({k: v[0] for k, v in best.items()}, separators=(",", ":")).encode(), 9)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, raw, None)
    with open(os.path.join(ROOT, "drive.enc"), "w") as f:
        json.dump({"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}, f)
    print(f"videos={len(rows)} names={len(best)} gz={len(raw)//1024}KB")


if __name__ == "__main__":
    main()
