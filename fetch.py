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
TOKEN = os.environ.get("META_TOKEN", "")
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
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  page retry {i + 1}/{tries - 1} after: {str(e)[:150]}", file=sys.stderr, flush=True)
            time.sleep(15 * (i + 1))


class ReportFailed(RuntimeError):
    pass


class OutOfTime(RuntimeError):
    pass


JOB_CAP = 10 * 60  # Mika 26.11: a Meta report that isn't ready in 10 minutes is asked for again


def wait_job(rid, label):
    """Wait for a Meta async report, writing its state to the log every minute.
    Meta sometimes fails or stalls a report (26.11: two refreshes sat silent for an hour)."""
    t0, last_print = time.time(), 0
    while True:
        st = get(f"{API}/{rid}", {"fields": "async_status,async_percent_completion"})
        s, pct = st["async_status"], st.get("async_percent_completion")
        if s == "Job Completed":
            return
        if s in ("Job Failed", "Job Skipped"):
            raise ReportFailed(f"{label}: report {rid} {s}")
        if time.time() - t0 > JOB_CAP:
            raise ReportFailed(f"{label}: report {rid} still '{s}' {pct}% after {JOB_CAP // 60} min, asking again")
        if time.time() - last_print >= 60:
            print(f"  {label}: {s} {pct}% ({int(time.time() - t0)}s)", file=sys.stderr, flush=True)
            last_print = time.time()
        time.sleep(5)


def report_rows(account_id, since, until):
    """One async report job — large ranges time out as plain GET.
    No impressions filter: Meta books some purchases on days the ad had no impressions (Mika 26.11, ~2% of purchases)."""
    job = get(f"{API}/{account_id}/insights", {
        "level": "ad", "time_increment": 1, "fields": FIELDS,
        "time_range": json.dumps({"since": since, "until": until}),
        "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
    }, method="POST")
    rid = job["report_run_id"]
    wait_job(rid, f"{account_id} {since}..{until}")
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


AUD_FIELDS = "ad_id,ad_name,campaign_name,spend,impressions,inline_link_clicks,actions,action_values"
AUD_WEEKS = 5  # whole weeks ending on the last data day; 13 weeks ran past an hour on 9.11, so 5 (covers "last 30 days")


def audience(accounts, until, weeks=AUD_WEEKS, deadline=None):
    """Age x gender per ad from Meta, summed per week x (editor, series, account) x segment.
    Ad level is needed for the editor and series; summing keeps the file small (Mika 9.11).
    Editors here come from the morning pull, so an editor-rule change shows on the next morning.
    Past the deadline it gives up, and the caller keeps yesterday's tab (Mika 26.11)."""
    since = until - dt.timedelta(days=weeks * 7 - 1)
    groups, gi, segs, si, cells = [], {}, [], {}, {}
    for acc in accounts:
        for w in range(weeks):
            if deadline and time.time() > deadline:
                raise OutOfTime(f"audience: out of time at {acc['name']} week {w + 1}/{weeks}")
            ws = since + dt.timedelta(days=7 * w)
            we = ws + dt.timedelta(days=6)
            rows = audience_rows(acc["id"], ws.isoformat(), we.isoformat())
            for r in rows:
                key = creative_key(r.get("ad_name", ""))
                g = (editor_of(key), series_of(key, r.get("campaign_name", "")), acc["name"])
                if g not in gi:
                    gi[g] = len(groups)
                    groups.append(list(g))
                s = f"{r.get('gender', 'unknown')}|{r.get('age', 'Unknown')}"
                if s not in si:
                    si[s] = len(segs)
                    segs.append(s)
                c = cells.setdefault((w, gi[g], si[s]), [0.0, 0, 0, 0.0, 0.0])
                c[0] += float(r.get("spend") or 0)
                c[1] += int(r.get("impressions") or 0)
                c[2] += int(r.get("inline_link_clicks") or 0)
                c[3] += act(r.get("actions"))
                c[4] += act(r.get("action_values"))
        print(f"audience {acc['name']}: done", file=sys.stderr, flush=True)
    out = [[w, g, s, round(c[0], 2), c[1], c[2], c[3], round(c[4], 2)]
           for (w, g, s), c in cells.items() if c[0] or c[3]]
    return {"aud_since": since.isoformat(), "aud_weeks": weeks, "aud_groups": groups, "aud_segs": segs,
            "aud_cols": ["week", "group", "seg", "spend", "impr", "clicks", "purch", "value"], "aud": out}


