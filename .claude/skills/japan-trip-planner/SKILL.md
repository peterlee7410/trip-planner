---
name: japan-trip-planner
description: 規劃日本自由行並建置零 API 費用的追蹤網站：機票／飯店動態比價提醒、每日行程地圖、JR 建議班次與月台、全程費用試算。說「規劃日本行程」「追蹤機票/飯店」「加電車時刻表」時使用。
---

# 日本自由行規劃＋自動追蹤網站

由 2026/10 關西行程（桃園⇄關西、大阪／天橋立／京都／奈良／宇治）整理而來。範本 repo：`peterlee7410/flight-tracker_1`（GitHub Pages：https://peterlee7410.github.io/flight-tracker_1/）。新旅程改在 `peterlee7410/trip-planner`（https://peterlee7410.github.io/trip-planner/）用 `/plan-trip` 建立 `trips/<slug>/`，不要再複製 flight-tracker_1。

## 核心原則

1. **執行時零 AI 費用**：網站是靜態頁（GitHub Pages），查價爬蟲在 GitHub Actions 定時跑（Python＋Playwright），通知走 Telegram。Claude 只在「規劃、研究、改版」時使用，不在追蹤迴圈裡呼叫任何 Claude API。
2. **絕不代訂**：不下單、不付款、不輸入密碼或卡號、不過 CAPTCHA。只提供帶日期與人數的預訂連結，由使用者自己完成。
3. **網站拒絕的來源不繞道**：WebFetch 因 robots 拒絕的頁面（Jorudan、ekitan 路線搜尋、NAVITIME）不改用 curl 或鏡像硬抓，改用下方可用來源。
4. **時刻表著作權**：JR おでかけネット禁止轉載時刻資料。每段只列 2–4 班重點車，再附官方當日完整時刻表連結，不要整張搬上網站。
5. **會變的資訊一律現查**：票價、房價、營業時間、活動日期、時刻表都要查當次資料，並在頁面標示查詢日期與「出發前再確認」。

## 工作流程（依序，每步完成就 commit）

1. **需求**：日期、人數、出發地與目的機場、行李（去程／回程托運數量）、預算上限、航班時間限制（例如「16:00 前抵達」「10:00 後起飛」）、可否紅眼、LCC／傳統航空組合。
2. **機票追蹤**：`tracker.py` 讀 Google Flights（zh-TW、TWD），解析 `li div[aria-label]`。篩時間限制，計入行李費，並比較 LCC 去＋傳統回的組合。寫入 `data/trip.json`、`data/history.json`。低於目標價或降價時發 Telegram。
3. **住宿**：每城市列 3–9 間候選，填入 `hotels.json`（id、city、checkin/checkout、jalan yad 編號、Agoda 路徑、照片、優缺點、接駁、浴室型態）。`hotel_tracker.py` 查じゃらん的空房月曆與各餐型最低總價，發出以下提醒：
   - 降價 ≥ ¥1,000
   - 由客滿變有房
   - 由有房變客滿
   - 只剩 1 間
4. **每日行程**：`trip.json` 的 `days[]` 放行程、住宿、路線、交通、車票、平價餐廳、寄物櫃。`index.html` 畫每日 SVG 小地圖（`dailyMaps`／`dailyPOIs`，座標 x×5、y×2.2，viewBox 500×220）、實景照片（Wikimedia Commons `Special:Redirect/file`）、轉乘細節。
5. **電車時刻表**：見下方 JR 做法。每段放在 `days[].trains[]`，並標示建議班次。
6. **費用試算**：`costItems`（cat、label、jpy、note、可選的 toggle）、`foodLevels`（省／一般／寬鬆）、住宿取「可訂最便宜」。頁面提供航班、每晚住宿、餐費等級、付費／免費選項、匯率的下拉切換，並畫堆疊長條圖加明細表。
7. **驗證與發佈**：本機起 `http.server`，用 Playwright 截 390px 與 1280px 的圖，確認沒有 JS 錯誤、沒有橫向捲動。接著 `git pull --rebase` 再 push（Actions 也會 commit，不可 force push），最後用瀏覽器開線上頁確認新內容已出現。

## 資料結構

```json
// data/trip.json（節錄）
{
  "days": [{
    "date": "2026-10-16", "place": "…", "stay": "…",
    "items": ["…"], "route": [], "transport": ["…"], "tickets": "…",
    "restaurants": [], "lockers": [],
    "trains": [{
      "leg": "京都 → 嵯峨嵐山",
      "src": "https://timetable.jr-odekake.net/station-timetable/2784055001?date=20261016",
      "options": [{"dep":"18:45","arr":"19:02","train":"普通","no":"261M","pf":"32","apf":"","rec":true,"note":"…"}],
      "tip": "…"
    }]
  }],
  "trainsNote": "時刻來源與查詢日期…",
  "costItems": [{"cat":"交通","label":"…","jpy":12000,"note":"…"}],
  "foodLevels": {"save":3500,"normal":5500,"loose":8000}
}
```

寫 `options` 時用具名欄位或 dict，不要用位置參數。之前曾把 `rec=True` 塞進 `apf`，畫面因此出現「到 true 號」。寫完用 assert 檢查型別：`rec` 必須是 bool，`pf` 和 `apf` 必須是字串。

## 來源手冊（實測可用與踩過的坑）

### 機票
- Google Flights 用無頭 Chromium、zh-TW 介面、TWD 幣別，讀 aria-label 即可。
- 排程：cron `47 0,12 * * *`（台北 08:47／20:47）。runner 用 `ubuntu-24.04`，搭配 `actions/checkout@v5`、`setup-python@v6`。
- 在 Spyder 執行時，如果已有 asyncio loop，要改在 thread 裡跑。沒有 `config.json` 時用內建預設值，參數解析用 `parse_known_args`。

