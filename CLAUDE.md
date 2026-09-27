# trip-planner — 旅程產生器與零 API 追蹤

網站：https://peterlee7410.github.io/trip-planner/（首頁就是旅程產生器；`?trip=<slug>` 顯示實查行程與即時價格）

這個 repo 是從 `peterlee7410/flight-tracker_1` 獨立出來的通用旅程系統。
**flight-tracker_1（2026/10 關西追蹤站）不要去改**：它自己的 index.html、data/、Actions 照常運作；這裡的關西行程只透過 `meta.liveDataDir` 讀它 Pages 上的價格。

## 架構原則（不可違反）
- **執行時零 AI 費用**：GitHub Actions 只跑 `tracker.py`、`hotel_tracker.py`（Python＋Playwright），網站是 GitHub Pages 靜態頁。不要在 Actions、網站或腳本中呼叫 Claude API、不要加入 `ANTHROPIC_API_KEY`。
- **不代訂**：不下單、不付款、不登入、不輸入卡號或密碼、不過 CAPTCHA。只提供帶日期與人數的預訂連結。
- **尊重網站限制**：WebFetch 因 robots 拒絕的網站（Jorudan、ekitan 路線搜尋、NAVITIME 等）不改用 curl/Playwright 硬抓；改用官方來源或 ekitan `/transit/section/`、JR おでかけネット 等可用頁面。
- **時刻表著作權**：JR 等官方時刻表禁止轉載。每段只列 2–4 班重點車＋官方當日時刻表連結（`src`）。
- **會變的資訊一律現查**並記錄查證日期；估算值要在 note 標明「估」。

## 目錄
| 路徑 | 用途 |
|---|---|
| `index.html`、`artifact.html` | 由 `python build.py` 從 `src/template.html`＋`src/example.json` 產生，**不要直接改** |
| `trips/index.json` | 已建立行程清單 `[{slug,title,destination,start,end}]` |
| `trips/<slug>/` | `trip.json`、`config.json`、`hotels.json`、`research/*.md`、`data/`（Actions 寫入） |
| `trips/README.md` | **trip.json 結構定義（必讀）** |
| `trips/validate.py` | trip.json 檢查：`python trips/validate.py [slug]` |
| `tracker.py --trip trips/<slug>` | Google Flights 查價；讀 `<trip>/config.json`，寫 `<trip>/data/history.json` |
| `hotel_tracker.py --trip trips/<slug>` | じゃらん（主）＋Booking（輔）查房價；讀 `<trip>/hotels.json`，寫 `<trip>/data/hotels.json` |
| `.github/workflows/track.yml` | 每天 09:17／21:17（台北）查所有有 config.json／hotels.json 的 `trips/*/`；沒有就直接結束 |
| `.claude/commands/plan-trip.md`、`.claude/agents/` | `/plan-trip` 與 5 個分工 agent |
| `agent_server.py`、`windows/start_agent.bat` | **本機 agent**：開 http://localhost:8787 就是產生器，按「產生行程」在這台電腦跑 plan_trip.py（訂閱額度、只收本機連線、同時只跑一趟）；結果在 trips/，不 commit/push |
| `plan_trip.py` | **快速規劃（10 分內）**：初稿 → 4 路平行查證（外部 timeout）＋ `tracker.py` 機票實查＋`tools/jalan_search.py` 住宿實價 → 程式合併 → 一致性整合。不 commit／push |
| `tools/jalan_search.py` | じゃらん車站周邊一覽（含稅總價、從車站步行／巴士分鐘）；車站代碼快取在 `tools/jalan_codes.json` |
| `tools/wiki_photos.py` | 景點實景照片（維基百科代表圖，Commons 自由授權、附作者）；`--trip trips/<slug>` 替每天加 photos；快取 `tools/wiki_photos_cache.json` |

`plan_trip.py` 的挑選規則（改規則要同步改 `tests/test_plan_trip.py`）：
- 航班：初稿給 1–2 個候選機場＋地面交通；每個機場各跑 tracker.py。排除到不了住宿地（落地+1h+交通 > 23:00）或回不了機場（< 07:00 出發）的班次、預設排除紅眼班（`--allow-redeye` 開放）；其餘以「機票＋地面交通 − 可用白天時數 × `--hour-value`（預設 NT$800／小時，全員）」最低者勝出。
- 住宿：程式先過濾成合格清單（非共用衛浴，且從車站步行 ≤15 分或搭巴士 ≤25 分），第一間一定是清單最便宜的，其餘在它 1.6 倍內。

## 開發慣例
- Windows 上用 `py` 或 `python`；終端機中文亂碼時先設 `PYTHONIOENCODING=utf-8`。
- 測試：`python -m pytest tests`、`python tracker.py --fixture tests/fixtures --dry-run`（應印出 15 筆）、`python trips/validate.py`。
- 改網頁：改 `src/` → `python build.py` → `python -m http.server 8000` 開 `http://localhost:8000/?trip=<slug>`，檢查無 JS 錯誤、390px 手機寬度沒有橫向捲動。
- Git：先 `git pull --rebase` 再 push（Actions 也會 commit）；禁止 force push。
- 回報格式：一兩句說明改了什麼＋「日期｜建議｜備註」短表＋使用者要自己處理的事（劃位、預約）＋Sources。
