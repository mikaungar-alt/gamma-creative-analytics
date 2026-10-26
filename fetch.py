#!/usr/bin/env python3
"""Pull ad-level daily Meta insights for the creative analytics dashboard,
then write an encrypted data file that only the dashboard password can open.

Env: META_TOKEN (system user, ads_read), DASH_PASSWORD, DAYS (default 90)
"""
import base64, datetime as dt, gzip, json, os, re, sys, time
import urllib.parse, urllib.request
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

API = "https://graph.facebook.com/v21.0"
TOKEN = os.environ["META_TOKEN"]
PASSWORD = os.environ["DASH_PASSWORD"]
DAYS = int(os.environ.get("DAYS", "90"))
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))

FIELDS = ("ad_id,ad_name,campaign_name,spend,impressions,inline_link_clicks,actions,action_values,"
          "video_play_actions,video_p25_watched_actions,video_p50_watched_actions,"
          "video_p75_watched_actions,video_p100_watched_actions,video_thruplay_watched_actions,"
          "video_avg_time_watched_actions")


def get(url, params=None, method="GET", tries=6):
    params = dict(params or {}, access_token=TOKEN)
    data = urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            if method == "GET":
                req = urllib.request.Request(url + ("&" if "?" in url else "?") + data)
            else:
                req = urllib.request.Request(url, data=data.encode(), method=method)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if i == tries - 1:
                raise RuntimeError(f"{e.code} {body[:500]}")
            print(f"  retry after {e.code}: {body[:200]}", file=sys.stderr)
            time.sleep(20 * (i + 1))
        except Exception as e:  # network
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


def paged(url, params=None):
    res = get(url, params)
    while True:
        yield from res.get("data", [])
        nxt = res.get("paging", {}).get("next")
        if not nxt:
            return
        res = get_raw(nxt)


