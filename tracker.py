"""
機票價格追蹤器 — 不需要任何 AI / API 金鑰。

用無頭瀏覽器 (Playwright + Chromium) 讀取 Google 航班的公開搜尋結果，
依 config.json 的條件篩選、組合、加上行李估算後，把結果寫進 data/history.json，
index.html 會讀這個檔案畫出追蹤頁面。

用法:
    python tracker.py              # 正常查價並寫入 data/
    python tracker.py --fixture DIR # 離線測試：讀 DIR 內的 out.html / ret.html / rt.html
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"  # --trip trips/<slug> 會改成該資料夾
TPE_TZ = dt.timezone(dt.timedelta(hours=8))

# 找不到 config.json 時使用的預設值（與 config.json 內容相同）
DEFAULT_CONFIG = {
    "trip_name": "桃園 ⇄ 大阪關西",
    "origin": "TPE", "destination": "KIX",
    "depart_date": "2026-10-14", "return_date": "2026-10-19", "adults": 2,
    "outbound_arrive_by": "16:00", "return_depart_after": "10:00", "direct_only": True,
    "bags_outbound": 1, "bags_return": 2,
    "target_total": 30000, "alert_below": 21000, "alert_drop": 800, "alert_days_before": 7,
    "keep_top": 15,
    "lcc_bag_fee": {"樂桃": 930, "虎航": 850, "捷星": 1000, "泰越捷": 1000, "越捷": 1000,
                    "亞洲航空": 1000, "酷航": 1000, "德威": 1000, "真航空": 1000, "濟州": 1000,
                    "獅子": 1000, "香港快運": 1000},
}


def load_config() -> dict:
    path = CONFIG_PATH
    if not path.exists():
        print("（找不到 config.json，使用程式內建的預設條件）")
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **json.loads(path.read_text(encoding="utf-8"))}

# ---------------------------------------------------------------- parsing

TIME_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})\s*(凌晨|清晨|上午|中午|下午|晚上)(\d{1,2}):(\d{2})")
PRICE_RE = re.compile(r"(\d[\d,]*)\s*新台幣")
AIRLINE_RE = re.compile(r"搭乘(.+?)的(?:直達)?航班")


def to_24h(period: str, hour: int, minute: int) -> str:
    if period in ("凌晨", "清晨", "上午"):
        hour = 0 if hour == 12 else hour
    elif period == "中午":  # 中午11:xx / 12:xx / 1:xx
        hour = hour if hour >= 11 else hour + 12
    else:  # 下午 / 晚上
        hour = hour if hour == 12 else hour + 12
    return f"{hour:02d}:{minute:02d}"


def parse_label(label: str) -> dict | None:
    """把 Google 航班結果的 aria-label 轉成結構化資料。"""
    if "新台幣" not in label or "商務艙" in label or "頭等艙" in label:
        return None
    price = PRICE_RE.search(label)
    airline = AIRLINE_RE.search(label)
    times = TIME_RE.findall(label)
    if not (price and airline and len(times) >= 2):
        return None
    (dm, dd, dp, dh, dmin), (am, ad, ap, ah, amin) = times[0], times[-1]
    return {
        "airline": airline.group(1).replace("航空公司", "航空").strip(),
        "price": int(price.group(1).replace(",", "")),
        "dep": to_24h(dp, int(dh), int(dmin)),
        "arr": to_24h(ap, int(ah), int(amin)),
        "dep_date": f"{int(dm)}/{int(dd)}",
        "arr_date": f"{int(am)}/{int(ad)}",
        "direct": "直達" in label,
        "label": label,
    }


def dedupe(flights: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in flights:
        key = (f["airline"], f["dep"], f["arr"], f["price"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out

# ---------------------------------------------------------------- rules


def lcc_fee(cfg: dict, airline: str) -> int | None:
    for key, fee in cfg["lcc_bag_fee"].items():
        if key in airline:
            return fee
    return None


def short(airline: str) -> str:
    return (airline.replace("日本航空", "").replace("航空", "").replace("台灣", "")
            .replace("臺灣", "").replace(" X", "X").strip() or airline)


def md(date_str: str) -> str:
    d = dt.date.fromisoformat(date_str)
    return f"{d.month}/{d.day}"


def ok_outbound(cfg, f) -> bool:
    return (f["arr_date"] == md(cfg["depart_date"]) and f["arr"] <= cfg["outbound_arrive_by"]
            and (f["direct"] or not cfg["direct_only"]))


def ok_return(cfg, f) -> bool:
    return (f["dep_date"] == md(cfg["return_date"]) and f["dep"] >= cfg["return_depart_after"]
            and (f["direct"] or not cfg["direct_only"]))


def make_option(cfg, out, ret, fare, source, note=""):
    fo, fr = lcc_fee(cfg, out["airline"]), lcc_fee(cfg, ret["airline"])
    bags = (fo or 0) * cfg["bags_outbound"] + (fr or 0) * cfg["bags_return"]
    kind = "LCC" if fo is not None and fr is not None else "FSC" if fo is None and fr is None else "MIX"
    if kind == "MIX" and fo is None:
        kind = "MIX2"  # 傳統去、廉航回
    same = out["airline"] == ret["airline"]
    name = out["airline"] if same else f"{short(out['airline'])} ＋ {short(ret['airline'])}"
    notes = [n for n in [note, "紅眼" if out["dep"] < "06:00" else "", "" if same else "兩張單程"] if n]
    return {
        "airline": name,
        "type": kind,
        "out": f"{out['dep_date']} {out['dep']}→{out['arr']}",
        "ret": f"{ret['dep_date']} {ret['dep']}→{ret['arr']}",
        "outArr": out["arr"],
        "retDep": ret["dep"],
        "outDepDate": out["dep_date"],
        "outArrDate": out["arr_date"],
        "retDepDate": ret["dep_date"],
        "retArrDate": ret["arr_date"],
        "fare2pax": fare,
        "bagsEst": bags,
        "totalEst": fare + bags,
        "source": source,
        "checkUrl": urls(cfg)["rt"],
        "note": "；".join(notes),
    }


def build_options(cfg, outs, rets, rt_pairs) -> list[dict]:
    outs = [f for f in outs if ok_outbound(cfg, f)]
    rets = [f for f in rets if ok_return(cfg, f)]
    opts = {}

    def put(o):
        key = (o["out"], o["ret"], o["airline"])
        if key not in opts or o["totalEst"] < opts[key]["totalEst"]:
            opts[key] = o

    for out, ret in itertools.product(outs, rets):
        put(make_option(cfg, out, ret, out["price"] + ret["price"], "Google 航班"))
    for out, ret in rt_pairs:  # 來回套票：ret["price"] 是整趟來回總價
        if ok_outbound(cfg, out) and ok_return(cfg, ret):
            put(make_option(cfg, out, ret, ret["price"], "Google 航班", "來回套票"))

    ranked = sorted(opts.values(), key=lambda o: o["totalEst"])
    # 保留整體最便宜的 N 筆，並確保每一類 (LCC / MIX / FSC) 至少有最便宜的 3 筆
    keep = ranked[: cfg["keep_top"]]
    for kind in ("MIX", "FSC", "MIX2"):
        extra = [o for o in ranked if o["type"] == kind][:3]
        keep += [o for o in extra if o not in keep]
    return sorted(keep, key=lambda o: o["totalEst"])

# ---------------------------------------------------------------- browsing


def gf_url(q: str) -> str:
    return ("https://www.google.com/travel/flights/search?q=" + urllib.parse.quote(q)
            + "&curr=TWD&hl=zh-TW&gl=TW")


def urls(cfg):
    o, d, a = cfg["origin"], cfg["destination"], cfg["adults"]
    return {
        "out": gf_url(f"One way flights to {d} from {o} on {cfg['depart_date']} {a} adults"),
        "ret": gf_url(f"One way flights to {o} from {d} on {cfg['return_date']} {a} adults"),
        # 來回先試「<代碼> airport」：「Flights to KMQ from TPE」會被 Google 當成昆明 KMG；
        # 但 KIX 反而要用原本的寫法才載得出來，所以兩種都留，由 header_ok 檢查解析結果
        "rt": gf_url(f"Flights to {d} airport from {o} on {cfg['depart_date']} through {cfg['return_date']} {a} adults"),
        "rt_alt": gf_url(f"Flights to {d} from {o} on {cfg['depart_date']} through {cfg['return_date']} {a} adults"),
    }


def header_ok(text: str, origin: str, dest: str) -> bool:
    """搜尋列（「航班搜尋 來回 2 經濟艙 臺北市 小松市 KMQ …」）要出現目的地代碼，避免查到別的機場。"""
    i = text.find("航班搜尋")
    return i >= 0 and re.search(rf"\b{re.escape(dest)}\b", text[i:i + 80]) is not None


def collect(load):
    """依序載入 out／ret／rt 三頁；單頁失敗（逾時、查不到）只記錄，不讓其他頁的結果一起作廢。"""
    got = {}
    for key in ("out", "ret", "rt"):
        try:
            got[key] = load(key)
        except Exception as e:
            print(f"  {key} 頁失敗：{str(e).splitlines()[0][:120]}", file=sys.stderr)
            got[key] = []
    if not any(got.values()):
        raise SystemExit("Google Flights 三個查詢頁都失敗（頁面格式或網路問題）。")
    return got


READ_LABELS = """() => [...new Set([...document.querySelectorAll('li div[aria-label]')]
    .map(e => e.getAttribute('aria-label')).filter(t => t && t.includes('新台幣')))]"""

EXPAND = """() => { const b=[...document.querySelectorAll('button')]
    .find(x => /更多航班/.test(x.innerText||'')); if (b) { b.click(); return true } return false }"""


def load_results(page, url: str, cfg: dict | None = None) -> list[str]:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    accept_consent(page)
    page.wait_for_selector("li div[aria-label*='新台幣']", timeout=30000)
    page.wait_for_timeout(1500)
    if cfg:
        head = page.evaluate("() => document.body.innerText.replace(/\\s+/g, ' ')")
        if not header_ok(head, cfg["origin"], cfg["destination"]):
            i = head.find("航班搜尋")
            raise RuntimeError(f"Google 把目的地解析錯了：{head[i:i + 40]}")
    if page.evaluate(EXPAND):
        page.wait_for_timeout(2500)
    return page.evaluate(READ_LABELS)


def accept_consent(page):
    """歐洲 IP 可能出現 Google 同意畫面；選『全部拒絕』(僅必要 Cookie) 後繼續。"""
    if "consent.google" in page.url:
        for text in ("全部拒絕", "Reject all"):
            btn = page.get_by_role("button", name=text)
            if btn.count():
                btn.first.click()
                page.wait_for_load_state("domcontentloaded")
                break


def returns_for(page, rt_url: str, out_label: str) -> list[dict]:
    page.goto(rt_url, wait_until="domcontentloaded", timeout=60000)
    accept_consent(page)
    page.wait_for_selector("li div[aria-label*='新台幣']", timeout=30000)
    page.wait_for_timeout(1500)
    page.evaluate(EXPAND)
    page.wait_for_timeout(1500)
    clicked = page.evaluate(
        """(lbl) => { const el=[...document.querySelectorAll('li div[aria-label]')]
            .find(e => e.getAttribute('aria-label') === lbl); if (el) { el.click(); return true } return false }""",
        out_label)
    if not clicked:
        return []
    # 點了去程後清單會換成回程：等到清單裡不再有原本那個去程航班（不寫死機場名稱）
    page.wait_for_function(
        """(lbl) => { const l=[...document.querySelectorAll("li div[aria-label*='新台幣']")].map(e => e.getAttribute('aria-label'));
                      return l.length > 0 && !l.includes(lbl) }""",
        arg=out_label, timeout=30000)
    page.wait_for_timeout(1500)
    return [f for f in map(parse_label, page.evaluate(READ_LABELS)) if f]


def scrape(cfg, fixture: Path | None):
    """Spyder / Jupyter 內部已有 asyncio 迴圈，Playwright 同步 API 不能直接用，改在背景執行緒跑。"""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _scrape(cfg, fixture)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(_scrape, cfg, fixture).result()


def _scrape(cfg, fixture: Path | None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("尚未安裝 Playwright。請在命令提示字元執行：\n"
                         "  python -m pip install playwright\n"
                         "  python -m playwright install chromium\n"
                         "（在 Spyder 可於 IPython 主控台輸入 %pip install playwright，"
                         "再輸入 !python -m playwright install chromium）")

    u = urls(cfg)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="zh-TW", timezone_id="Asia/Taipei",
                                  viewport={"width": 1280, "height": 1800})
        page = ctx.new_page()
        if fixture:
            get = lambda name: (page.goto((fixture / f"{name}.html").as_uri()), page.evaluate(READ_LABELS))[1]
            outs = [f for f in map(parse_label, get("out")) if f]
            rets = [f for f in map(parse_label, get("ret")) if f]
            rt_out = [f for f in map(parse_label, get("rt")) if f]
            rt_pairs = []
            for f in dedupe(rt_out):
                fx = fixture / f"rt_{f['dep'].replace(':', '')}.html"
                if ok_outbound(cfg, f) and fx.exists():
                    page.goto(fx.as_uri())
                    rt_pairs += [(f, r) for r in map(parse_label, page.evaluate(READ_LABELS)) if r]
        else:
            rt_used = {}  # 來回頁實際成功的網址，選回程時要用同一個

            def load(key):
                if key != "out":
                    time.sleep(2)
                tries = [u["rt"], u["rt_alt"]] if key == "rt" else [u[key], u[key]]  # 單程：同網址重試一次
                for i, url in enumerate(tries):
                    try:
                        labels = load_results(page, url, cfg)
                        rt_used[key] = url
                        return labels
                    except Exception as e:
                        if i == len(tries) - 1:
                            raise
                        print(f"  {key} 頁重試：{str(e).splitlines()[0][:80]}", file=sys.stderr)
                        time.sleep(2)

            got = collect(load)
            outs = [f for f in map(parse_label, got["out"]) if f]
            rets = [f for f in map(parse_label, got["ret"]) if f]
            rt_out = [f for f in map(parse_label, got["rt"]) if f]
            rt_pairs = []
            candidates = [f for f in dedupe(rt_out) if ok_outbound(cfg, f)]
            for f in candidates[:cfg.get("rt_max_candidates", 10)]:  # 每個要重開來回頁，快速規劃時調低
                try:
                    rt_pairs += [(f, r) for r in returns_for(page, rt_used.get("rt", u["rt"]), f["label"])]
                except Exception as e:  # 單一航班失敗不影響整體
                    print(f"  略過 {f['airline']} {f['dep']}: {e}", file=sys.stderr)
                time.sleep(2)
        browser.close()
    return dedupe(outs), dedupe(rets), rt_pairs

# ---------------------------------------------------------------- output & alerts


def load_history() -> list[dict]:
    path = DATA / "history.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def summarize(cfg, options, prev_best):
    best = options[0]
    non_red = next((o for o in options if "紅眼" not in o["note"]), None)
    mix = next((o for o in options if o["type"] == "MIX"), None)
    fmt = lambda n: f"NT${n:,}"
    parts = [f"最低 {best['airline']} {best['out']} / {best['ret']}，約 {fmt(best['totalEst'])}"]
    if non_red and non_red is not best:
        parts.append(f"非紅眼最低 {non_red['airline']} 約 {fmt(non_red['totalEst'])}")
    if mix:
        parts.append(f"去廉航回傳統最低 {mix['airline']} 約 {fmt(mix['totalEst'])}")
    if prev_best is not None:
        d = best["totalEst"] - prev_best
        parts.append("與上次持平" if d == 0 else f"比上次{'漲' if d > 0 else '降'} {fmt(abs(d))}")
    parts.append("在目標內" if best["totalEst"] <= cfg["target_total"] else "高於目標")
    return "；".join(parts) + "。"


def should_alert(cfg, best, prev_best, now) -> bool:
    days_left = (dt.date.fromisoformat(cfg["depart_date"]) - now.date()).days
    return (best <= cfg["alert_below"]
            or (prev_best is not None and prev_best - best >= cfg["alert_drop"])
            or (0 <= days_left <= cfg["alert_days_before"]))


def notify(text: str):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("（未設定 Telegram，略過推播）")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, help="離線測試資料夾")
    ap.add_argument("--dry-run", action="store_true", help="只印結果，不寫檔、不推播")
    ap.add_argument("--trip", type=Path, help="行程資料夾（如 trips/sapporo-2026-12），讀它的 config.json、寫它的 data/")
    args, _ = ap.parse_known_args()  # Spyder 會多塞參數，忽略即可
    if args.trip:
        global DATA, CONFIG_PATH
        base = args.trip if args.trip.is_absolute() else ROOT / args.trip
        DATA, CONFIG_PATH = base / "data", base / "config.json"

    cfg = load_config()
    now = dt.datetime.now(TPE_TZ)
    if now.date() >= dt.date.fromisoformat(cfg["depart_date"]):
        print("行程已出發，停止追蹤。")
        return

    outs, rets, rt_pairs = scrape(cfg, args.fixture.resolve() if args.fixture else None)
    print(f"去程 {len(outs)} 筆、回程 {len(rets)} 筆、來回套票 {len(rt_pairs)} 筆")
    options = build_options(cfg, outs, rets, rt_pairs)
    if not options:
        raise SystemExit("沒有符合條件的航班，或頁面格式改變（請看上方筆數）。")

    history = load_history()
    prev_best = history[-1]["options"][0]["totalEst"] if history else None
    check = {
        "checkedAt": now.strftime("%Y-%m-%dT%H:%M"),
        "summary": summarize(cfg, options, prev_best),
        "options": options,
    }
    print(check["summary"])
    for o in options:
        print(f"  {o['totalEst']:>7,}  {o['type']:<4} {o['airline']:<14} {o['out']}  {o['ret']}  {o['note']}")
    if args.dry_run:
        return

    DATA.mkdir(exist_ok=True)
    history.append(check)
    (DATA / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")

    if should_alert(cfg, options[0]["totalEst"], prev_best, now):
        notify("⚠️ 建議現在下訂\n" + check["summary"])
    elif os.getenv("NOTIFY_EVERY_RUN") == "1":
        notify(check["summary"])


if __name__ == "__main__":
    main()
