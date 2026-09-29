# SPY 歷史樣本

`spy.csv` 為 Yahoo Finance 公開行情快照：2026-06-25 至 2026-09-24，共 64 筆 SPY／USD 日線。

來源：<https://finance.yahoo.com/quote/SPY/history/>；實際請求 URL、下載時間與驗證雜湊見 `spy-source.json`。
使用 chart 回應的 `indicators.quote` 開高低收與成交量，保留回傳小數，未使用 `adjclose`；日期由交易日時間戳轉換。
排除下載當天與缺值日，經專案 CSV／OHLCV 驗證。此檔不是即時行情，不會自動更新。

原始下載回應留在本機忽略的 `artifacts/trading/spy-yahoo-source.json`，並未把合成資料標成真實價格。
