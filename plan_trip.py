"""快速規劃：10 分鐘內一定產生 trips/<slug>/trip.json。

流程（外部程式強制時間上限，AI 無法拖延）：
  1. 初稿（約 1–2 分）：一次 claude -p，不上網，用 planner 網頁同一份 SCHEMA 產生整份 trip.json。
  2. 平行查證（到期限前 1 分為止）：flights / stays / transit / local 四個 claude -p 同時跑，
     各自把修正寫到 research/<name>.patch.json；時間到就結束程序。
  3. 合併：逐一套用修正檔，套用後 trips/validate.py 不通過就退回該份；查不到的保留初稿並維持「估」。

用法：
  python plan_trip.py --slug kanazawa-2026-11 --dest 金澤 --start 2026-11-21 --end 2026-11-22 \
      [--travelers 2] [--budget 20000] [--origin TPE] [--notes "…"] [--deadline 540]
不 commit、不 push；完成後自己看 http://localhost:8000/?trip=<slug> 再決定。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRIPS = ROOT / "trips"
sys.path.insert(0, str(TRIPS))
import validate  # noqa: E402

CLAUDE = shutil.which("claude") or "claude"
T0 = time.monotonic()


def log(msg):
    print(f"[{time.monotonic() - T0:5.0f}s] {msg}", flush=True)


def run_claude(prompt, timeout, tools=(), cwd=ROOT):
    """跑一次 claude -p（prompt 走 stdin）；逾時就砍掉整個程序樹。回傳 (結果文字, 是否逾時)。"""
    cmd = [CLAUDE, "-p", "--output-format", "json", "--model", "sonnet",
           "--permission-mode", "acceptEdits",
           "--disallowedTools", "Bash(git commit:*)", "Bash(git push:*)", "Bash(git add:*)"]
    if tools:
        cmd += ["--allowedTools", *tools]
    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    try:
        out, _ = p.communicate(prompt, timeout=max(5, timeout))
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        p.communicate()
        return "", True
    try:
        return json.loads(out).get("result", ""), False
    except Exception:
        return out, False


def extract_json(text):
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1)
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1])


# ---------------------------------------------------------------- 1. 初稿

def schema():
    s = (ROOT / "src" / "template.html").read_text(encoding="utf-8")
    return re.search(r"const SCHEMA = `(.*?)`;", s, re.S).group(1)


def draft_prompt(a):
    days = (dt.date.fromisoformat(a.end) - dt.date.fromisoformat(a.start)).days + 1
    return f"""你是資深自由行規劃師。依下列條件產生完整旅程，用繁體中文（地名可附當地文字）。
不要使用任何工具。只回覆一個 JSON 物件，不要其他文字，格式如下：
{schema()}

條件：
- 目的地：{a.dest}
- 出發機場：{a.origin}；日期 {a.start} 出發、{a.end} 回程；共 {days} 天 {days - 1} 晚；{a.travelers} 人
- 總預算 NT${a.budget}（全員）
- 其他：{a.notes or "無"}