def audience_rows(account_id, since, until):
    for attempt in range(3):
        try:
            job = get(f"{API}/{account_id}/insights", {
                "level": "ad", "fields": AUD_FIELDS, "breakdowns": "age,gender",
                "time_range": json.dumps({"since": since, "until": until}),
                "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
            }, method="POST")
            rid = job["report_run_id"]
            wait_job(rid, f"audience {account_id} {since}")
            return list(paged(f"{API}/{rid}/insights", {"limit": 2000}))
        except ReportFailed as e:
            print(f"  {e} {account_id} {since}; retry {attempt + 1}/3", file=sys.stderr, flush=True)
            time.sleep(30 * (attempt + 1))
    raise ReportFailed(f"audience {account_id} {since}..{until} keeps failing")


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


# ---------- blocked ads (Mika 9.11: "שיתחיל מהיום לא אחורה") ----------
# The team swaps the video inside a rejected ad, after which Meta only shows the new one.
# The activity log keeps both: the rejection (run status -> "Not Approved") and the swap
# (update_ad_creative, old creative id in old_value[2]). So the original that was blocked can be shown.
REJECTED = ("not approved", "disapproved", "rejected")
# "נדרש עדכון" / With issues is a rejection too (Ads Manager 9.11: "Can Not Enable Ad with Ad Review Rejected").
# Its label is translated per user locale, so match the run_status code, which every locale shares:
# 18 = with issues (seen on ad 52562782358014, 8.11 ~13:23 UTC, together with a with_issue_code). 1 = active.
WITH_ISSUES_CODE = 18


def rejection_kind(x):
    """'Rejected', 'With issues', or '' for a run-status change that is not a block."""
    rs = x.get("run_status") if isinstance(x.get("run_status"), dict) else {}
    if rs.get("new_value") == WITH_ISSUES_CODE or "with_issue_code" in x:
        return "With issues"
    return "Rejected" if str(x.get("new_value", "")).lower() in REJECTED else ""


def was_active(x):
    rs = x.get("run_status") if isinstance(x.get("run_status"), dict) else {}
    return rs.get("old_value") == 1 or str(x.get("old_value", "")).lower() == "active"


def log_day(account_id, day):
    """One UTC day of the activity log. Gamma IL writes ~1,800 events a day; reading 20+ days in
    one paging chain failed with a Meta 500 in 19 of 21 runs (checked 26.11), one day takes ~20s."""
    start = int(dt.datetime.combine(day, dt.time(), dt.timezone.utc).timestamp())
    params = {"fields": "event_type,object_id,object_name,extra_data,event_time", "limit": 200,
              "since": start, "until": start + 86400}
    out = []
    for e in paged(f"{API}/{account_id}/activities", params):
        if e.get("event_type") in ("update_ad_run_status", "update_ad_creative"):
            try:
                x = json.loads(e.get("extra_data") or "{}")
            except ValueError:
                x = {}
            out.append({"event_type": e["event_type"], "object_id": e.get("object_id"),
                        "object_name": e.get("object_name", ""), "event_time": e["event_time"], "x": x})
    return out


def log_events(account_id, since_day, cache, deadline=None):
    """Activity log since since_day, one day at a time. Whole UTC days already read on an earlier
    run come from `cache` ({day: events}) and are not asked again; today is always re-read."""
    out, today = [], dt.datetime.now(dt.timezone.utc).date()
    d = since_day
    while d <= today:
        k = d.isoformat()
        if d < today and k in cache:
            out += cache[k]
        else:
            if deadline and time.time() > deadline:
                raise OutOfTime(f"activity log {account_id}: out of time at {k}")
            ev = log_day(account_id, d)
            if d < today:
                cache[k] = ev
            out += ev
        d += dt.timedelta(days=1)
    return out


