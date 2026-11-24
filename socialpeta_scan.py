#!/usr/bin/env python3
"""Runs on Kfir every morning (launchd com.mika.sp.competitors), with no Claude in the loop (Mika 24.11:
"כן - איך עושים את זה?"). Fills the Competitors tab of the analytics site from SocialPeta.

SocialPeta has no API: this drives Mika's logged-in Chrome through AppleScript, running the tested page
scripts in socialpeta/. It opens two tabs (ad list, Top Dramas), puts her own tab back in front right away,
works in the background and closes only the tabs it opened, by their Chrome tab id.

1. US · English · Meta + TikTok · Top 1%/10% · 30 days · by impressions        (morning skill 5ג)
2. PAGES pages of 60 cards; each card's Detail window gives the .mp4, the poster frame and the drama
3. Same video on several networks = one ad
4. AI per drama: Mika's verdicts (mika-ai-verdicts.json) win; otherwise SocialPeta's "AI Cast" tag
5. top = first 10 · notai = first 10 whose drama Mika checked as not AI. A drama nobody has checked
   does not go into notai (Mika 24.11 14:14 marked every untagged drama as AI); it is listed in
   pending-check.json so it can be put in front of her
6. competitors_push.py encrypts and publishes. Any failure: one mail to Mika, nothing published.
"""
import datetime, json, os, re, subprocess, sys, time, traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(ROOT, "socialpeta")
WORK = os.path.expanduser("~/Library/Application Support/claude-work/socialpeta")
LOG = os.path.expanduser("~/Library/Logs/sp-competitors.log")
PAGES = int(os.environ.get("SP_PAGES", "3"))
LIST_URL = "https://socialpeta.com/modules/drama/creative"
CHART_URL = "https://socialpeta.com/modules/drama/charts"
sys.path.insert(0, os.path.expanduser("~/.claude/tools"))


def log(s):
    with open(LOG, "a") as f:
        f.write(datetime.datetime.now().strftime("%d.%m %H:%M:%S ") + s + "\n")


def osa(script, *args):
    r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError("osascript: " + r.stderr.strip()[:300])
    return r.stdout.strip()


def open_tab(url):
    """New tab in Chrome's front window; her tab goes straight back to the front. Returns the tab id."""
    return osa(f'''tell application "Google Chrome"
      set w to front window
      set keep to active tab index of w
      set t to make new tab at end of tabs of w with properties {{URL:"{url}"}}
      set active tab index of w to keep
      return id of t
    end tell''')


def close_tab(tid):
    osa(f'''tell application "Google Chrome"
      repeat with w in windows
        repeat with t in tabs of w
          if (id of t as text) is "{tid}" then
            close t
            return "closed"
          end if
        end repeat
      end repeat
    end tell''')


def run(tid, name, **vars):
    pre = "".join(f"window.__{k}__={json.dumps(v)};" for k, v in vars.items())
    js = pre + open(os.path.join(JS, name)).read()
    path = os.path.join(WORK, "_run.js")
    open(path, "w").write(js)
    return osa(f'''on run argv
      set js to (read POSIX file (item 1 of argv) as «class utf8»)
      tell application "Google Chrome"
        repeat with w in windows
          repeat with t in tabs of w
            if (id of t as text) is "{tid}" then return execute t javascript js
          end repeat
        end repeat
      end tell
      return "TAB GONE"
    end run''', path)


def wait_for(fn, ok, tries=10, gap=2):
    v = None
    for _ in range(tries):
        time.sleep(gap)
        v = fn()
        if ok(v):
            return v
    return v


def set_filters(t):
    for ph, w in (("Country/Region", "United States"), ("Language", "English")):
        sel = run(t, "sel.js")
        if f"{ph}: {w}" in sel:
            continue
        run(t, "open.js", PH=ph, W=w); time.sleep(2)
        for _ in range(3):
            if "checked=true" in run(t, "pick.js", PH=ph, W=w):
                break
            time.sleep(1.5)
        run(t, "ok2.js", W=w); time.sleep(4)
    run(t, "f.js"); time.sleep(4)
    run(t, "sort.js"); time.sleep(6)
    sel = run(t, "sel.js")
    need = ["Country/Region: United States", "Language: English", "Top 1% Creatives", "TikTok", "FB News Feed"]
    miss = [n for n in need if n not in sel]
    if miss:
        raise RuntimeError("filters did not hold: " + ", ".join(miss) + " | " + sel[:300])


def scan_list(t):
    rows = []
    for page in range(1, PAGES + 1):
        if page > 1:
            if run(t, "next.js") != "next":
                break
            time.sleep(7)
        n = int(wait_for(lambda: run(t, "cards_count.js"), lambda v: v.isdigit() and int(v) > 0, tries=8))
        for i in range(n):
            card = json.loads(run(t, "card.js", I=i))
            m = {}
            for _ in range(2):
                run(t, "det.js", I=i)
                m = json.loads(wait_for(lambda: run(t, "one.js"), lambda v: '"video":"http' in v, tries=5) or "{}")
                run(t, "close.js"); time.sleep(1)
                if m.get("video"):
                    break
            rows.append({"p": page, "i": i, "card": card, "modal": m})
        sel = run(t, "sel.js")
        if "Country/Region: United States" not in sel:
            raise RuntimeError(f"filters dropped on page {page}")
        log(f"page {page}: {n} cards")
    return rows


def ai_tags(c, dramas):
    run(c, "all.js"); time.sleep(4)
    out = {}
    for d in dramas:
        run(c, "q.js", Q=d); time.sleep(1); run(c, "go.js")
        r = wait_for(lambda: run(c, "res2.js", Q=d), lambda v: v != "notready", tries=8)
        out[d] = "AI" if "AI" in r.split(",") else "Not labeled"
    return out