### 飯店
- **じゃらん（主要來源）**：`https://www.jalan.net/yad{ID}/plan/?stayYear=&stayMonth=&stayDay=&stayCount=&roomCount=&adultNum=&yadNo=&roomCrack=200000`
  - 空房月曆 selector：`.calendar-day-{YYYY-MM-DD}`。
  - 方案 selector：`.p-planCassette`、`tr.js-searchYadoRoomPlanCd`、`.p-searchResultItem__totalCell`、`.p-mealType__value`。
  - 從 GitHub Actions 查詢很穩定。
- **Agoda（只做連結，不追蹤）**：自動化瀏覽時所有房型都顯示「已完售」，連淡季也一樣。頁首的「自 NT$…」是舊價格，不能當作可訂價。
  - 帶入日期與人數的連結格式：`https://www.agoda.com/zh-tw/{path}/hotel/{city}-jp.html?checkIn=YYYY-MM-DD&checkOut=YYYY-MM-DD&los=1&rooms=1&adults=2&children=0&currencyCode=TWD`。
  - 按鈕設成主要（紫色），じゃらん比價、Booking、官網、地圖排在後面。
- **Booking.com**：從 GitHub 常逾時，只當輔助來源；失敗時頁面顯示「待下次查價」。
- **住宿判斷重點**：
  - 共用衛浴（例：Sakura Terrace The Atelier）要標示清楚。
  - 溫泉旅館確認有沒有接駁、是否要預約、check-in 截止時間。
  - 同一間房比較素泊和含餐方案的價差。

### JR 西日本時刻（おでかけネット）
- **找車站方向代碼**：
  1. 在 `timetable.jr-odekake.net` 同源頁面裡 `fetch('/cgi-bin/mydia_sp.cgi?MD=3&FN=0&EID={站EID}')`。
  2. 用 regex 抓出 `link(\d+)` 和方向名稱，得到 station-timetable 代碼。
  3. 站 EID 可從 `jr-odekake.net/eki/timetable?id=` 搜尋取得。
- **當日站時刻表**：`/station-timetable/{代碼}?date=YYYYMMDD`（下拉選單約可選 6 週）。
  - 列：`tr.body-row`。第一格是小時；每個 `a[href*=train-timetable]` 是一班車，末端文字節點含分鐘、車種、行先。
- **列車詳細**：`/train-timetable/{id}?date=YYYYMMDD`。解析 `tr` 可得「列車番号」，以及各站的「着／発時刻、のりば」。
  - 月台只在部分車站顯示；沒有就寫「看站內看板」。
- **已知代碼（2026 年 10 月時刻）**：

  | 車站 | 方向 | 代碼 |
  |---|---|---|
  | 関西空港 | 往天王寺／大阪 | 3162069001 |
  | 天王寺 | 往京都（HARUKA） | 2993076002 |
  | 天王寺 | 往関空（阪和線） | 2993068001 |
  | 天王寺 | 往奈良（大和路線） | 2993062002 |
  | 京都 | 嵯峨野線往福知山 | 2784055001 |
  | 京都 | 往大阪／関空（HARUKA） | 2784076001 |
  | 京都 | 往奈良線 | 2784064001 |
  | 嵯峨嵐山 | 往京都 | 2875055002 |
  | 奈良 | 奈良線往京都 | 2981064001 |
  | 宇治 | 往京都 | 3017064001 |
  | 福知山 | 往京都 | 3208024001 |

  站 EID：天王寺 0620831、京都 0610116、嵯峨嵐山 0610704、奈良 0620816、宇治 0621609、福知山 0630719。
- **京都丹後鐵道區間**（例：天橋立）沒有 JR 站時刻表。從福知山站的時刻表反查直通特急即可拿到天橋立的發車時刻與列車番號。
- **私鐵與地鐵**（京阪、市營地鐵、Metro、阪神）：班次密，只列建議時段與頻率。ekitan 的 `/transit/section/sf-X/st-Y?dt=&tm=` 可以補查單班。

### 常見交通陷阱（寫進 tip）
- 京都 09:25 的特急是きのさき，只到福知山，不是はしだて。
- 関空快速和紀州路快速在日根野分割，要坐前 4 節「関西空港」車廂。
- 特急はしだて不停嵯峨嵐山。
- 京都站 HARUKA 在 30 號月台（西端），奈良線在 8–10 號（東南側），轉乘至少留 20–30 分。
- 注意平日、土休日時刻不同；時刻改正日（例：10/3）前查到的資料要重查。
- 大型活動日（花火大會）私鐵會臨時加開或改停靠，一律以當天官方公告為準，也要寫出散場後的分流車站。

### 活動與景點
- 夜間活動確認四項：期間、時段、最後入場時間、票價；也要分清現場購票和線上購票的差別（例：嵐山月灯路）。
- 花火大會：
  - 先列免費觀賞區，再列付費席，都要寫明規則，例如幾點起可佔位、哪些堤防禁止觀賞。
  - 附上建議抵達時間。
  - 附上備案地點。

## 回報格式
- 完成後一兩句說明改了什麼。
- 附一張「日期｜建議班次或價格｜月台或備註」的短表。
- 列出需要使用者自己處理的事，例如劃位、預約接駁、確認訂房。
- 最後附 Sources 清單。

## 不要做
- 不幫忙訂房、訂票、付款、登入。
- 不把 Agoda 自動化看到的「已完售」當成事實。
- 不整張轉載 JR 時刻表。
- 不在 GitHub Actions 或網站裡呼叫 Claude API（除非使用者明確要 AI 功能，且清楚知道要按量付費）。
