# Jev SPY Paper Trader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 使用者已確認於本對話依序實作。Task 1–6 已完成；83 個離線測試通過，實測界線見 `docs/spy-paper-trader-verification.md`。下方保留原始執行步驟，完成證據以驗證報告及 `artifacts/trading-progress.md` 為準。

**Goal:** 建立可操作、可核對紀錄的中文 SPY 模擬交易工具，決策限定 `buy`、`sell`、`hold`。

**Architecture:** 在現有套件新增獨立 trading 模組。資料、決策、帳戶、持久化與本機 HTTP 服務分開；瀏覽器僅顯示與操作，所有憑證及模型呼叫在 Python 後端。保留原瀏覽器代理用途。

**Tech Stack:** Python >=3.12、標準函式庫、現有 httpx、原生 HTML/CSS/JavaScript、pytest、ruff、uv。

**Spec:** `docs/superpowers/specs/2026-09-25-spy-paper-trader-design.md`

## Global Constraints

- 初始資金 10,000 美元，每次最多交易一股，不借款、不放空。
- Jev 決策只有三種：`buy`、`sell`、`hold`，介面分別顯示「買入」、「賣出」、「持有」。API 決策值與紀錄統一使用小寫。
- 模型僅看到截至決策日的最近 20 筆日線與當時帳戶資料。
- 成交使用下一交易日開盤價加上設定的滑價與手續費。
- 手續費及滑價在開始時固定，預設每筆 1 美元及 1 個基點。
- 支援手動逐步操作；依使用者追加需求，提供每批 5 天的自動決策／成交、停止、進度及資產視覺化。仍不串券商、不自動下真實訂單。
- 自動化測試不呼叫付費 API。離線展示標明合成行情及規則模型，不能冒充 Jev 實測。
- 憑證留在伺服器；輸出位於 `artifacts/trading/`；不提交或推送，也不初始化 Git。
- 不需要 OpenAI API 或文字生成 helper。

## Review Focus

1. CSV 的 BOM、重複日期、NaN、Infinity 及過大檔案：讀取 UTF-8 BOM，其他不合法輸入拒絕；Task 1 與 5 測試。
2. 隔日跳空導致現金不足：保留 buy 決策但拒絕成交，不改寫模型結果；Task 2 測試。
3. 同時雙擊與過期頁面送出命令：同一版本僅生效一次，不重複呼叫或推進；Task 4 與 5 測試。
4. 紀錄寫入失敗與模型逾時：不繼續交易，不自動重送付費请求；Task 3 與 4 測試。
5. 模型回應與匯入文字含 HTML 或敏感內容：安全文字呈現、固定錯誤訊息、憑證不進匯出；Task 3 與 5 測試。

## File Map

- `jev_ultrafast/trading/__init__.py`：模組說明。
- `data.py`：Bar、Dataset、CSV 與 Alpha Vantage 資料。
- `engine.py`：Decimal 帳戶與不可變交易結果。
- `decision.py`：Jev HTTP 請求、回應驗證及離線規則。
- `storage.py`：原子寫入、執行識別碼、紀錄讀取與匯出。
- `session.py`：手動狀態機、版本鎖及執行紀錄。
- `server.py`：本機 HTTP API、設定與啟動入口。
- `static/index.html`、`static/app.js`、`static/style.css`：中文介面。
- `tests/test_trading_data.py`、`test_trading_engine.py`、`test_trading_decision.py`、`test_trading_session.py`、`test_trading_server.py`：離線測試。
- `pyproject.toml`、`.env.example`、`README.md`：新增指令與設定說明。

## Task 1: 行情資料

**Files:** 新增 `trading/__init__.py`、`trading/data.py`、`tests/test_trading_data.py`。

**Interfaces:** frozen `Bar(date: str, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int)`；frozen `Dataset(bars: tuple[Bar, ...], source: str, digest: str)`；`parse_csv(text: str) -> Dataset`、`demo_dataset() -> Dataset`、`download_spy(key: str, client: httpx.Client) -> Dataset`。

- [ ] 先加入失敗測試，確認行情順序、BOM 與非法數值。

