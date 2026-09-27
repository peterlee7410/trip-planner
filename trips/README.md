# trips/<slug>/ 結構

slug 規則：小寫英數與連字號，`<地名>-<YYYY>-<MM>`，例如 `sapporo-2026-12`。

```
trips/<slug>/
  trip.json        行程本體（planner 讀取）— 下方結構
  config.json      機票追蹤條件（tracker.py 格式，見根目錄 config.json）
  hotels.json      飯店追蹤清單（hotel_tracker.py 格式，見根目錄 hotels.json；非日本可只填 booking）
  research/        agent 的查證筆記：flights.md、hotels.md、transit.md、local.md（含來源網址與查證日期）
  data/            GitHub Actions 寫入的 history.json、hotels.json（不要手動改）
```

完成後在 `trips/index.json` 加一筆：`{"slug":"sapporo-2026-12","title":"…","destination":"…","start":"2026-12-20","end":"2026-12-24"}`。
網頁：`https://peterlee7410.github.io/trip-planner/?trip=<slug>`
驗證：`python trips/validate.py <slug>`

價格由其他 repo 追蹤時（例如關西行程由 flight-tracker_1 追蹤），在 `meta.liveDataDir` 填對方 GitHub Pages 的 data 網址（`https://<帳號>.github.io/<repo>/data/`），planner 會讀那裡的即時價格，這個 repo 就不用放 config.json／hotels.json，避免重複查價與重複通知。

## trip.json

```jsonc
{
  "meta": {"destination":"北海道 札幌・小樽","origin":"TPE","start":"YYYY-MM-DD","end":"YYYY-MM-DD",
           "travelers":2,"budgetTwd":60000,"source":"verified","verifiedAt":"YYYY-MM-DD"},
  "title": "行程標題", "summary": "2–3 句",
  "currency": "JPY", "localToTwd": 0.21,
  "flights": {"arriveAirport":"CTS","departAirport":"CTS","estTwdTotal":26000,
              "advice":"建議組合與理由（實查價格＋查詢時間）","tips":["…"]},
  "passes": [{"name":"…","priceLocal":0,"covers":"…","verdict":"划算與否＋計算"}],
  "stays": [{"city":"札幌","checkin":"YYYY-MM-DD","checkout":"YYYY-MM-DD","area":"…","why":"…",
    "candidates":[{"name":"正式名稱","estLocalPerNight":12000,"note":"實查：方案/衛浴/接駁",
                   "trackId":"hotels.json 的 id（有追蹤才填）",
                   "agodaUrl":"https://www.agoda.com/zh-tw/<path>/hotel/<city>.html?checkIn=…&checkOut=…&los=1&rooms=1&adults=2&children=0&currencyCode=TWD",
                   "jalanUrl":"https://www.jalan.net/yadXXXXXX/","url":"官網"}]}],
  "days": [{"date":"YYYY-MM-DD","title":"…","base":"當晚住哪",
    "items":["依時間排序"],
    "legs":[{"leg":"A → B（路線）","src":"官方當日時刻表網址",
      "options":[{"dep":"08:38","arr":"10:39","train":"はしだて1号","no":"5081M","pf":"31","apf":"","rec":true,"note":"為何建議"}],
      "tip":"轉乘陷阱"}],
    "food":["…"],"tips":["寄物櫃/營業時間/票券"],"mapStops":["Google 地圖可搜的地名（當地語言）"],
    "map":[{"label":"地圖短名","sub":"09:00 重點","icon":"選填 emoji","query":"Google 地圖搜尋名","x":0,"y":0}],
    "pois":[{"type":"food|locker","label":"…","sub":"…","x":0,"y":0,"fx":0,"fy":0,"query":"…"}]}],
  "route": [{"label":"KIX","icon":"✈","date":"10/14","hot":false,"query":"選填"}],
  "costs": [{"cat":"交通|門票與活動|其他","label":"…","local":0,"note":"算法","optional":false}],
  "foodLevels": {"save":3500,"normal":5500,"loose":8000},
  "checklist": ["使用者自己要做的事"]
}
```

規則：
- `costs` 不含機票、住宿、餐費（網頁另外計算）。金額為全員合計的當地貨幣。
- `options` 每段恰好一個 `rec: true`；`no`、`pf`、`apf` 只填查證過的值，否則空字串。
- `rec`/`optional` 必須是 boolean，`pf`/`apf` 必須是字串（寫完用 Python assert 檢查）。

## 圖像化（選填）
- `route`：整趟路線 SVG 的節點（依序，含進出機場）。沒填時用「抵達機場 → 每段住宿城市 → 回程機場」自動產生。
- `days[].map`：每日路線 SVG 的站點。`x`、`y` 是 0–100 的手排座標（關西行程沿用 flight-tracker_1 的位置）；沒填座標就自動鋸齒排版。沒有 `map` 時改用 `mapStops`，時間從 `items` 對出來。
- `days[].pois`：🍜 美食／🧳 寄物的支線，`x,y` 是點的位置、`fx,fy` 是連回路線的點；只在手排座標時使用。
- 有每日地圖時，`items` 會收進「文字行程」摺疊區，畫面以圖為主。
- `days[].photos`：實景照片 `[{"name","src","page","artist","license"}]`，由 `python tools/wiki_photos.py --trip trips/<slug>` 或 `plan_trip.py` 自動產生。
  只用維基百科頁面代表圖（Wikimedia Commons，CC／公有領域，附作者與授權），頁面要有座標；日文／中文站名只查日文維基百科；查不到就不放。
  飯店、餐廳照片版權屬於業者，不嵌入，只在住宿卡片放「照片 ↗」連到じゃらん照片頁。
