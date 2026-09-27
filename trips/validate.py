"""檢查 trips/<slug>/trip.json 是否符合 trips/README.md 的結構。
用法：python trips/validate.py [slug ...]（不給 slug 就檢查 trips/index.json 裡的全部）
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

TRIPS = Path(__file__).resolve().parent


def dates(a, b):
    d, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def check(slug):
    errs = []
    err = errs.append
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        err("slug 只能有小寫英數與連字號")
    d = TRIPS / slug
    t = json.loads((d / "trip.json").read_text(encoding="utf-8"))
    m = t.get("meta", {})
    for k in ("destination", "origin", "start", "end", "travelers", "source"):
        if k not in m:
            err(f"meta 缺 {k}")
    for k in ("title", "summary", "currency", "localToTwd", "flights", "stays", "days", "costs", "foodLevels", "checklist"):
        if k not in t:
            err(f"缺 {k}")
    if m.get("source") == "verified" and not m.get("verifiedAt"):
        err("source=verified 但沒有 verifiedAt")
    if errs:
        return errs

    want = list(dates(m["start"], m["end"]))
    got = [x.get("date") for x in t["days"]]
    if got != want:
        err(f"days 沒有逐日涵蓋 {m['start']}–{m['end']}：{got}")

    nights, prev_out = [], m["start"]
    for s in t["stays"]:
        if s["checkin"] != prev_out:
            err(f"stays 不連續：{prev_out} 之後接 {s['checkin']}")
        nights += list(dates(s["checkin"], s["checkout"]))[:-1]
        prev_out = s["checkout"]
    if prev_out != m["end"]:
        err(f"stays 最後退房 {prev_out}，應為 {m['end']}")

    hotels_file = d / "hotels.json"
    track_ids = ({h["id"] for h in json.loads(hotels_file.read_text(encoding="utf-8"))["hotels"]}
                 if hotels_file.exists() else None)
    for s in t["stays"]:
        for c in s["candidates"]:
            p = c.get("estLocalPerNight")
            if p is not None and not isinstance(p, (int, float)):
                err(f"{c.get('name')}：estLocalPerNight 要是數字或 null")
            if c.get("trackId") and track_ids is not None and c["trackId"] not in track_ids:
                err(f"{c.get('name')}：trackId {c['trackId']} 不在 hotels.json")

    for day in t["days"]:
        for leg in day.get("legs", []):
            opts = leg.get("options", [])
            recs = [o for o in opts if o.get("rec") is True]
            if opts and len(recs) != 1:
                err(f"{day['date']} {leg.get('leg')}：rec=true 應恰好 1 班，現在 {len(recs)}")
            for o in opts:
                if not isinstance(o.get("rec"), bool):
                    err(f"{day['date']} {leg.get('leg')} {o.get('dep')}：rec 要是 boolean")
                for k in ("no", "pf", "apf"):
                    if not isinstance(o.get(k, ""), str):
                        err(f"{day['date']} {leg.get('leg')} {o.get('dep')}：{k} 要是字串")

    for c in t["costs"]:
        if not isinstance(c.get("optional", False), bool):
            err(f"costs「{c.get('label')}」：optional 要是 boolean")
        if not isinstance(c.get("local"), (int, float)):
            err(f"costs「{c.get('label')}」：local 要是數字")

    idx = json.loads((TRIPS / "index.json").read_text(encoding="utf-8"))
    if slug not in {x["slug"] for x in idx}:
        err("trips/index.json 沒有這個 slug")
    return errs


def main():
    slugs = sys.argv[1:] or [x["slug"] for x in json.loads((TRIPS / "index.json").read_text(encoding="utf-8"))]
    bad = 0
    for s in slugs:
        errs = check(s)
        print(f"{'OK ' if not errs else 'NG '} {s}")
        for e in errs:
            print("   -", e)
        bad += bool(errs)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