```python
def test_nonfinite_csv_is_rejected():
    import pytest
    from jev_ultrafast.trading.data import parse_csv
    with pytest.raises(ValueError):
        parse_csv('date,open,high,low,close,volume\n2026-01-02,NaN,5,1,3,10\n')

def test_demo_is_labeled_and_ordered():
    from jev_ultrafast.trading.data import demo_dataset
    data = demo_dataset()
    assert len(data.bars) >= 21
    assert data.source == 'synthetic'
    assert list(data.bars) == sorted(data.bars, key=lambda b: b.date)
```

- [ ] 執行 `uv run pytest tests/test_trading_data.py -q`，確認缺少模組造成失敗。
- [ ] 實作固定 CSV 欄位 `date,open,high,low,close,volume`，使用 `csv.DictReader(text.lstrip('\ufeff').splitlines())`。日期用 `date.fromisoformat`；拒絕重複或非遞增日期；所有價格有限且大於零；`low <= open,close <= high`；成交量為非負整數；21 至 10,000 筆。使用正規化內容 SHA-256 作 digest。
- [ ] 內建 60 個工作日的確定性合成價格，source 固定 synthetic。Alpha Vantage 僅發送一次 GET 到官方 query URL，指定 SPY/daily/compact；將最新在前的 API 結果排序後驗證。限流、缺少時序及 HTTP 錯誤回傳固定文字，不輸出帶 key 的 URL。
- [ ] 使用 `httpx.MockTransport` 測試成功、429、API `Information` 訊息；補上 BOM、重複日期、Infinity、負成交量及 OHLC 不合理測試，重跑本檔。

## Task 2: 模擬成交引擎

**Files:** 新增 `trading/engine.py`、`tests/test_trading_engine.py`。

**Interfaces:** frozen `Account(cash: Decimal, shares: int)`；frozen `Costs(fee: Decimal, slippage_bps: Decimal)`；`settle(account, action: str, bar: Bar, costs) -> tuple[Account, dict]`；`equity(account, close: Decimal) -> Decimal`。輸出交易記錄包含 action、status、price、fee、quantity、reason。

- [ ] 寫出核心失敗案例。

```python
def test_hold_keeps_empty_position():
    from decimal import Decimal
    from jev_ultrafast.trading.data import demo_dataset
    from jev_ultrafast.trading.engine import Account, Costs, settle
    before = Account(Decimal('10000.00'), 0)
    after, record = settle(before, 'hold', demo_dataset().bars[20], Costs(Decimal('1'), Decimal('1')))
    assert after == before
    assert record['quantity'] == 0
```

- [ ] 執行 `uv run pytest tests/test_trading_engine.py -q`，確認失敗。
- [ ] 實作純函式，價格按買／賣方向套用 `open * (1 +/- slippage_bps / 10000)`；成交金額與現金使用 `quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)`，明訂先將成交價四捨五入再加扣費用；估值同樣到美分。費用限制 0–100 美元，滑價 0–100 基點，拒絕非有限值。
- [ ] 測試開盤 100、滑價 1 基點、費用 1 時：buy 扣款 101.01；sell 入款 98.99。另測試跳空不足、無股可賣、未知 action、成本高於賣出所得、hold 不收費、估值與原帳戶不可變。重跑本檔。

## Task 3: Jev 決策與離線規則

**Files:** 新增 `trading/decision.py`、`tests/test_trading_decision.py`；修改 `.env.example` 加入空白 `ALPHAVANTAGE_API_KEY=`。

**Interfaces:** `build_request(bars: tuple[Bar, ...], account: Account, model: str) -> dict`；`validate_answer(answer: dict) -> dict`；`JevDecider(key: str, model: str, client: httpx.Client).decide(request: dict) -> dict`；`RuleDecider.decide(request: dict) -> dict`。

- [ ] 核對 TypeSafe 官網實際連結與 Choice 規格；從 `https://docs.typesafe.ai/llms.txt` 找入口，對照現有 `model.py` 的 systemone body。無法確認時記錄差異，不猜新端點。
- [ ] 寫失敗測試：

```python
def test_only_three_lowercase_actions():
    import pytest
    from jev_ultrafast.trading.decision import validate_answer
    with pytest.raises(ValueError):
        validate_answer({'choice': 'BUY', 'probabilities': {'buy': 1, 'sell': 0, 'hold': 0}, 'confidence': 1})
```