def objects(ids, fields):
    """Batched lookups. One object we may not read fails its whole batch, so a failed batch is split
    in half until the bad one is alone (one-by-one fallback hit Meta's request limit on 26.11)."""
    out, ids = {}, [i for i in dict.fromkeys(ids) if i]

    def fetch_ids(chunk):
        try:
            out.update(get(f"{API}/", {"ids": ",".join(chunk), "fields": fields}, tries=2))
        except Exception as e:
            if len(chunk) == 1:
                return
            if len(chunk) == 50:
                print("objects batch failed, splitting:", str(e)[:120], file=sys.stderr, flush=True)
            half = len(chunk) // 2
            fetch_ids(chunk[:half])
            fetch_ids(chunk[half:])
    for i in range(0, len(ids), 50):
        fetch_ids(ids[i:i + 50])
    return out


def blocked_ads(spent_ids, log_cache=None, deadline=None):
    """One row per (ad, original creative) rejected since config blocked_since.
    log_cache ({account id: {day: events}}) is filled in place, so the next run reads only new days.
    If an account's log can't be read, the whole stage fails and the caller keeps yesterday's tab:
    a tab missing Gamma IL looked complete and showed ~30 blocked ads instead of ~630 (26.11)."""
    since = CONFIG.get("blocked_since", "2026-11-09")
    since_day = dt.date.fromisoformat(since)
    log_cache = {} if log_cache is None else log_cache
    found = []
    for acc in CONFIG["accounts"]:
        t0 = time.time()
        ev = log_events(acc["id"], since_day, log_cache.setdefault(acc["id"], {}), deadline)
        print(f"blocked: {acc['name']} log, {len(ev)} events ({int(time.time() - t0)}s)", file=sys.stderr, flush=True)
        ev.sort(key=lambda e: e["event_time"])
        swaps = {}
        for e in ev:
            if e["event_type"] == "update_ad_creative":
                old, new = e["x"].get("old_value") or [], e["x"].get("new_value") or []
                swaps.setdefault(e["object_id"], []).append((e["event_time"], str(old[-1]) if old else "", str(new[-1]) if new else ""))
        seen = set()
        for e in ev:
            kind = rejection_kind(e["x"]) if e["event_type"] == "update_ad_run_status" else ""
            if not kind:
                continue
            aid, t = e["object_id"], e["event_time"]
            later = [s for s in swaps.get(aid, []) if s[0] > t]
            orig = later[0][1] if later else ""
            if (aid, orig or "now") in seen:
                continue
            seen.add((aid, orig or "now"))
            found.append({"aid": aid, "name": e.get("object_name", ""), "t": t, "acc": acc["name"],
                          "orig": orig, "new": later[-1][2] if later else "",
                          "kind": kind, "was_live": was_active(e["x"])})
    if not found:
        return []
    cfields = "id,name,thumbnail_url,video_id,object_story_spec{video_data{video_id}}"
    ads = objects([f["aid"] for f in found], f"name,effective_status,ad_review_feedback,campaign{{name}},creative{{{cfields}}}")
    crs = objects([c for f in found for c in (f["orig"], f["new"])], cfields)

    def vid_of(c):  # the creative's thumbnail is often just the page logo; the video itself has the real frame and file name
        return (c or {}).get("video_id") or (((c or {}).get("object_story_spec") or {}).get("video_data") or {}).get("video_id", "")
    pool = [(ads.get(f["aid"], {}).get("creative") or {}) for f in found] + list(crs.values())
    vids = objects([vid_of(c) for c in pool], "picture,title")

    def look(c):
        v = vids.get(vid_of(c), {})
        title = re.sub(r"\.(mp4|mov)$", "", v.get("title") or "", flags=re.I)
        return (v.get("picture") or (c or {}).get("thumbnail_url", "")), title
    match, rows = drive_matcher(load_drive()), []
    for f in found:
        ad = ads.get(f["aid"], {})
        cur = ad.get("creative") or {}
        orig = crs.get(f["orig"]) if f["orig"] else cur
        new = crs.get(f["new"], {}) if f["new"] else {}
        name = ad.get("name") or f["name"]
        o_thumb, o_title = look(orig)
        n_thumb, n_title = look(new) if new else ("", "")
        key = creative_key(o_title or name)
        camp = (ad.get("campaign") or {}).get("name", "")
        fb = ad.get("ad_review_feedback") or {}
        reason = "; ".join(f"{k}: {v}" for part in fb.values() if isinstance(part, dict) for k, v in part.items())
        same_video = bool(new) and vid_of(new) == vid_of(orig)
        if fb or ad.get("effective_status") in ("DISAPPROVED", "WITH_ISSUES"):
            status = "Still blocked"
        elif new:
            status = "Text replaced" if same_video else "Replaced"
        else:
            status = "Approved again"
        rows.append([f["aid"], name, key, editor_of(key), series_of(key, camp), camp, f["acc"], f["t"][:10],
                     o_thumb, status, n_thumb, n_title or re.sub(r"\s*\[.*$", "", re.sub(r"^CR\s+", "", new.get("name", ""))),
                     reason, f["was_live"] or f["aid"] in spent_ids, music_of(key), format_of(key), match(key), f["kind"]])
    print(f"blocked: {len(rows)} blocked originals since {since} "
          f"({sum(r[-1] == 'Rejected' for r in rows)} rejected, {sum(r[-1] == 'With issues' for r in rows)} with issues)", file=sys.stderr)
    return rows