def build(rows, sp_ai, verdicts):
    seen, ads = set(), []
    for r in sorted(rows, key=lambda r: (r["p"], r["i"])):
        m, c = r["modal"], r["card"]
        v = m.get("video")
        if not v or v in seen:
            continue
        seen.add(v)
        li = next((k for k, x in enumerate(c) if re.fullmatch(r"(\d+m )?\d+s", x)), None)
        cs = c.index("Creative Score") if "Creative Score" in c else len(c)
        g = lambda key: c[c.index(key) + 1] if key in c else None
        dur = re.sub(r"\D", "", m.get("dur") or g("Duration") or "")
        d = m.get("drama")
        ai = "AI" if d in verdicts["AI"] else "Real actors" if d in verdicts["Real"] else sp_ai.get(d, "Not labeled")
        ads.append({"app": c[0], "advertiser": m.get("acct") or (c[1] if len(c) > 1 else None), "drama": d, "ai": ai,
                    "len": c[li] if li is not None else None, "text": " ".join(c[li + 1:cs]) if li is not None else "",
                    "days": int(dur) if dur else None, "last_seen": g("Last Seen"),
                    "impr": next((x.split(":", 1)[1] for x in c if x.startswith("Impression:")), m.get("impr")),
                    "video": v, "thumb": m.get("poster")})
    return ads


def chrome_running():
    r = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True)
    return r.returncode == 0


def main():
    os.makedirs(WORK, exist_ok=True)
    today = datetime.date.today().isoformat()
    # Mika 24.11 15:35: "07:30 בבוקר כפיר עוד לא ער לרוב". launchd tries every 30 minutes;
    # the scan runs once, on the first try of the day when Kfir is awake and Chrome is open.
    done = os.path.join(WORK, f".done-{today}")
    if os.path.exists(done) or not chrome_running():
        return
    if not (8 <= datetime.datetime.now().hour < 19):
        return
    vpath = os.path.join(WORK, "mika-ai-verdicts.json")
    verdicts = json.load(open(vpath)) if os.path.exists(vpath) else {"AI": [], "Real": []}
    t = c = None
    try:
        t = open_tab(LIST_URL); time.sleep(12)
        body = run(t, "sel.js")
        if "logged in on another device" in body.lower() or "Selected" not in body:
            raise RuntimeError("SocialPeta is not logged in here (another device?): " + body[:200])
        set_filters(t)
        rows = scan_list(t)
        json.dump(rows, open(os.path.join(WORK, f"raw-{today}.json"), "w"), ensure_ascii=False)
        dramas = sorted({r["modal"].get("drama") for r in rows if r["modal"].get("drama")})
        todo = [d for d in dramas if d not in verdicts["AI"] and d not in verdicts["Real"]]
        sp_ai = {}
        if todo:
            c = open_tab(CHART_URL); time.sleep(10)
            sp_ai = ai_tags(c, todo)
        ads = build(rows, sp_ai, verdicts)
        if len(ads) < 10:
            raise RuntimeError(f"only {len(ads)} ads with a video link")
        top = ads[:10]
        notai = [a for a in ads if a["ai"] == "Real actors"][:10]
        pending = sorted({a["drama"] for a in ads if a["ai"] == "Not labeled" and a["drama"]})
        json.dump({"date": today, "dramas": [{"drama": d, **next({"thumb": a["thumb"], "video": a["video"], "app": a["app"]}
                   for a in ads if a["drama"] == d)} for d in pending]},
                  open(os.path.join(WORK, "pending-check.json"), "w"), ensure_ascii=False, indent=1)
        day = {"date": today, "top": top, "notai": notai,
               "filter": "United States · English · Meta and TikTok · Top 1% and 10% creatives · last 30 days · "
                         "sorted by impressions · same video counted once."}
        path = os.path.join(WORK, f"{today}.json")
        json.dump(day, open(path, "w"), ensure_ascii=False, indent=1)
        env = dict(os.environ)
        for line in open(os.path.expanduser("~/.claude/secrets.env")):
            if line.startswith("GCA_DASH_PASSWORD="):
                env["GCA_DASH_PASSWORD"] = line.split("=", 1)[1].strip().strip('"').strip("'")
        out = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "competitors_push.py"), path],
                             env=env, capture_output=True, text=True, check=True).stdout.strip()
        log(f"ok: {len(ads)} ads, notai={len(notai)}, to check={len(pending)} · {out}")
        open(done, "w").write(out)
    except Exception as e:
        log("FAILED: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        mailed = os.path.join(WORK, f".mailed-{today}")
        if os.path.exists(mailed):
            sys.exit(1)  # one mail a day; the next half-hour try may still succeed
        open(mailed, "w").write("1")
        try:
            from importlib import util
            spec = util.spec_from_file_location("gw", os.path.expanduser("~/.claude/tools/gca-watch.py"))
            gw = util.module_from_spec(spec); spec.loader.exec_module(gw)
            gw.send("🕵️ Competitors לא התעדכן היום",
                    "הסריקה של סושיאל פיטה לא הצליחה הבוקר, ולכן בלשונית Competitors נשארו בינתיים הרשימות של היום הקודם. הסקריפט ינסה שוב כל חצי שעה עד 19:00.\n\n"
                    f"הסיבה, כפי שהסקריפט ראה אותה:\n{e}\n\nהלוג: ~/Library/Logs/sp-competitors.log")
        except Exception as m:
            log("mail failed too: " + str(m))
        sys.exit(1)
    finally:
        for tab in (t, c):
            if tab:
                try:
                    close_tab(tab)
                except Exception:
                    pass


if __name__ == "__main__":
    main()