- [ ] 執行 `uv run pytest tests/test_trading_decision.py -q` 確認失敗後實作。Body 沿官方 schema，固定單一 choice question，criteria 只有小寫三項；state 為行情與帳戶，不能含未來資料。請求 instructions 指明每日、一股、不借款、不放空、允許 hold；不要求生成文字理由。
- [ ] 驗證 probabilities 三鍵、各項及 confidence 為有限 0–1 數值（拒絕 bool）、機率和容差 0.02、choice 對應最大值。保留 request、raw response、model、usage、latency_ms，但不保存 HTTP headers 或 key。
- [ ] 每次 decide 僅一次 POST，timeout 25 秒；連線錯誤、非 JSON、格式錯誤回傳固定安全錯誤。規則模式比較最近五日收盤均價與最近收盤，上方 buy、下方且有持股 sell、其餘 hold，明確 model=`offline-rule`。
- [ ] MockTransport 測試 timeout、503、非法機率、NaN、空 answers、供應商錯誤含秘密字串；斷言無重試且對外錯誤不含秘密。重跑本檔。

## Task 4: 狀態機與可核對紀錄

**Files:** 新增 `trading/storage.py`、`trading/session.py`、`tests/test_trading_session.py`。

**Interfaces:** `Store(root: Path).write(run_id: str, state: dict) -> None`、`read(run_id) -> dict`、`list_runs() -> list[dict]`、`export_csv(run_id) -> str`；`Session(dataset: Dataset, decider, costs: Costs, store: Store)`，方法 `snapshot() -> dict`、`decide(expected_version: int) -> dict`、`advance(expected_version: int) -> dict`。

- [ ] 建立 SpyDecider 保存 request 並固定返回有效 buy；用 `tmp_path` 建立 Store。先測以下行為，執行 `uv run pytest tests/test_trading_session.py -q` 確認失敗。

```python
def assert_no_future(request, bars):
    sent = request['state']['bars']
    assert len(sent) == 20
    assert sent[-1]['date'] == bars[19].date
    assert bars[20].date not in str(request)
```

- [ ] 初始化 index=19、version=0、cash=10000、shares=0；JSON 金額用字串。状态分 ready、requesting、pending、finished、error；以鎖與 expected_version 防重入。decide 成功進 pending，advance 恰好前進一個交易日、套用 settle、收盤估值，最後一日進 finished。
- [ ] 每個 session 使用隨機十六進位 run_id；Store 僅接受固定格式 id。暫存檔寫入、flush/fsync、`os.replace` 原子替換。當前狀態先建立新副本、成功寫入後才替換記憶體狀態。
- [ ] 請求開始前持久化 requesting 和 attempts，才呼叫模型；回應記錄失敗時封鎖後續動作。模型錯誤不自動重試；同一日 requesting/error 不再提供執行按鈕，只可匯出或開新模擬。結束與失敗紀錄均可讀取，不自動恢復執行。
- [ ] 快取驗證後 dataset 及 source/digest；每次 run 保存起始設定、完整價格、逐步紀錄、actual API attempts 與離線決策數（分開計數）。匯出 CSV 欄位固定，文字欄以中和公式字首方式避免試算表公式執行。
- [ ] 測試未來資訊隔離、連續雙擊、并發相同版本、隔日跳空、最後一日不呼叫、hold、寫入開始失敗不呼叫模型、回應後寫入失敗不能交易、重啟讀取不呼叫 API、路徑穿越、CSV 公式字首。重跑 Task 1–4 測試。

## Task 5: 本機服务及中文介面

**Files:** 新增 `trading/server.py`、`trading/static/index.html`、`app.js`、`style.css`、`tests/test_trading_server.py`；修改 `pyproject.toml`。

**Interfaces:** `create_server(host='127.0.0.1', port=8767) -> ThreadingHTTPServer`、`main() -> None`。GET `/api/state`、`/api/runs`、`/api/export?id=<id>&format=json|csv`；POST `/api/start`、`/api/decide`、`/api/advance`。靜態資源以白名單服務。

