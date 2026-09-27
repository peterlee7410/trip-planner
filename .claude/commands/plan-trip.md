---
description: 實查並建立一趟旅程的完整規劃與零 API 追蹤網站。用法：/plan-trip 目的地 出發日 回程日 [人數] [其他條件]
argument-hint: 札幌・小樽 2026-12-20 2026-12-24 2人 預算6萬 去程16點前到
---

要規劃的旅程：$ARGUMENTS

請依下列流程完成（先讀 `CLAUDE.md` 與 `trips/README.md`）：

1. **解析條件**：目的地、日期、人數（預設 2）、出發機場（預設 TPE）、預算、抵達/起飛時間限制、行李、興趣、已訂項目。缺少的關鍵條件（目的地或日期）才問我；其他用預設值並在最後列出你採用的假設。
2. **建立資料夾**：slug = `<英文地名>-<YYYY>-<MM>`，建 `trips/<slug>/research/`。如果我在 planner 網頁產生過初稿（trip.json），我會提供，當作起點。
3. **平行研究**：同時啟動 `flight-scout`、`hotel-scout`、`local-scout`；`local-scout` 完成後（有逐日地點）再啟動 `transit-planner`。每個 agent 的研究筆記寫在 `trips/<slug>/research/`。
4. **整合上線**：啟動 `trip-builder` 產生 `trip.json`、驗證、更新 `trips/index.json`、預覽、commit 並 push。
5. **獨立查核**：另開一個 general-purpose agent，只給它 `trip.json` 與 `research/`，抽查 5 項最關鍵事實（航班時刻、住宿價格與空房、主要車次、活動日期、門票），回報不一致處；有錯就修正後再 push。
6. **回報**（繁體中文、精簡）：網站網址、總費用與每人費用、預算差額、每日「建議班次｜月台」短表、我要自己做的事（訂票、劃位、預約接駁），以及 Sources。

規則：不代訂、不付款、不登入；robots 拒絕的網站不繞道；時刻表只列重點班次＋官方連結；GitHub Actions 不加任何 AI 呼叫。
