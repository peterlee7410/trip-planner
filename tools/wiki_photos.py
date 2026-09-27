"""景點實景照片：維基百科頁面代表圖（Wikimedia Commons，自由授權），不需要 AI、不需要金鑰。

  python tools/wiki_photos.py 兼六園 ひがし茶屋街          # 查單一地點
  python tools/wiki_photos.py --trip trips/<slug>          # 替 trip.json 每天的站點加上 photos

規則（寧可不放，也不放錯）：
- 用站點的 query（當地語言）或 label 當維基百科標題（ja → en），跟隨重新導向；
- 頁面要有座標（排除人名、列車名、城市總覽等非單一地點），
  而且代表圖要在 Commons 上、授權是 CC 或公有領域；
- 同一趟裡離其他照片中位位置超過 max_km 的丟掉（避免同名的外地景點）；
- 每張附作者、授權與 Commons 頁面連結（CC 授權要求標示）。
查詢結果快取在 tools/wiki_photos_cache.json。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "wiki_photos_cache.json"
UA = "trip-planner/0.1 (https://peterlee7410.github.io/trip-planner/)"  # Wikimedia 要求可識別的 User-Agent
FREE = re.compile(r"^(CC|Public domain|PD)", re.I)


def http_fetch(url, params):
    q = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _page(fetch, lang, title):
    r = fetch(f"https://{lang}.wikipedia.org/w/api.php",
              {"action": "query", "prop": "pageimages|coordinates", "piprop": "name",
               "redirects": 1, "titles": title})
    return next(iter(r.get("query", {}).get("pages", {}).values()), {})


def _file(fetch, name, width):
    r = fetch("https://commons.wikimedia.org/w/api.php",
              {"action": "query", "prop": "imageinfo", "iiprop": "url|extmetadata",
               "iiurlwidth": width, "titles": f"File:{name}"})
    page = next(iter(r.get("query", {}).get("pages", {}).values()), {})
    return (page.get("imageinfo") or [None])[0]


def lookup(title, fetch=http_fetch, cache=None, width=640):
    """回傳 {name, src, page, artist, license, lat, lon}；找不到合格照片回傳 None。"""
    title = re.sub(r"\s*[（(].*$", "", str(title)).strip()
    if not title:
        return None
    if cache is not None and title in cache:
        return cache[title]
    found = None
    # 日文／中文站名只查 ja：en 會把「京都」導到 Kyoto 城市頁（代表圖是金閣寺），和轉車站無關
    langs = ("ja",) if re.search(r"[぀-ヿ㐀-鿿]", title) else ("ja", "en")
    for lang in langs:
        try:
            p = _page(fetch, lang, title)
        except Exception:
            continue
        coords = p.get("coordinates") or []
        if not (p.get("pageimage") and coords):
            continue
        try:
            info = _file(fetch, p["pageimage"], width)
        except Exception:
            info = None
        meta = (info or {}).get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not (info and FREE.match(lic)):
            continue
        artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
        found = {"name": title, "src": info.get("thumburl") or info.get("url"), "page": info.get("descriptionurl"),
                 "artist": artist[:80] or "Wikimedia Commons", "license": lic,
                 "lat": coords[0]["lat"], "lon": coords[0]["lon"]}
        break
    if cache is not None:
        cache[title] = found
    return found


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def stop_names(day):
    stops = day.get("map") or []
    names = [(s.get("query") or s.get("label")) if isinstance(s, dict) else s[0] for s in stops]
    return [n for n in (names or day.get("mapStops") or []) if n]


def annotate(trip, fetch=http_fetch, cache=None, max_km=300, per_day=4, workers=6):
    """每天加 photos（最多 per_day 張），同一趟離中位位置太遠的丟掉。回傳加了幾張。"""
    names = {n for d in trip.get("days", []) for n in stop_names(d)}
    with ThreadPoolExecutor(workers) as ex:
        found = dict(zip(names, ex.map(lambda n: lookup(n, fetch, cache), names)))
    ok, ok_src = [p for p in found.values() if p], set()
    if ok:
        mid = {"lat": statistics.median(p["lat"] for p in ok), "lon": statistics.median(p["lon"] for p in ok)}
        ok_src = {p["src"] for p in ok if _km(p, mid) <= max_km}
    total, seen = 0, set()  # 同一張照片整趟只出現一次（住宿地每天都是站點，不要每天重複）
    for d in trip.get("days", []):
        photos = []
        for n in stop_names(d):
            p = found.get(n)
            if p and p["src"] in ok_src and p["src"] not in seen:
                seen.add(p["src"])
                photos.append({k: p[k] for k in ("name", "src", "page", "artist", "license")})
        d["photos"] = photos[:per_day]
        total += len(d["photos"])
    return total


def load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache):
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--trip", type=Path)
    a = ap.parse_args()
    cache = load_cache()
    if a.trip:
        path = a.trip / "trip.json"
        t = json.loads(path.read_text(encoding="utf-8"))
        n = annotate(t, cache=cache)
        path.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{a.trip}：加上 {n} 張照片")
    for name in a.names:
        print(name, "→", json.dumps(lookup(name, cache=cache), ensure_ascii=False))
    save_cache(cache)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
