# 旅程產生器

https://peterlee7410.github.io/trip-planner/

- **產生初稿**：填目的地、日期、人數 → 「產生提示詞」→ 貼到 Claude → 把回覆的 JSON 貼回來。結果是 AI 估算，每項附查證連結。
- **實查行程**：在 Claude Code 執行 `/plan-trip 目的地 出發日 回程日 人數 條件`，agent 會實查機票、住宿、官方時刻表，建立 `trips/<slug>/`，網址是 `?trip=<slug>`。
- **自動查價**：GitHub Actions 每天兩次查 `trips/*/` 的機票與飯店，價格變動時用 Telegram 通知（需在 repo Settings → Secrets 設定 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`）。網站運作時不使用任何 AI。

已建立的行程：

| 行程 | 網址 | 價格來源 |
|---|---|---|
| 關西 6 天（2026-10-14～19） | [?trip=kansai-2026-10](https://peterlee7410.github.io/trip-planner/?trip=kansai-2026-10) | flight-tracker_1 的追蹤資料 |

規則與結構見 `CLAUDE.md`、`trips/README.md`。