規則：
1. days 必須逐日涵蓋 {a.start} 到 {a.end}，第一天含機場進城，最後一天含回機場並預留國際線 3 小時。
2. stays 的日期要連續涵蓋每一晚（最後一段 checkout = {a.end}）；每段 2–3 間真實存在的候選。
3. legs 只放需要搭車的段落，每段 2–3 個 options，恰好一個 rec=true；no/pf/apf 不確定就留空字串。
4. 價格用一般水準估計，寧可略高；costs 不含機票、住宿、餐費；local 一律是數字，optional 一律是 boolean。
5. 字數精簡：items 每天 3–6 條、每條 40 字內；food 與 tips 各最多 3 條。
6. stays 每段另加 "station"：住宿區域最近的鐵路車站，用當地語言正式站名（例：金沢駅、京都駅），給住宿搜尋用。
7. flights.arriveAirport／departAirport 用 IATA 三碼，選 {a.origin} 有直飛、離目的地最方便的機場。
8. flights 另加 "airportOptions"：1–2 個 {a.origin} 有直飛的候選機場（第一個＝arriveAirport），每個
   {{"code":"IATA","groundMinutes":到住宿區的單程分鐘,"groundLocalPerPerson":單程每人當地幣,"groundRoute":"路線"}}；
   例如金澤可比較小松 KMQ（巴士 40 分）與關西 KIX（鐵路約 3 小時）。程式會實查兩邊機票，連同交通時間與費用一起比較。"""


def make_draft(a, d):
    text, to = run_claude(draft_prompt(a), timeout=a.draft_timeout)
    if to:
        raise SystemExit("初稿逾時")
    t = extract_json(text)
    t["meta"] = {"destination": a.dest, "origin": a.origin, "start": a.start, "end": a.end,
                 "travelers": a.travelers, "budgetTwd": a.budget, "source": "ai",
                 "generatedAt": dt.date.today().isoformat()}
    for day in t.get("days", []):
        for leg in day.get("legs", []):
            for o in leg.get("options", []):
                o["rec"] = bool(o.get("rec"))
                for k in ("no", "pf", "apf"):
                    o[k] = "" if o.get(k) is None else str(o[k])
    for c in t.get("costs", []):
        c["optional"] = bool(c.get("optional"))
    (d / "trip.json").write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return t


# ---------------------------------------------------------------- 2. 平行查證

COMMON = """你是「{role}」查證員。今天 {today}。旅程初稿在 trips/{slug}/trip.json（先讀它）。
硬性限制：
- 你只有 {minutes} 分鐘，時間到程序會被直接結束；最多 {calls} 次工具呼叫。先查最關鍵的，查不到就保留初稿值並在 note 標「估」。
- 第 {first} 次工具呼叫之前，就先把目前最好的結果寫進 trips/{slug}/research/{name}.patch.json，之後每查到新東西就覆寫一次。
- 不訂票、不登入；robots 拒絕的網站（Jorudan、NAVITIME、ekitan 路線搜尋）不要繞道；不要跑 tracker.py、hotel_tracker.py。
- 只寫 {name}.patch.json 這一個檔案，不要改 trip.json 或其他檔案。
修正檔格式（JSON，欄位規則見 trips/README.md）：
{fmt}
寫完最後一次修正檔就直接結束，回覆一句話即可。"""

SCOUTS = {
    "flights": ("機票", 8,
                '{"flights": {與 trip.json 的 flights 同結構，advice 寫實查到的航空公司、每週飛行日與時刻、查詢時間，另加 "outbound": {"airline":"","no":"","dep":"HH:MM","arr":"HH:MM"}, "return": {同上}（建議的去回程班機，旅遊當天星期幾有飛的）}, "sources": ["網址"]}',
                "查旅遊日期實際有飛的直飛航空公司、每週飛行日與時刻（航空公司官網或時刻表）。"
                "票價不用查：程式會另外用 Google Flights 實查並覆寫 estTwdTotal。"),
    "stays": ("住宿", 8,
              '{"stays": [與 trip.json 的 stays 同結構，每段 2–3 間，estLocalPerNight 用實查到的含稅價（每間每晚），note 寫方案與查詢時間，可加 jalanUrl/url], "sources": ["網址"]}',
              "每晚確認 2–3 間真實存在、交通方便的候選，查含稅價與空房；不要找 Agoda 網址。"),
    "transit": ("交通", 10,
                '{"legs": {"YYYY-MM-DD": [與 trip.json days[].legs 同結構的陣列，每段恰好一個 rec=true，src 放官方當日時刻表網址]}, "passes": [可省略], "sources": ["網址"]}',
                "依初稿每日行程，查主要移動段落（機場進出、城市間）旅遊當天的官方時刻；市區巴士地鐵只寫發車頻率。JR 西日本用 timetable.jr-odekake.net。"),
    "local": ("景點與活動", 8,
              '{"days": {"YYYY-MM-DD": {"items": [...], "food": [...], "tips": [...], "mapStops": [...]}}, "costs": [與 trip.json costs 同結構], "checklist": [...], "sources": ["網址"]}',
              "確認主要景點在旅遊日期的營業時間、門票、當週活動；不要大改每天去的城市，只修正時間、票價與順序。"),
}


def scout_prompt(name, a, minutes):
    role, calls, fmt, task = SCOUTS[name]
    return COMMON.format(role=role, today=dt.date.today().isoformat(), slug=a.slug, minutes=minutes,
                         calls=calls, first=max(3, calls // 2), name=name, fmt=fmt) + "\n任務：" + task


PICK_STAYS = """你是住宿挑選員。旅程初稿在 trips/{slug}/trip.json，每一晚的合格住宿清單在 trips/{slug}/research/jalan-pool-*.json
（程式已排除共用衛浴、膠囊與步行超過 20 分的郊外旅館，依價格排序；total＝整段住宿、{rooms} 間房、{adults} 位大人的含稅總價；
stationWalk＝從車站步行分鐘、busMin＝從車站搭巴士分鐘，細節看 access）。住宿預算約每晚 {cur} {per_night:,}（全員）。
不要上網，只讀這些檔案。{minutes} 分鐘內完成。
每段 stays 挑 2–3 間：第一間一定是清單最便宜的那間；其餘在最便宜那間 1.6 倍價格內，選地點或評分較好的；
note 寫出和預算的差距，以及和最便宜那間比好在哪裡。清單是空的那段，才從 research/jalan-<日期>.json 全清單挑。
寫 trips/{slug}/research/stays.patch.json：{{"stays": [與 trip.json stays 同結構；candidates 的 name 用清單名稱，
estLocalPerNight = total ÷ 晚數 ÷ 房數（整數），jalanUrl = 清單的 url，yad = 清單的 yad，
note = "じゃらん <checkedAt> 實查，含稅最低方案" 加上步行或交通重點]}}
寫完就結束，回覆一句話即可。"""


def airport_options(t):
    f = t.get("flights") or {}
    opts = [o for o in (f.get("airportOptions") or []) if re.fullmatch(r"[A-Z]{3}", str(o.get("code", "")).upper())]
    main = (f.get("arriveAirport") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", main) and main not in [str(o["code"]).upper() for o in opts]:
        opts.insert(0, {"code": main})
    return {str(o["code"]).upper(): o for o in opts[:2]}


def flight_config(a, dest):
    return {"trip_name": f"{a.origin} ⇄ {dest}", "origin": a.origin, "destination": dest,
            "depart_date": a.start, "return_date": a.end, "adults": a.travelers,
            "outbound_arrive_by": a.arrive_by, "return_depart_after": a.depart_after,
            "bags_outbound": a.bags, "bags_return": a.bags,
            "target_total": a.budget, "alert_below": int(a.budget * 0.7), "alert_drop": 500,
            "rt_max_candidates": 4}


def price_airport(a, d, code):
    """tracker.py 查一個機場，放在 research/fl-<代碼>/（選定後才複製成正式追蹤設定）。"""
    w = d / "research" / f"fl-{code}"
    w.mkdir(parents=True, exist_ok=True)
    (w / "config.json").write_text(json.dumps(flight_config(a, code), ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "tracker.py", "--trip", str(w)], cwd=ROOT, timeout=a.tool_timeout,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        log(f"機票實查 {code}：逾時")
        return None
    hist = w / "data" / "history.json"
    if r.returncode or not hist.exists():
        log(f"機票實查 {code}：沒有結果（{(r.stdout + r.stderr).strip().splitlines()[-1:] or '無輸出'}）")
        return None
    last = json.loads(hist.read_text(encoding="utf-8"))[-1]
    log(f"機票實查 {code}：{len(last['options'])} 個組合，最低 NT${last['options'][0]['totalEst']:,}")
    return last


def run_flight_prices(a, d, t):
    """每個候選機場平行跑 tracker.py（Google Flights）；回傳 {代碼: 最新一次結果}。"""
    info = airport_options(t)
    if not info:
        log("機票實查：初稿沒有有效的機場代碼，略過")
        return {}
    with cf.ThreadPoolExecutor(len(info)) as ex:
        res = dict(zip(info, ex.map(lambda c: price_airport(a, d, c), info)))
    return {c: r for c, r in res.items() if r}


def run_jalan(a, d, t):
    """每段住宿跑一次 tools/jalan_search.py；回傳成功的段數。"""
    if (t.get("currency") or "").upper() != "JPY":
        log("住宿實查：不是日本行程，改由 AI 查證")
        return 0
    sys.path.insert(0, str(ROOT / "tools"))
    import jalan_search
    rooms = max(1, -(-a.travelers // 2))
    ok = 0
    for s in t.get("stays", []):
        st = (s.get("station") or "").strip()
        if not st:
            continue
        try:
            r = jalan_search.search(st, s["checkin"], s["checkout"], a.travelers, rooms)
        except (Exception, SystemExit) as e:
            log(f"住宿實查 {st} {s['checkin']}：失敗（{str(e)[:80]}）")
            continue
        (d / "research" / f"jalan-{s['checkin']}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        pool = {**r, "hotels": hotel_pool(r["hotels"])[:12]}
        (d / "research" / f"jalan-pool-{s['checkin']}.json").write_text(
            json.dumps(pool, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log(f"住宿實查 {st} {s['checkin']}：{len(r['hotels'])} 間有空房，合格 {len(pool['hotels'])} 間")
        ok += 1
    return ok


def hotel_pool(hotels, max_walk=15, max_bus=25):
    """合格住宿：非共用衛浴，而且「從車站步行 max_walk 分內」或「搭巴士 max_bus 分內」；依價格排序。
    只寫車程的交流道旁、郊外溫泉會被排除。沒有 stationWalk 欄位的舊資料退回用 walkMin ≤ 20。"""
    def ok(h):
        if h.get("shared"):
            return False
        if "stationWalk" not in h:
            return h.get("walkMin") is not None and h["walkMin"] <= 20
        return ((h["stationWalk"] is not None and h["stationWalk"] <= max_walk)
                or (h.get("busMin") is not None and h["busMin"] <= max_bus))
    return sorted(filter(ok, hotels), key=lambda h: h["total"])


def pick_hotels(pool, n=3, cap=1.6):
    """一定包含最便宜的合格住宿；其餘在它 cap 倍價格內，評分高的優先。"""
    if not pool:
        return []
    cheapest = pool[0]
    rest = [h for h in pool[1:] if h["total"] <= cheapest["total"] * cap]
    rest.sort(key=lambda h: (-(h.get("rating") or 0), h["total"]))
    return [cheapest] + rest[:n - 1]


def auto_pick_stays(a, d, t):
    """住宿挑選員沒交出結果時，由程式依步行距離與價格挑。"""
    rooms = max(1, -(-a.travelers // 2))
    stays = []
    for s in t["stays"]:
        f = d / "research" / f"jalan-{s['checkin']}.json"
        if not f.exists():
            stays.append(s)
            continue
        r = json.loads(f.read_text(encoding="utf-8"))
        pick = pick_hotels(hotel_pool(r["hotels"])) or sorted(
            [h for h in r["hotels"] if not h["shared"]], key=lambda h: h["total"])[:3]
        stays.append({**s, "candidates": [{
            "name": h["name"], "estLocalPerNight": round(h["total"] / r["nights"] / rooms), "yad": h["yad"],
            "jalanUrl": h["url"],
            "note": f"じゃらん {r['checkedAt'].replace('T', ' ')} 實查，含稅最低方案"
                    + (f"；{s.get('station', '')}步行約 {h['stationWalk']} 分" if h.get("stationWalk")
                       else f"；{s.get('station', '')}搭巴士約 {h['busMin']} 分" if h.get("busMin") else "")}
            for h in pick] or s["candidates"]})
    return {"stays": stays}


def run_stays(a, d, t, deadline_at):
    if run_jalan(a, d, t):
        left = deadline_at - time.monotonic() - a.merge_reserve
        rooms = max(1, -(-a.travelers // 2))
        nights = max(1, (dt.date.fromisoformat(a.end) - dt.date.fromisoformat(a.start)).days)
        per_night = round(a.budget * a.hotel_share / nights / float(t.get("localToTwd") or 0.21))
        prompt = PICK_STAYS.format(slug=a.slug, rooms=rooms, adults=a.travelers, minutes=max(1, int(left // 60)),
                                   cur=t.get("currency", "JPY"), per_night=per_night)
        _, to = run_claude(prompt, left, ("Read", f"Write(trips/{a.slug}/research/*)"))
        pf = d / "research" / "stays.patch.json"
        if to or not pf.exists():
            pf.write_text(json.dumps(auto_pick_stays(a, d, t), ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
            return "挑選員逾時，改由程式挑選"
        return "完成（じゃらん實價）"
    left = deadline_at - time.monotonic() - a.merge_reserve
    tools = ("WebSearch", "WebFetch", "Read", f"Write(trips/{a.slug}/research/*)")
    _, to = run_claude(scout_prompt("stays", a, max(1, int(left // 60))), left, tools)
    return "逾時結束" if to else "完成"


def run_scouts(a, d, t, deadline_at):
    budget = deadline_at - time.monotonic() - a.merge_reserve
    minutes = max(1, int(budget // 60))
    tools = ("WebSearch", "WebFetch", "Read", f"Write(trips/{a.slug}/research/*)")
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(run_claude, scout_prompt(n, a, minutes), budget, tools): n
                for n in SCOUTS if n != "stays"}
        futs[ex.submit(run_stays, a, d, t, deadline_at)] = "stays"
        fprice = ex.submit(run_flight_prices, a, d, t)
        for f in cf.as_completed(futs):
            r = f.result()
            log(f"查證 {futs[f]}：{r if isinstance(r, str) else ('逾時結束' if r[1] else '完成')}")
        return fprice.result()


# ---------------------------------------------------------------- 3. 合併

def apply_patch(t, name, p):
    if name == "flights" and isinstance(p.get("flights"), dict):
        t["flights"] = {**t.get("flights", {}), **p["flights"]}
    elif name == "stays" and isinstance(p.get("stays"), list) and p["stays"]:
        t["stays"] = p["stays"]
    elif name == "transit" and isinstance(p.get("legs"), dict):
        for day in t["days"]:
            if isinstance(p["legs"].get(day["date"]), list):
                day["legs"] = p["legs"][day["date"]]
        if isinstance(p.get("passes"), list) and p["passes"]:
            t["passes"] = p["passes"]
    elif name == "local" and isinstance(p.get("days"), dict):
        for day in t["days"]:
            for k, v in (p["days"].get(day["date"]) or {}).items():
                if k in ("items", "food", "tips", "mapStops") and isinstance(v, list):
                    day[k] = v
        for k in ("costs", "checklist"):
            if isinstance(p.get(k), list) and p[k]:
                t[k] = p[k]
    else:
        return False
    return True


def merge(a, d):
    path = d / "trip.json"
    applied = []
    for name in SCOUTS:
        pf = d / "research" / f"{name}.patch.json"
        if not pf.exists():
            log(f"合併 {name}：沒有修正檔，保留初稿")
            continue
        try:
            patch = json.loads(pf.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"合併 {name}：修正檔不是完整 JSON（{e}），保留初稿")
            continue
        before = path.read_text(encoding="utf-8")
        t = json.loads(before)
        if not apply_patch(t, name, patch):
            log(f"合併 {name}：格式不符，保留初稿")
            continue
        path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        errs = validate.check(a.slug)
        if errs:
            path.write_text(before, encoding="utf-8")
            log(f"合併 {name}：套用後驗證失敗，退回（{errs[0]}）")
        else:
            applied.append(name)
            log(f"合併 {name}：已套用")
            if name == "stays" and all("估" in (c.get("note") or "") for st in patch["stays"] for c in st.get("candidates", [])):
                applied.remove(name)
                log("合併 stays：候選全部是估價，不算已查證")
            if name == "stays":
                write_hotel_tracking(a, d, path)
    t = json.loads(path.read_text(encoding="utf-8"))
    t["meta"]["verifiedSections"] = applied
    if len(applied) == len(SCOUTS):
        t["meta"]["source"], t["meta"]["verifiedAt"] = "verified", dt.date.today().isoformat()
    path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return applied


def write_hotel_tracking(a, d, path):
    """有じゃらん編號的候選寫進 hotels.json（hotel_tracker.py 格式），並在 trip.json 標 trackId。"""
    t = json.loads(path.read_text(encoding="utf-8"))
    hotels = []
    for s in t["stays"]:
        for c in s["candidates"]:
            yad = str(c.pop("yad", "") or (re.search(r"yad(\d+)", c.get("jalanUrl") or "") or [None, ""])[1])
            if not yad:
                continue
            hid = f"jalan-{yad}-{s['checkin']}"
            c["trackId"] = hid
            hotels.append({"id": hid, "nameZh": c["name"], "name": c["name"], "city": s.get("city", ""),
                           "checkin": s["checkin"], "checkout": s["checkout"], "jalan": yad})
    if not hotels:
        return
    (d / "hotels.json").write_text(json.dumps(
        {"adults": a.travelers, "rooms": max(1, -(-a.travelers // 2)), "jpy_to_twd": t.get("localToTwd", 0.21),
         "alert_drop_jpy": 1000, "keep_history": 120, "hotels": hotels}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def usable_hours(start, arr, end, dep, ground_min, day=(8, 22)):
    """在目的地可用的白天時數：落地 +1h 通關 + 進城時間，到起飛 −2h − 去機場時間；每天只算 day 區間。"""
    t = lambda d, hm: dt.datetime.fromisoformat(f"{d}T{hm}")
    ready = t(start, arr) + dt.timedelta(minutes=60 + ground_min)
    leave = t(end, dep) - dt.timedelta(minutes=120 + ground_min)
    total, cur = 0.0, dt.date.fromisoformat(start)
    while cur <= dt.date.fromisoformat(end):
        lo = max(ready, dt.datetime.combine(cur, dt.time(day[0])))
        hi = min(leave, dt.datetime.combine(cur, dt.time(day[1])))
        total += max(0.0, (hi - lo).total_seconds() / 3600)
        cur += dt.timedelta(days=1)
    return total


def feasible(start, arr, end, dep, ground_min, latest="23:00", earliest="07:00"):
    """到得了、回得去：落地 +1h 通關 + 進城要在 latest 前到住宿地；去機場要在 earliest 之後出發（末班／首班車）。"""
    t = lambda d, hm: dt.datetime.fromisoformat(f"{d}T{hm}")
    ready = t(start, arr) + dt.timedelta(minutes=60 + ground_min)
    leave = t(end, dep) - dt.timedelta(minutes=120 + ground_min)
    return ready <= t(start, latest) and leave >= t(end, earliest)


def choose_flight(by_airport, info, start, end, travelers, fx, hour_value, allow_redeye=False):
    """跨機場比較：分數 = 機票 + 機場⇄市區來回交通 − 可用時數 × 每小時價值（全員、台幣），越低越好。
    排除：到不了住宿地或回不了機場的班次（末班／首班車）；紅眼班（前一晚沒得睡，除非 allow_redeye）。
    全部都被排除時，保留分數最好的一班並在 warning 說明。"""
    every = [o for last in by_airport.values() for o in last.get("options", [])]
    skip_red = not allow_redeye and any("紅眼" not in (o.get("note") or "") for o in every)
    ok, fallback = None, None
    for code, last in by_airport.items():
        g = info.get(code) or {}
        gmin, glocal = int(g.get("groundMinutes") or 60), float(g.get("groundLocalPerPerson") or 0)
        ground_twd = round(glocal * travelers * 2 * fx)
        for o in last.get("options", []):
            if skip_red and "紅眼" in (o.get("note") or ""):
                continue
            m1 = re.search(r"\d\d:\d\d→(\d\d:\d\d)", o["out"])
            m2 = re.search(r"(\d\d:\d\d)→", o["ret"])
            if not (m1 and m2):
                continue
            hours = usable_hours(start, m1[1], end, m2[1], gmin)
            c = {"airport": code, "option": o, "hours": hours, "groundTwd": ground_twd, "groundMinutes": gmin,
                 "score": o["totalEst"] + ground_twd - hour_value * hours, "checkedAt": last["checkedAt"]}
            if feasible(start, m1[1], end, m2[1], gmin):
                ok = c if ok is None or c["score"] < ok["score"] else ok
            elif fallback is None or c["score"] < fallback["score"]:
                fallback = c
    if ok or not fallback:
        return ok
    fallback["warning"] = (f"沒有班次能在 23:00 前到住宿地並在 07:00 後出發去機場（地面交通約 {fallback['groundMinutes']} 分），"
                           "這班需要自行安排過夜或計程車")
    return fallback


def apply_flight_price(d, pick, compare=()):
    """用 choose_flight 選出的班機覆寫機票價格與建議（AI 查到的價格不採用）。compare：各機場最佳方案的說明行。"""
    path = d / "trip.json"
    t = json.loads(path.read_text(encoding="utf-8"))
    f = t.setdefault("flights", {})
    if not pick:
        f["advice"] = "票價未能實查，為估算。" + (f.get("advice") or "")
        path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return False
    best, code = pick["option"], pick["airport"]

    def leg(s, airline):
        m = re.match(r"(\S+)\s+(\d\d:\d\d)→(\d\d:\d\d)", s)
        return {"airline": airline, "no": "", "dep": m[2], "arr": m[3]} if m else {}

    parts = [p.strip() for p in best["airline"].split("＋")]
    f["arriveAirport"] = f["departAirport"] = code
    f["outbound"] = leg(best["out"], parts[0])
    f["return"] = leg(best["ret"], parts[-1])
    f["estTwdTotal"] = best["totalEst"]
    when = pick["checkedAt"].replace("T", " ")
    f["advice"] = (f"Google Flights {when} 實查，{code} 進出：{best['airline']} 去 {best['out']}／回 {best['ret']}，"
                   f"全員含行李約 NT${best['totalEst']:,}（{best['note'] or best['type']}）；"
                   f"在目的地可用約 {pick['hours']:.0f} 小時。"
                   + (f"⚠️ {pick['warning']}。" if pick.get("warning") else "")
                   + "".join(f"比較：{c}。" for c in compare)
                   + (f.get("advice") or ""))
    path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"機票：採用 {code} {best['airline']} NT${best['totalEst']:,}，可用 {pick['hours']:.1f} 小時")
    return True


def add_ground_cost(a, d, draft, pick):
    """選定機場和初稿主要機場不同時，補上機場⇄市區的交通費（初稿的 costs 是用原本機場估的）。"""
    main = (draft.get("flights", {}).get("arriveAirport") or "").upper()
    g = airport_options(draft).get(pick["airport"]) or {}
    if pick["airport"] == main or not g.get("groundLocalPerPerson"):
        return
    path = d / "trip.json"
    t = json.loads(path.read_text(encoding="utf-8"))
    t.setdefault("costs", []).append({
        "cat": "交通", "label": f"{pick['airport']}⇄市區來回（{g.get('groundRoute') or '機場交通'}）",
        "local": round(float(g["groundLocalPerPerson"]) * a.travelers * 2),
        "note": f"估；初稿以 {main} 估交通，改走 {pick['airport']} 後補上，請檢查是否與原有項目重複", "optional": False})
    path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def decide_flights(a, d, by_airport, draft):
    """跨機場挑班機；選定機場的 config 與查價紀錄複製到行程根目錄，Actions 之後就追蹤它。"""
    if not by_airport:
        return None, []
    info = airport_options(draft)
    fx = float(draft.get("localToTwd") or 0.21)
    pick = choose_flight(by_airport, info, a.start, a.end, a.travelers, fx, a.hour_value, a.allow_redeye)
    compare = []
    for code in by_airport:
        if code != pick["airport"]:
            alt = choose_flight({code: by_airport[code]}, info, a.start, a.end, a.travelers, fx, a.hour_value,
                                a.allow_redeye)
            if alt:
                compare.append(f"{code} 最佳為 {alt['option']['airline']} NT${alt['option']['totalEst']:,}"
                               f"＋地面交通 NT${alt['groundTwd']:,}，可用 {alt['hours']:.0f} 小時")
    src = d / "research" / f"fl-{pick['airport']}"
    shutil.copy(src / "config.json", d / "config.json")
    (d / "data").mkdir(exist_ok=True)
    shutil.copy(src / "data" / "history.json", d / "data" / "history.json")
    return pick, compare


RECONCILE = """下面是一份旅程 JSON，各段由不同人分別查證，可能互相矛盾。只檢查並修正：
1. 第一天機場進城的交通時刻要在去程班機抵達（flights.outbound.arr，若沒有就看 flights.advice）之後 45–90 分鐘內出發（入境與領行李至少 45 分）；
2. 最後一天去機場的交通要在回程班機起飛（flights.return.dep）前至少 2 小時抵達機場；
3. 第一天與最後一天 items 的時間要和上述一致，不要安排在飛機起飛後或抵達前的活動；
4. 機場段 legs 要用 flights.arriveAirport／departAirport 這個機場。如果原本寫的是別的機場，就改寫成這個機場到住宿區的一般路線
   （每段 2–3 個 options、恰好一個 rec=true，時刻依班距推估，note 標「估」）。
