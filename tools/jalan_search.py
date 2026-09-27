"""じゃらん：車站周邊住宿一覽（帶日期與人數，含稅總價）。不需要 AI，約 15–30 秒。

  python tools/jalan_search.py 金沢駅 2026-11-21 2026-11-22 [--adults 2] [--rooms 1] [--limit 30]

流程：
  1. 車站名 → 車站代碼（先查 tools/jalan_codes.json 快取；沒有就用關鍵字搜尋找一間飯店，
     從它頁面的「〇〇駅」連結取得 /<都道府縣>/STA_<代碼>/）
  2. 開車站一覽頁（stayYear/Month/Day、stayCount、adultNum、roomCount），改成「料金が安い順」
  3. 每張卡片取：yad 編號、名稱、1 泊大人 N 名合計（含稅，最低方案）、評分、交通
輸出 JSON 陣列（stdout）。只讀公開搜尋結果，不登入、不訂房。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path

CODES = Path(__file__).resolve().parent / "jalan_codes.json"

STATION_JS = r"""(kw) => {
  const links = [...document.querySelectorAll('a')]
    .map(a => [a.getAttribute('href') || '', (a.innerText || '').trim()])
    .filter(([h]) => /^\/\d{6}\/STA_\d+\/$/.test(h));
  const exact = links.find(([, t]) => t === kw || t === kw + '駅');
  const fuzzy = links.find(([, t]) => t.length > 1 && kw.includes(t.replace(/駅$/, '')));
  return {exact: exact ? exact[0] : null, fuzzy: fuzzy ? fuzzy[0] : null};
}"""

CARDS_JS = r"""(kw) => [...document.querySelectorAll('a[href*="/yad"]')]
  .filter(a => /合計\(税込\)/.test(a.innerText || ''))
  .map(a => {
    const t = (a.innerText || '').replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0));
    const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
    const num = re => { const m = t.match(re); return m ? parseInt(m[1].replace(/,/g, ''), 10) : null; };
    const acc = (t.match(/【アクセス】\s*([^\n]+)/) || [])[1] || '';
    // 只看提到目標車站的那一句（「和倉温泉駅から送迎バス5分」不算金沢駅）：
    //   從車站步行＝「金沢駅…徒歩N分」且中間沒有巴士或開車；巴士＝「金沢駅…バス…N分」
    const base = kw.replace(/駅$/, '');
    const parts = acc.split(/[、。/／]/).filter(s => s.includes(base));
    const sw = parts.map(s => s.match(/駅(.{0,25}?)徒歩\s*約?\s*(\d+)\s*分/)).find(m => m && !/バス|車で|タクシー/.test(m[1]));
    const bus = parts.map(s => s.match(/バス.{0,20}?(\d+)\s*分/)).find(Boolean);
    return {
      yad: ((a.getAttribute('href') || '').match(/yad(\d+)/) || [])[1],
      name: lines[0],
      total: num(/合計\(税込\)\s*([\d,]+)円/),
      perPerson: num(/1名\s*([\d,]+)円/),
      rating: parseFloat((t.match(/\n(\d\.\d)\n/) || [])[1]) || null,
      access: acc.slice(0, 120),
      shared: /相部屋|ドミトリー|カプセル|バス・?トイレ共同|共同バス/.test(t),
      walkMin: (m => m ? parseInt(m[1], 10) : null)(t.match(/徒歩\s*約?\s*(\d+)\s*分/)),
      stationWalk: sw ? parseInt(sw[2], 10) : null,
      busMin: bus ? parseInt(bus[1], 10) : null,
    };
  })"""


def load_codes():
    try:
        return json.loads(CODES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def station_path(page, kw):
    codes = load_codes()
    if kw in codes:
        return codes[kw]
    q = urllib.parse.quote(kw.encode("shift_jis"))
    page.goto(f"https://www.jalan.net/uw/uwp2011/uww2011init.do?keyword={q}&distCd=06&rootCd=7701&screenId=FWPCTOP",
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    yads = page.evaluate(r"""() => [...new Set([...document.querySelectorAll('a')]
        .map(a => ((a.getAttribute('href') || '').match(/yadMap\('(\d+)'\)|\/yad(\d+)\//) || []).slice(1).find(Boolean))
        .filter(Boolean))]""")
    path = None
    for yad in yads[:4]:  # 優先名稱完全相同的車站，避免「金沢駅」被當成「北鉄金沢駅」
        page.goto(f"https://www.jalan.net/yad{yad}/", wait_until="domcontentloaded", timeout=45000)
        hit = page.evaluate(STATION_JS, kw)
        path = hit["exact"] or path or hit["fuzzy"]
        if hit["exact"]:
            break
    if not path:
        raise SystemExit(f"じゃらん找不到「{kw}」的車站代碼（試過 {len(yads[:4])} 間飯店）")
    codes[kw] = path
    CODES.write_text(json.dumps(codes, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def search(kw, checkin, checkout, adults=2, rooms=1, limit=30):
    from playwright.sync_api import sync_playwright

    d0, d1 = dt.date.fromisoformat(checkin), dt.date.fromisoformat(checkout)
    q = dict(stayYear=d0.year, stayMonth=d0.month, stayDay=d0.day, stayCount=(d1 - d0).days,
             roomCount=rooms, adultNum=adults)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo",
                                   viewport={"width": 1280, "height": 1800}).new_page()
        path = station_path(page, kw)
        url = f"https://www.jalan.net{path}?" + urllib.parse.urlencode(q)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        try:  # 改成「料金が安い順」；失敗就用預設排序
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                page.evaluate("changeSORT('1')")
            page.wait_for_timeout(1500)
        except Exception:
            pass
        cards = page.evaluate(CARDS_JS, kw)
        browser.close()
    seen, out = set(), []
    for c in cards:
        if c["yad"] and c["total"] and c["yad"] not in seen:
            seen.add(c["yad"])
            c["url"] = f"https://www.jalan.net/yad{c['yad']}/"
            out.append(c)
    return {"station": kw, "listUrl": url, "checkedAt": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "adults": adults, "rooms": rooms, "nights": q["stayCount"], "hotels": out[:limit]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("station")
    ap.add_argument("checkin")
    ap.add_argument("checkout")
    ap.add_argument("--adults", type=int, default=2)
    ap.add_argument("--rooms", type=int, default=1)
    ap.add_argument("--limit", type=int, default=30)
    a = ap.parse_args()
    r = search(a.station, a.checkin, a.checkout, a.adults, a.rooms, a.limit)
    sys.stdout.write(json.dumps(r, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