BLOCKED_COLS = ["ad_id", "name", "creative", "editor", "series", "campaign", "account", "date", "thumb",
                "status", "new_thumb", "new_name", "reason", "ran", "music", "format", "drive", "kind"]


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
                    act(r.get("actions"), "video_view"),  # 3-second views, for Hook rate (Mikita via Mika 24.11)
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

    old_aud = {}
    if os.environ.get("MERGE") == "1":
        # Mika 24.11: "אל תמחק את הקודמים, תביא עדכון ל-7 ימים האחרונים ואת השאר תשאיר".
        # Fresh days replace the same days in the live file; older days stay; window stays 90 days.
        old = open_live()
        ocols = old["ad_cols"]
        assert ocols == ["name", "creative", "editor", "series", "campaign", "account", "thumb", "id",
                         "music", "format", "ratio", "drive"], ocols
        final_since = until - dt.timedelta(days=89)
        osince = dt.date.fromisoformat(old["since"])
        pos = {a[7]: i for i, a in enumerate(ad_list)}
        remap = {}
        for i, a in enumerate(old["ads"]):
            if a[7] not in pos:
                pos[a[7]] = len(ad_list)
                ad_list.append(a)
            remap[i] = pos[a[7]]
        kept = []
        for r in old["rows"]:
            d = osince + dt.timedelta(days=r[0])
            if final_since <= d < since:
                kept.append([(d - final_since).days, remap[r[1]]] + r[2:])
        shift = (since - final_since).days
        for r in rows:
            r[0] += shift
        print(f"merge: kept {len(kept)} old rows before {since}, {len(rows)} fresh rows", file=sys.stderr)
        rows = kept + rows
        since = final_since
        old_aud = {k: v for k, v in old.items() if k.startswith("aud")}

    payload = {
        "generated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "since": since.isoformat(), "until": until.isoformat(),
        "ad_cols": ["name", "creative", "editor", "series", "campaign", "account", "thumb", "id",
                    "music", "format", "ratio", "drive"],
        "row_cols": ["day", "ad", "spend", "impr", "clicks", "purch", "value", "p25", "p50", "p75",
                     "p100", "thru", "watch_sum", "plays"],
        "editors": [e["name"] for e in CONFIG["editors"]] + [CONFIG["default_editor"]],
        "ads": ad_list, "rows": rows,
        "blocked_cols": BLOCKED_COLS, "blocked_since": CONFIG.get("blocked_since", "2026-11-09"),
    }
    # Mika 26.11: the numbers, Blocked and Audience stand apart. Each extra stage has its own time limit;
    # if it fails or runs out of time, the page keeps that tab from the last good run and the numbers still go up.
    live = live_payload()
    print(f"numbers done in {int(time.time() - START) // 60} min", file=sys.stderr, flush=True)
    log_cache = open_enc(os.path.join(ROOT, "site", "blocked_log.enc"), url=f"{SITE}/blocked_log.enc") or {}
    try:
        payload["blocked"] = blocked_ads({a[7] for a in ad_list}, log_cache, stage_deadline(25))
    except Exception as e:
        print(f"blocked: kept from the last good run ({e})", file=sys.stderr, flush=True)
        payload["blocked"] = live.get("blocked", [])
    if log_cache:  # days read so far are kept even when the stage failed, so the next run starts further on
        write_enc(os.path.join(ROOT, "site", "blocked_log.enc"), log_cache)
    if old_aud:  # a merge keeps yesterday's audience tab
        payload.update(old_aud)
    else:
        try:
            payload.update(audience(accounts, until, deadline=stage_deadline(35)))
        except Exception as e:
            print(f"audience: kept from the last good run ({e})", file=sys.stderr, flush=True)
            payload.update({k: v for k, v in live.items() if k.startswith("aud")})
    gz = write_enc(os.path.join(ROOT, "site", "data.enc"), payload)
    print(f"ads={len(ad_list)} rows={len(rows)} gz={gz // 1024}KB, all done in {int(time.time() - START) // 60} min",
          file=sys.stderr)


