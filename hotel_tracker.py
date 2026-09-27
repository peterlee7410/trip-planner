"""
飯店價格與空房追蹤器 — 不需要任何 AI / API 金鑰。

讀 hotels.json 的飯店清單，用無頭瀏覽器查：
  - じゃらん：指定日期的空房月曆與各方案含稅總價（主要來源，最穩定）
  - Booking.com：搜尋結果卡片的價格（輔助，失敗時略過）
結果寫入 data/hotels.json，追蹤頁會讀這個檔案顯示最新價、漲跌與空房狀態。
價格下跌、由客滿變成有房、或剩餘房間變少時，透過 Telegram 通知（需設定環境變數）。

用法:
    python hotel_tracker.py            # 查價並寫檔
    python hotel_tracker.py --dry-run  # 只印結果
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TPE_TZ = dt.timezone(dt.timedelta(hours=8))

JALAN_JS = """() => {
  const u = new URL(location.href);
  const iso = `${u.searchParams.get('stayYear')}-${String(u.searchParams.get('stayMonth')).padStart(2,'0')}-${String(u.searchParams.get('stayDay')).padStart(2,'0')}`;
  const cell = document.querySelector(`.calendar-day-${iso}`);
  const num = s => { const v = (s||'').match(/[\\d,]+(?=円)/g); return v ? parseInt(v[v.length-1].replace(/,/g,''),10) : null; };
  const plans = [];
  document.querySelectorAll('.p-planCassette').forEach(c => {
    const plan = (c.querySelector('.p-searchResultItem__catchPhrase')?.innerText||'').trim();
    const meal = (c.querySelector('.p-mealType__value')?.innerText||'').trim();
    c.querySelectorAll('tr.js-searchYadoRoomPlanCd').forEach(r => {
      const total = num(r.querySelector('.p-searchResultItem__totalCell')?.innerText);
      if (!total) return;
      plans.push({ plan, meal, total,
        room: (r.querySelector('.p-searchResultItem__planName')?.innerText||'').trim(),
        coupon: num(r.querySelector('.jlnpc-coupon-price__emphasizeText')?.innerText) || 0,
        noBath: /バスなし/.test(r.innerText||''),
        nonRefund: /返金不可/.test(plan) });
    });
  });
  return { calendar: cell ? cell.innerText.replace(/\\s+/g,' ').trim() : null,
           hasStock: cell ? /has-stock/.test(cell.className) : null, plans };
}"""

BOOKING_JS = """(match) => {
  const cards = [...document.querySelectorAll('[data-testid="property-card"]')];
  const card = cards.find(c => match.some(m => (c.querySelector('[data-testid="title"]')?.innerText||'').includes(m)));
  if (!card) return { found: false, cards: cards.length };
  const txt = card.innerText || '';
  const priceTxt = card.querySelector('[data-testid="price-and-discounted-price"]')?.innerText || '';
  const nums = priceTxt.match(/[\\d,]{3,}/g);
  return { found: true,
    title: card.querySelector('[data-testid="title"]')?.innerText || '',
    price: nums ? parseInt(nums[nums.length-1].replace(/,/g,''),10) : null,
    taxIncluded: /含稅費/.test(card.querySelector('[data-testid="taxes-and-charges"]')?.innerText||''),
    tax: card.querySelector('[data-testid="taxes-and-charges"]')?.innerText || '',
    unit: (card.querySelector('[data-testid="recommended-units"]')?.innerText||'').split('\\n')[0],
    soldOut: /無空房|已無法預訂/.test(txt) };
}"""

MEAL_KEYS = {"食事なし": "none", "朝のみ": "breakfast", "夕のみ": "dinner", "朝・夕": "both", "朝/夕あり": "both"}


def jalan_url(h, cfg):
    d = dt.date.fromisoformat(h["checkin"])
    nights = (dt.date.fromisoformat(h["checkout"]) - d).days
    q = dict(stayYear=d.year, stayMonth=d.month, stayDay=d.day, stayCount=nights,
             roomCount=cfg["rooms"], adultNum=cfg["adults"], yadNo=h["jalan"], roomCrack="200000")
    return f"https://www.jalan.net/yad{h['jalan']}/plan/?" + urllib.parse.urlencode(q)


def booking_url(h, cfg):
    q = dict(ss=h["booking"]["query"], checkin=h["checkin"], checkout=h["checkout"],
             group_adults=cfg["adults"], no_rooms=cfg["rooms"], group_children=0,
             selected_currency="TWD", lang="zh-tw")
    return "https://www.booking.com/searchresults.zh-tw.html?" + urllib.parse.urlencode(q)


def summarize_jalan(raw):
    plans = raw.get("plans") or []
    best = {}
    for p in plans:
        key = MEAL_KEYS.get(p["meal"], "other")
        if key not in best or p["total"] < best[key]["total"]:
            best[key] = {k: p[k] for k in ("total", "room", "plan", "coupon", "noBath", "nonRefund")}
    cal = raw.get("calendar") or ""
    rooms_left = None
    parts = cal.split()
    if len(parts) >= 3 and parts[2].startswith("部屋") and parts[1].isdigit():
        rooms_left = int(parts[1])
    available = bool(plans) and raw.get("hasStock") is not False
    return {
        "available": available,
        "calendar": cal,
        "roomsLeft": rooms_left,
        "min": min((p["total"] for p in plans), default=None),
        "best": best,
        "plans": len(plans),
    }


def scrape(cfg):
    from playwright.sync_api import sync_playwright

    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo",
                                  viewport={"width": 1280, "height": 1800})
        page = ctx.new_page()
        for h in cfg["hotels"]:
            res = {}
            if h.get("jalan"):
                try:
                    page.goto(jalan_url(h, cfg), wait_until="domcontentloaded", timeout=60000)
                    try:  # 空房月曆由 JS 載入，等指定日期的格子出現
                        page.wait_for_selector(f".calendar-day-{h['checkin']}", timeout=20000)
                    except Exception:
                        page.wait_for_selector(".p-planCassette", timeout=15000)
                    page.wait_for_timeout(1500)
                    res["jalan"] = summarize_jalan(page.evaluate(JALAN_JS))
                    res["jalan"]["url"] = jalan_url(h, cfg)
                except Exception as e:
                    res["jalan"] = {"error": str(e)[:200]}
                time.sleep(2)
            if h.get("booking"):
                try:
                    bpage = ctx.new_page()
                    bpage.set_extra_http_headers({"Accept-Language": "zh-TW,zh;q=0.9"})
                    bpage.goto(booking_url(h, cfg), wait_until="domcontentloaded", timeout=60000)
                    bpage.wait_for_selector('[data-testid="property-card"]', timeout=30000)
                    bpage.wait_for_timeout(2500)
                    res["booking"] = bpage.evaluate(BOOKING_JS, h["booking"]["match"])
                    res["booking"]["url"] = booking_url(h, cfg)
                    bpage.close()
                except Exception as e:
                    res["booking"] = {"error": str(e)[:200]}
                time.sleep(2)
            out[h["id"]] = res
            print(f"  {h['id']}: {json.dumps(brief(res), ensure_ascii=False)}")
        browser.close()
    return out


def brief(res):
    j, b = res.get("jalan") or {}, res.get("booking") or {}
    return {"jalan": j.get("error") or (j.get("min") if j.get("available") else f"客滿({j.get('calendar')})"),
            "booking": b.get("error") or (b.get("price") if b.get("found") and not b.get("soldOut") else "無房/未找到")}


def best_twd(cfg, res):
    """回傳這間飯店目前可訂的最低價（台幣）與來源。"""
    cands = []
    j = res.get("jalan") or {}
    if j.get("available") and j.get("min"):
        cands.append((round(j["min"] * cfg["jpy_to_twd"]), "じゃらん"))
    b = res.get("booking") or {}
    if b.get("found") and not b.get("soldOut") and b.get("price"):
        cands.append((b["price"], "Booking"))
    return min(cands) if cands else (None, None)


def alerts(cfg, cur, prev):
    msgs = []
    names = {h["id"]: h["nameZh"] for h in cfg["hotels"]}
    for hid, res in cur.items():
        now_twd, src = best_twd(cfg, res)
        before_twd, _ = best_twd(cfg, (prev or {}).get(hid, {})) if prev else (None, None)
        j, pj = res.get("jalan") or {}, ((prev or {}).get(hid) or {}).get("jalan") or {}
        if prev and now_twd and before_twd is None:
            msgs.append(f"🟢 {names[hid]} 出現空房：約 NT${now_twd:,}（{src}）")
        elif now_twd and before_twd and before_twd - now_twd >= cfg["alert_drop_jpy"] * cfg["jpy_to_twd"]:
            msgs.append(f"📉 {names[hid]} 降價：NT${before_twd:,} → NT${now_twd:,}（{src}）")
        elif prev and now_twd is None and before_twd:
            msgs.append(f"🔴 {names[hid]} 已客滿（上次約 NT${before_twd:,}）")
        if j.get("roomsLeft") == 1 and pj.get("roomsLeft", 9) != 1:
            msgs.append(f"⏳ {names[hid]} じゃらん只剩 1 間")
    return msgs


def notify(text):
    import urllib.request
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("（未設定 Telegram，略過推播）")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--trip", type=Path, help="行程資料夾（如 trips/sapporo-2026-12），讀它的 hotels.json、寫它的 data/")
    args, _ = ap.parse_known_args()
    global DATA
    base = ROOT
    if args.trip:
        base = args.trip if args.trip.is_absolute() else ROOT / args.trip
        DATA = base / "data"

    cfg = json.loads((base / "hotels.json").read_text(encoding="utf-8"))
    now = dt.datetime.now(TPE_TZ)
    last_night = max(h["checkin"] for h in cfg["hotels"])
    if now.date().isoformat() >= last_night:
        print("住宿日期已到，停止追蹤。")
        return

    print("查詢飯店價格…")
    cur = scrape(cfg)
    path = DATA / "hotels.json"
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    prev = hist[-1]["hotels"] if hist else None
    msgs = alerts(cfg, cur, prev)
    for m in msgs:
        print(m)
    if args.dry_run:
        return
    ok = any(not (r.get("jalan") or {}).get("error") for r in cur.values())
    if not ok:
        raise SystemExit("所有飯店都查詢失敗（可能被網站擋下），這次不寫入。")
    hist.append({"checkedAt": now.strftime("%Y-%m-%dT%H:%M"), "hotels": cur})
    hist = hist[-cfg["keep_history"]:]
    DATA.mkdir(exist_ok=True)
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    if msgs:
        notify("🏨 住宿價格變動\n" + "\n".join(msgs))


if __name__ == "__main__":
    main()