- [ ] 用臨時目錄與 ephemeral port 啟動測試伺服器；先測外來 Host、Origin、缺少 token 回 403，無效 JSON 回 400，超過 2 MiB 回 413。執行 `uv run pytest tests/test_trading_server.py -q` 確認失敗。
- [ ] 使用 HTTP 標準庫，僅接受實際綁定位址的 Host；變更操作需要同源 Origin 與隨機 X-CSRF-Token，HTML 內注入 token（不是 API 金鑰）。JSON 禁止非有限數字。任何路徑與 provider URL 不由用戶提供。載入 .env 不覆蓋已存在環境，支援空行註解與引號，不輸出秘密。
- [ ] POST start 接受 `source: demo|alpha|csv`、`mode: rule|jev`、fee、slippage_bps 與 csv_text；拒絕 demo+jev，防合成行情被當實際 Jev 交易展示。啟動期間下載仅一次，已有 cache 可選使用。金鑰缺少回清楚錯誤，不降級。
- [ ] command 使用 run_id、expected_version 避免舊頁操作新帳戶；衝突回 409。快照包含日期、來源、模式、帳戶、equity_history、history、pending、error、model_calls、latency_ms 及 can_decide/can_advance。GET 不外洩伺服器環境。
- [ ] 中文單頁包含 SPY 標題、來源/模式、資料日期、費用輸入、新模擬、執行決策、推進日期、三項帳戶數值、資產 SVG 折線、紀錄表、請求/回應 details、歷史執行與匯出。提示合成資料、未計股息、模型機率非勝率。以 textContent 呈現所有動態字串。
- [ ] JS fetch 封裝檢查 HTTP 狀態，進行中停用操作按鈕，finally 恢復；衝突刷新快照且不自動重送。窄螢幕折疊欄位；鍵盤 focus 與 aria-live 顯示結果；SVG 座標依容器大小计算、空資料顯示文字。

```toml
[project.scripts]
jev = "jev_ultrafast.demo:main"
jev-trader = "jev_ultrafast.trading.server:main"
```

- [ ] HTTP 整合測試 start→decide→advance→export，核對交易及期末總值；測注入文字保留為文字、credentials 不在快照與 export、不同 run_id 命令拒絕。跑 `node --check jev_ultrafast/trading/static/app.js` 與 Task 5 測試。

## Task 6: 文件、整體驗證與交付

**Files:** 修改 `README.md`；新增 `docs/spy-paper-trader.md`；核對 `.env.example`、`pyproject.toml` 及打包結果。

- [ ] 在 README 加入簡短入口與命令 `uv run jev-trader`。獨立文件寫資料 CSV 格式、金鑰環境名稱、模式差別、操作流程、手續費/滑價/估值公式、無股息與公司行動限制、模型可能記憶歷史資料、錯誤不重試、紀錄位置與實際測試結果。引用上游 Jev Ultrafast 和 TypeSafe，不宣稱整個上游是個人實作。
- [ ] 執行完整驗證：

```powershell
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/trading/static/app.js
uv build
```

- [ ] 檢查 wheel 包含 trading 靜態資源；必要時在 hatch build 設定 include。以離線模式啟動本機服務，在瀏覽器檢查 1024px 與 390px 寬度、載入資料、buy/sell/hold、逐步成交、匯出、錯誤狀態及重新載入。
- [ ] 以獨立方式重算匯出中每筆現金變化及最終估值，不只檢查畫面顯示「完成」。UI 失敗修正後只重跑受影響測試及必要全套檢查。
- [ ] 真實 API 測試與離線測試分開報告。沒有金鑰或尚未實際呼叫時明確寫「真實 Jev/行情下載未驗證」，不替用戶建立憑證或要求貼秘密到對話。
- [ ] 交付啟動命令、使用入口、測試摘要和已知限制；不提交、不推送。

## Self-Review

- 規格映射：資料 Task 1；帳戶 Task 2；Jev Task 3；紀錄與狀態 Task 4；介面與安全 Task 5；文件及驗收 Task 6。
- 依賴方向：data → engine → decision → session/storage → server/UI，沒有循環 import 要求。
- 小寫 action 名稱、Decimal 金額、run_id 和 expected_version 在所有模組一致。
- 五項 Review Focus 均分配測試；離線證據不能替代真實模型呼叫。
- 執行方式：使用者已核准，由目前代理依序實作；另由獨立審查代理核對程式。