不要使用任何工具，不要改其他欄位。只回覆一個 JSON：{"days": {"YYYY-MM-DD": {"items": [...], "legs": [...]}}}，只放需要修改的日期（legs 格式同原本，每段恰好一個 rec=true）；都一致就回 {"days": {}}。
旅程 JSON：
"""


def reconcile(a, d, deadline_at):
    left = deadline_at - time.monotonic() - 5
    if left < 45:
        log("一致性整合：時間不夠，略過")
        return False
    path = d / "trip.json"
    before = path.read_text(encoding="utf-8")
    full = json.loads(before)  # 只給它需要比對的部分：航班＋第一天＋最後一天
    brief = {"flights": {k: full.get("flights", {}).get(k)
                         for k in ("arriveAirport", "departAirport", "outbound", "return", "advice")},
             "days": [full["days"][0]] + ([full["days"][-1]] if len(full["days"]) > 1 else [])}
    text, to = run_claude(RECONCILE + json.dumps(brief, ensure_ascii=False), timeout=left)
    if to:
        log("一致性整合：逾時，保留合併結果")
        return False
    try:
        fix = extract_json(text).get("days") or {}
    except Exception:
        log("一致性整合：回覆不是 JSON，保留合併結果")
        return False
    t = json.loads(before)
    for day in t["days"]:
        for k, v in (fix.get(day["date"]) or {}).items():
            if k in ("items", "legs") and isinstance(v, list):
                day[k] = v
    path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    errs = validate.check(a.slug)
    if errs:
        path.write_text(before, encoding="utf-8")
        log(f"一致性整合：修正後驗證失敗，退回（{errs[0]}）")
        return False
    log(f"一致性整合：修正 {list(fix) or '無（已一致）'}")
    return True


def update_index(a, t):
    ip = TRIPS / "index.json"
    idx = [x for x in json.loads(ip.read_text(encoding="utf-8")) if x["slug"] != a.slug]
    idx.append({"slug": a.slug, "title": t.get("title", a.dest), "destination": a.dest,
                "start": a.start, "end": a.end})
    ip.write_text(json.dumps(idx, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--travelers", type=int, default=2)
    ap.add_argument("--budget", type=int, default=60000)
    ap.add_argument("--origin", default="TPE")
    ap.add_argument("--notes", default="")
    ap.add_argument("--deadline", type=int, default=540, help="全部完成的秒數上限（預設 9 分）")
    ap.add_argument("--draft-timeout", type=int, default=180)
    ap.add_argument("--merge-reserve", type=int, default=90, help="留給合併與一致性整合的秒數")
    ap.add_argument("--tool-timeout", type=int, default=180, help="tracker.py 機票實查的秒數上限")
    ap.add_argument("--arrive-by", default="23:00", help="去程最晚抵達（預設放寬，交給評分挑）")
    ap.add_argument("--depart-after", default="06:00", help="回程最早起飛（預設放寬，交給評分挑）")
    ap.add_argument("--hour-value", type=int, default=800,
                    help="在目的地每多 1 小時值多少台幣（全員），用來和票價取捨")
    ap.add_argument("--hotel-share", type=float, default=0.35, help="總預算中住宿約占多少比例（給挑選員參考）")
    ap.add_argument("--allow-redeye", action="store_true", help="紅眼班也納入比較（預設排除，除非全部都是紅眼）")
    ap.add_argument("--bags", type=int, default=1, help="全員每段托運行李總件數（LCC 會加行李費）")
    ap.add_argument("--draft-only", action="store_true")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", a.slug):
        raise SystemExit("slug 只能有小寫英數與連字號")
    deadline_at = T0 + a.deadline
    d = TRIPS / a.slug
    (d / "research").mkdir(parents=True, exist_ok=True)

    log("產生初稿…")
    t = make_draft(a, d)
    update_index(a, t)
    errs = validate.check(a.slug)
    log(f"初稿完成：{len(t.get('days', []))} 天；驗證 {'OK' if not errs else '有問題：' + '；'.join(errs[:3])}")
    if a.draft_only:
        return

    log(f"平行查證（到 {a.deadline - a.merge_reserve}s 為止）…")
    by_airport = run_scouts(a, d, t, deadline_at)
    applied = merge(a, d)
    pick, compare = decide_flights(a, d, by_airport, t)
    if apply_flight_price(d, pick, compare):
        applied.append("flightPrice")
        add_ground_cost(a, d, t, pick)
    reconcile(a, d, deadline_at)
    t = json.loads((d / "trip.json").read_text(encoding="utf-8"))
    update_index(a, t)
    errs = validate.check(a.slug)
    log(f"完成：已查證 {applied or '無'}；驗證 {'OK' if not errs else '；'.join(errs[:3])}")
    log(f"預覽：python -m http.server 8000 → http://localhost:8000/?trip={a.slug}")


if __name__ == "__main__":
    main()