START = time.time()
SITE = "https://mikaungar-alt.github.io/gamma-creative-analytics"
RUN_BUDGET = 95 * 60  # the GitHub step is stopped at 100 minutes; finish and save before that


def stage_deadline(minutes):
    """A stage gets `minutes`, but never past the run budget."""
    return min(time.time() + minutes * 60, START + RUN_BUDGET)


def write_enc(path, obj):
    raw = gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), 9)
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(hashes.SHA256(), 32, salt, 250000).derive(PASSWORD.encode())
    ct = AESGCM(key).encrypt(iv, raw, None)
    with open(path, "w") as f:
        json.dump({"v": 1, "iter": 250000, "salt": base64.b64encode(salt).decode(),
                   "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}, f)
    return len(raw)


def open_enc(path, url=None):
    """Decrypt a local file, or the published copy at `url`. None if neither can be read."""
    try:
        if url and not os.path.exists(path):
            with urllib.request.urlopen(url, timeout=60) as r:
                d = json.load(r)
        else:
            d = json.load(open(path))
        key = PBKDF2HMAC(hashes.SHA256(), 32, base64.b64decode(d["salt"]), d["iter"]).derive(PASSWORD.encode())
        return json.loads(gzip.decompress(AESGCM(key).decrypt(base64.b64decode(d["iv"]), base64.b64decode(d["ct"]), None)))
    except Exception as e:
        print(f"could not open {url or path}: {str(e)[:120]}", file=sys.stderr, flush=True)
        return None


def live_payload():
    """What the site shows now, to fall back on per tab."""
    return open_enc(os.path.join(ROOT, "site", "live.enc"), url=f"{SITE}/data.enc") or {}


def open_live():
    d = json.load(open(os.path.join(ROOT, "site", "data.enc")))
    key = PBKDF2HMAC(hashes.SHA256(), 32, base64.b64decode(d["salt"]), d["iter"]).derive(PASSWORD.encode())
    return json.loads(gzip.decompress(AESGCM(key).decrypt(base64.b64decode(d["iv"]), base64.b64decode(d["ct"]), None)))


def relink():
    """Only config changed: open the live data, redo the editor and drive columns, lock it again.
    Seconds instead of a full Meta pull."""
    path = os.path.join(ROOT, "site", "data.enc")
    d = json.load(open(path))
    key = PBKDF2HMAC(hashes.SHA256(), 32, base64.b64decode(d["salt"]), d["iter"]).derive(PASSWORD.encode())
    payload = json.loads(gzip.decompress(AESGCM(key).decrypt(base64.b64decode(d["iv"]), base64.b64decode(d["ct"]), None)))
    cols = payload["ad_cols"]
    if "drive" not in cols:
        cols.append("drive")
        for a in payload["ads"]:
            a.append("")
    ci, di = cols.index("creative"), cols.index("drive")
    # editor rules live in config.json, so a rule change (Denis DG -> HE, 9.11) also lands without a Meta pull
    ei = cols.index("editor")
    payload["editors"] = [e["name"] for e in CONFIG["editors"]] + [CONFIG["default_editor"]]
    match, links = drive_matcher(load_drive()), {}
    for a in payload["ads"]:
        a[ei] = editor_of(a[ci])
        if a[ci] not in links:
            links[a[ci]] = match(a[ci])
        a[di] = links[a[ci]]
    print(f"drive links: {sum(1 for v in links.values() if v)} of {len(links)} creatives", file=sys.stderr)
    # Mika 26.11: a push never asks Meta for anything. Blocked and Audience come with the morning run;
    # asking for them here made a one-line fix take 22 minutes to go live.
    raw = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, raw, None)
    with open(path, "w") as f:
        json.dump(dict(d, iv=base64.b64encode(iv).decode(), ct=base64.b64encode(ct).decode()), f)


if __name__ == "__main__":
    relink() if "--relink" in sys.argv else main()