def get_raw(full_url, tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(full_url, timeout=120) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(15 * (i + 1))


class ReportFailed(RuntimeError):
    pass


def report_rows(account_id, since, until):
    """One async report job — large ranges time out as plain GET."""
    job = get(f"{API}/{account_id}/insights", {
        "level": "ad", "time_increment": 1, "fields": FIELDS,
        "time_range": json.dumps({"since": since, "until": until}),
        "filtering": json.dumps([{"field": "impressions", "operator": "GREATER_THAN", "value": 0}]),
        "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
    }, method="POST")
    rid = job["report_run_id"]
    while True:
        st = get(f"{API}/{rid}", {"fields": "async_status,async_percent_completion"})
        if st["async_status"] == "Job Completed":
            break
        if st["async_status"] in ("Job Failed", "Job Skipped"):
            raise ReportFailed(f"report {rid} {st['async_status']} for {account_id} {since}..{until}")
        print(f"  {account_id} job {st.get('async_percent_completion')}%", file=sys.stderr, flush=True)
        time.sleep(5)
    print(f"  {account_id} {since}..{until} job done, paging", file=sys.stderr, flush=True)
    return list(paged(f"{API}/{rid}/insights", {"limit": 500}))


def insights(account_id, since, until):
    """Meta async jobs fail at random now and then: retry, then split the range in half."""
    for attempt in range(3):
        try:
            return report_rows(account_id, since, until)
        except ReportFailed as e:
            print(f"  {e}; retry {attempt + 1}/3", file=sys.stderr, flush=True)
            time.sleep(30 * (attempt + 1))
    s, u = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    if (u - s).days < 2:
        raise ReportFailed(f"{account_id} {since}..{until} keeps failing")
    mid = s + (u - s) // 2
    print(f"  splitting {since}..{until}", file=sys.stderr, flush=True)
    return (insights(account_id, since, mid.isoformat()) +
            insights(account_id, (mid + dt.timedelta(days=1)).isoformat(), until))


def act(lst, kind="purchase"):
    for a in lst or []:
        if a.get("action_type") == kind:
            return float(a.get("value") or 0)
    return 0.0


def first(lst):
    for a in lst or []:
        return float(a.get("value") or 0)
    return 0.0


# ---------- naming rules ----------
COPY_RE = re.compile(r"(\s*-\s*copy(\s*\d+)?)+\s*$", re.I)


def creative_key(name):
    n = re.sub(r"^(copy of\s+)+", "", name.strip(), flags=re.I)
    n = COPY_RE.sub("", n).strip()
    n = re.sub(r"\.mp4.*$", "", n, flags=re.I)
    return n


def editor_of(key):
    low = key.lower()
    for rule in CONFIG["editors"]:
        if re.search(rule["pattern"], low):
            return rule["name"]
    return CONFIG["default_editor"]


SERIES_RULES = [(p.lower(), s["title"]) for s in CONFIG["series"] for p in s["ad_name_patterns"]]
CAMPAIGN_CODES = {k.upper(): v for k, v in CONFIG["campaign_series_codes"].items()}
GEOS = set(CONFIG["campaign_geos"])
NOT_SERIES = set(CONFIG["campaign_codes_not_series"])


def series_of(key, campaign):
    low = key.lower()
    for p, t in SERIES_RULES:
        if p in low:
            return t
    parts = campaign.upper().split("_")
    for p in parts[1:5]:
        if p in CAMPAIGN_CODES:
            return CAMPAIGN_CODES[p]
    # a new series shows up under its campaign code (US_GIRLFRIEND_...) until config.json names it
    raw = campaign.split("_")
    if len(raw) > 1 and parts[0] in GEOS and re.fullmatch(r"[A-Z]{3,}", raw[1]) and raw[1] not in NOT_SERIES:
        return raw[1]
    return "Other"


def music_of(key):
    m = re.search(r"(?<![a-z])(mus?\d{2,3})", key, re.I)
    if m:
        return m.group(1).upper().replace("MUS", "MU")
    m = re.search(r"hook-([A-Za-z]+)", key)
    if m:
        return m.group(1)
    m = re.search(r"\bX\s+([A-Za-z]+)", key) or re.search(r"__([A-Z][a-z]+[A-Z][A-Za-z]+)", key)
    if m:
        return m.group(1)
    return ""


FORMATS = [("trl", "Trailer"), ("hl", "HL"), ("clifh", "Cliffhanger"), ("flashback", "Flashback"),
           ("endcard", "Endcard"), ("rewind", "Rewind"), ("loop", "Loop"), ("master", "Episode cut"),
           ("seg_", "Segment"), ("combo", "Combo")]


def format_of(key):
    low = "_" + key.lower() + "_"
    out = []
    for tok, label in FORMATS:
        if re.search(r"[_\s]" + re.escape(tok.strip("_")) + (r"" if tok.endswith("_") else r"[\d_\s]"), low):
            out.append(label)
    return " + ".join(out[:2])


def ratio_of(key):
    m = re.search(r"(9x16|4x5|1x1|16x9)", key, re.I)
    return m.group(1).upper() if m else ""


# ---------- Drive links (map built on Kfir by drive_map.py) ----------
def drive_norm(name):
    """Same rule as drive_map.py — keep the two in step."""
    n = name.strip().lower()
    n = re.sub(r"\.(mp4|mov)$", "", n)
    n = re.sub(r"^(copy of\s+)+", "", n)
    n = re.sub(r"(\s*[-–]\s*copy(\s*\d+)?)+$", "", n)
    n = re.sub(r"\s*\(\d+\)$", "", n)
    n = n.replace(" x ", "__")
    n = re.sub(r"[^a-z0-9]", "_", n)
    # rank in front, with _ or a space, sometimes twice: 00_18_BOYFRIEND_..., "19 Fboy_..." (Mika, 26.10)
    return re.sub(r"^(\d{1,3}_)+", "", n).rstrip("_")


def load_drive():
    path, key = os.path.join(ROOT, "drive.enc"), os.environ.get("DRIVE_KEY")
    if not key or not os.path.exists(path):
        print("no Drive map, links skipped", file=sys.stderr)
        return {}
    d = json.load(open(path))
    raw = AESGCM(base64.b64decode(key)).decrypt(base64.b64decode(d["iv"]), base64.b64decode(d["ct"]), None)
    return json.loads(gzip.decompress(raw))


def drive_matcher(dmap):
    """Exact name only, plus the two safe cases from 20.10: Meta cut the name mid-word,
    or the ad adds only digits (a date/time stamp). Never a looser guess."""
    import bisect
    keys = sorted(dmap)

    renamed = {drive_norm(k): drive_norm(v) for k, v in CONFIG.get("drive_renamed_files", {}).items() if k != "_note"}

    def match(creative):
        k = drive_norm(creative)
        if not k:
            return ""
        if renamed.get(k) in dmap:  # one-off list: files renamed after upload, e.g. Bohdan's 50 for Liran
            return dmap[renamed[k]]
        if k in dmap:
            return dmap[k]
        base = re.sub(r"_\d{1,2}_\d{1,2}_\d{4}(_\d{3,4})?$", "", k)  # Pop's _10-14-2026_1607
        if base != k and base in dmap:
            return dmap[base]
        i = bisect.bisect_left(keys, k)
        hits = []
        while i < len(keys) and keys[i].startswith(k) and len(hits) < 2:
            if keys[i][len(k)] != "_":  # cut mid-word, not at a "_" boundary
                hits.append(keys[i])
            i += 1
        return dmap[hits[0]] if len(hits) == 1 and len(k) >= 20 else ""
    return match


def thumbnails(ad_ids):
    out = {}
    ids = list(ad_ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            res = get(f"{API}/", {"ids": ",".join(chunk), "fields": "creative{thumbnail_url,video_id}"})
        except Exception as e:
            print("thumb chunk failed:", e, file=sys.stderr)
            continue
        for aid, v in res.items():
            c = v.get("creative") or {}
            out[aid] = c.get("thumbnail_url", "")
    return out


def main():
    until = dt.date.today() - dt.timedelta(days=1)
    since = until - dt.timedelta(days=DAYS - 1)
    accounts = CONFIG["accounts"]
    ads, rows = {}, []
    ad_index = {}
    for acc in accounts:
        n = 0
        # 30-day slices keep each async job small
        s = since
        while s <= until:
            e = min(s + dt.timedelta(days=29), until)
            for r in insights(acc["id"], s.isoformat(), e.isoformat()):
                aid = r["ad_id"]
                if aid not in ad_index:
                    key = creative_key(r.get("ad_name", ""))
                    ad_index[aid] = len(ads)
                    ads[aid] = [r.get("ad_name", ""), key, editor_of(key),
                                series_of(key, r.get("campaign_name", "")),
                                r.get("campaign_name", ""), acc["name"], "", aid]
                day = (dt.date.fromisoformat(r["date_start"]) - since).days
                plays = first(r.get("video_play_actions"))
                rows.append([
                    day, ad_index[aid],
                    round(float(r.get("spend") or 0), 2),
                    int(r.get("impressions") or 0),
                    int(r.get("inline_link_clicks") or 0),
                    act(r.get("actions")),
                    round(act(r.get("action_values")), 2),
                    first(r.get("video_p25_watched_actions")),
                    first(r.get("video_p50_watched_actions")),
                    first(r.get("video_p75_watched_actions")),
                    first(r.get("video_p100_watched_actions")),
                    first(r.get("video_thruplay_watched_actions")),
                    round(first(r.get("video_avg_time_watched_actions")) * plays, 1),
                    plays,
                ])
                n += 1
            s = e + dt.timedelta(days=1)
        print(f"{acc['name']}: {n} rows", file=sys.stderr)

    thumbs = thumbnails(ads.keys())
    match = drive_matcher(load_drive())
    links = {}
    ad_list = [None] * len(ads)
    for aid, a in ads.items():
        a[6] = thumbs.get(aid, "")
        key = a[1]
        if key not in links:
            links[key] = match(key)
        ad_list[ad_index[aid]] = a + [music_of(key), format_of(key), ratio_of(key), links[key]]
    print(f"drive links: {sum(1 for v in links.values() if v)} of {len(links)} creatives", file=sys.stderr)
    print("no drive link:", " | ".join(k for k, v in links.items() if not v), file=sys.stderr)

    payload = {
        "generated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "since": since.isoformat(), "until": until.isoformat(),
        "ad_cols": ["name", "creative", "editor", "series", "campaign", "account", "thumb", "id",
                    "music", "format", "ratio", "drive"],
        "row_cols": ["day", "ad", "spend", "impr", "clicks", "purch", "value", "p25", "p50", "p75",
                     "p100", "thru", "watch_sum", "plays"],
        "editors": [e["name"] for e in CONFIG["editors"]] + [CONFIG["default_editor"]],
        "ads": ad_list, "rows": rows,
    }
    raw = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(hashes.SHA256(), 32, salt, 250000).derive(PASSWORD.encode())
    ct = AESGCM(key).encrypt(iv, raw, None)
    out = os.path.join(ROOT, "site", "data.enc")
    with open(out, "w") as f:
        json.dump({"v": 1, "iter": 250000, "salt": base64.b64encode(salt).decode(),
                   "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}, f)
    print(f"ads={len(ad_list)} rows={len(rows)} gz={len(raw)//1024}KB", file=sys.stderr)


if __name__ == "__main__":
    main()
