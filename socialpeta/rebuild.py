# Rebuild today's SocialPeta lists from the saved raw scan with the new AI calls; keep today's Meta lists.
import sys, json
sys.path.insert(0, "/Users/mikaungar/gamma-creative-analytics")
import socialpeta_scan as s
import datetime
W = s.WORK; today = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
rows = json.load(open(f"{W}/raw-{today}.json"))
mv = json.load(open(f"{W}/mika-ai-verdicts.json")); cv = json.load(open(f"{W}/claude-ai-verdicts.json"))
mine = set(mv["AI"]) | set(mv["Real"])
v = {"AI": mv["AI"] + [d for d in cv["AI"] if d not in mine], "Real": mv["Real"] + [d for d in cv["Real"] if d not in mine]}
pend = {x["drama"] for x in json.load(open(f"{W}/pending-check.json"))["dramas"]}
dramas = {r["modal"].get("drama") for r in rows if r["modal"].get("drama")}
sp_ai = {d: ("Not labeled" if d in pend else "AI") for d in dramas}  # anything SocialPeta did not tag went to pending
ads = s.build(rows, sp_ai, v)
day = json.load(open(f"{W}/{today}.json"))
day["top"] = ads[:10]; day["notai"] = [a for a in ads if a["ai"] == "Real actors"][:10]
left = sorted({a["drama"] for a in ads if a["ai"] == "Not labeled" and a["drama"]})
json.dump(day, open(f"{W}/{today}.json", "w"), ensure_ascii=False, indent=1)
json.dump({"date": today, "dramas": [{"drama": d, **next({"thumb": a["thumb"], "video": a["video"], "app": a["app"]} for a in ads if a["drama"] == d)} for d in left]},
          open(f"{W}/pending-check.json", "w"), ensure_ascii=False, indent=1)
print("ads", len(ads), "notai", [(a["drama"], a["impr"]) for a in day["notai"]], "still unclear", left)
