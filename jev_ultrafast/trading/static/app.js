"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="csrf-token"]').content;
const labels = {buy: "買入", sell: "賣出", hold: "持有"};
const sources = {"yahoo-finance": "Yahoo Finance 日線", "market-cache": "股票行情快取", synthetic: "合成示例", "yahoo-snapshot": "Yahoo Finance 真實歷史樣本", "alpha-vantage": "Alpha Vantage", "alpha-cache": "行情快取", csv: "匯入 CSV（來源由使用者提供）"};
const phases = {ready: "等待決策", requesting: "處理中", pending: "等待推進", finished: "重播完成", error: "已停止"};
const fills = {filled: "已成交", rejected: "已拒絕", held: "持有", pending: "待推進"};
let current = null;
let busy = false;
let replay;
let configuration = {jev: false, alpha: false};
let initialized = false;
let cachedSymbols = [];
const selectedSymbol = () => $("symbol").value.trim().toUpperCase().replaceAll(".", "-");
let pricePage = 0;
const usd = value => Number(value).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = "message" + (error ? " error" : "");
  $("message").hidden = !text;
}
async function api(path, body) {
  const options = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": token}, body: JSON.stringify(body)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作失敗，請重新整理。");
  return result;
}
function controls() {
  const locked = busy || replay?.isRunning();
  const differentMode = current && ($("mode").value !== current.mode || selectedSymbol() !== (current.symbol || "SPY") || $("start-at").value !== (current.start_at || "replay"));
  $("start").disabled = locked;
  $("decide").disabled = locked || differentMode || !current?.can_decide;
  $("advance").disabled = locked || differentMode || !current?.can_advance;
  $("auto-start").disabled = locked || differentMode || current?.start_at === "latest" || !(current?.can_decide || current?.can_advance);
  $("auto-stop").disabled = !replay?.isRunning();
  $("open-records").disabled = !current;
  for (const id of ["source", "mode", "symbol", "start-at", "fee", "slippage", "csv-file"]) $(id).disabled = locked;
}
function cell(row, value) { const td = document.createElement("td"); td.textContent = value; row.append(td); return td; }
function exportUrl(id, format) { return `/api/export?id=${encodeURIComponent(id)}&format=${format}`; }
function render(data) {
  if (current?.run_id !== data.session?.run_id) { $("decision-select").value = "latest"; pricePage = 0; }
  current = data.session;
  configuration = data.configured;
  cachedSymbols = data.cached_symbols || (data.cached ? ["SPY"] : []);
  if (!initialized) {
    initialized = true;
    if (!current) $("mode").value = configuration.jev ? "jev" : "rule";
    if (current) {
      $("mode").value = current.mode;
      $("symbol").value = current.symbol || "SPY";
      $("start-at").value = current.start_at || "replay";
      $("source").value = {"yahoo-finance": "yahoo", "market-cache": "cache", synthetic: "demo", "yahoo-snapshot": "sample", "alpha-vantage": "alpha", "alpha-cache": "cache", csv: "csv"}[current.source];
    }
    updateSource();
  }
  $("credentials").textContent = `Jev 金鑰：${data.configured.jev ? "已設定" : "未設定"} · 行情金鑰：${data.configured.alpha ? "已設定" : "未設定"}`;
  updateSource();
  $("empty").hidden = !!current;
  $("workspace").hidden = !current;
  $("mode-summary").textContent = !current ? "內附 SPY 真實樣本，可先用離線規則。" : `${current.symbol || "SPY"} · ${current.start_at === "latest" ? "最新行情判斷" : "歷史重播"} · ${sources[current.source]} · ${current.mode === "jev" ? "Jev API" : "離線規則，未呼叫 Jev"}`;
  controls();
  if (!current) return;
  const s = current;
  $("tab-prices").textContent = `${s.symbol || "SPY"} 行情`;
  $("price-title").textContent = `${s.symbol || "SPY"} 行情紀錄 · USD`;
  if (s.start_at === "latest") $("auto-status").textContent = "最新行情判斷：請點執行決策；自動執行僅適用歷史重播。";
  $("auto-note").textContent = s.start_at === "latest" ? "最新行情判斷 · 每次一筆；歷史重播才可自動執行。" : s.mode === "jev" ? "每批最多 5 次付費請求；停止會等目前操作完成。" : "離線規則 · 不呼叫 Jev";
  $("data-range").textContent = `${s.bars[0].date} — ${s.bars.at(-1).date} · ${s.bars.length} 個交易日`;
  $("source-badge").textContent = sources[s.source] || s.source;
  $("mode-badge").textContent = s.mode === "jev" ? "Jev 真實 API" : "離線規則模型";
  $("data-notice").textContent = s.source === "synthetic" ? "這是合成行情與規則模型的流程展示，不是真實行情或 Jev 實測。" : ["yahoo-snapshot", "yahoo-finance", "market-cache"].includes(s.source) ? "歷史日線（快取以記錄日期為準），使用 quote OHLC，未使用 adjclose。模擬不計股息，非即時行情。" : "未調整行情／未計股息的模擬結果；若期間包含拆股，請勿據此解讀績效。";
  const total = s.equity_history.at(-1).equity;
  const initial = Number(s.initial_cash);
  const profit = Number(total) - initial;
  const returnPct = initial > 0 ? profit / initial * 100 : null;
  // Round for display only; suppress a misleading negative zero.
  const displayedReturn = returnPct === null ? null : Number(returnPct.toFixed(2));
  const returnText = displayedReturn === null ? "—" : `${displayedReturn >= 0 ? "+" : ""}${displayedReturn.toFixed(2)}%`;
  $("return-rate").textContent = returnText;
  $("return-rate").className = displayedReturn > 0 ? "gain" : displayedReturn < 0 ? "loss" : "";
  $("return-rate").title = `相對本金 $${usd(initial)} 的總報酬率；已反映手續費與滑價。`;
  $("return-rate").setAttribute("aria-label", `相對本金報酬率 ${returnText}`);
  $("equity").textContent = "$" + usd(total);
  $("cash").textContent = "$" + usd(s.account.cash);
  $("shares").textContent = s.account.shares;
  $("change").textContent = `相較本金 $${usd(initial)}：${profit >= 0 ? "+" : ""}${usd(profit)} USD（${returnText}）`;
  $("cost-label").textContent = `手續費 $${usd(s.costs.fee)} · 滑價 ${s.costs.slippage_bps} 基點`;
  $("current-day").textContent = `${s.date} 收盤`;
  $("input-summary").textContent = `輸入：${s.symbol || "SPY"}，截至 ${s.date} 的 20 天行情、現金 $${usd(s.account.cash)}、持股 ${s.account.shares} 股。`;
  $("decide").textContent = s.mode === "jev" ? "讓 Jev 做決策" : "執行規則決策";
  $("phase").textContent = s.start_at === "latest" && s.status === "pending" ? "最新判斷已完成" : phases[s.status];
  const latest = s.history.at(-1);
  const decision = s.status === "pending" ? latest?.decision : null;
  for (const action of ["buy", "sell", "hold"]) {
    $("choice-" + action).classList.toggle("selected", decision?.choice === action);
  }
  const fill = latest?.settlement;
  $("last-result").textContent = !fill ? "尚無成交。初始本金 $10,000，每次最多一股。" : fill.status === "filled" ? `最近成交：${fill.date} ${labels[fill.action]} ${fill.quantity} 股，成交價 $${usd(fill.price)}，手續費 $${usd(fill.fee)}。` : `${fill.date}：${fill.status === "held" ? "持有，未交易。" : "未成交：" + fill.reason}`;
  $("decision-action").textContent = decision ? `${labels[decision.choice]} · ${decision.choice}` : s.status === "finished" ? "重播完成" : s.status === "error" ? "已停止" : "準備決策";
  $("decision-action").className = "decision-action " + (decision?.choice || "");
  $("decision-description").textContent = s.error || (s.start_at === "latest" && decision ? "已取得最新已載入行情的判斷；尚無隔日行情，不模擬未來成交。" : s.status === "finished" ? "已到最後一筆行情，無下一交易日可成交。可匯出全部紀錄。" : decision ? (decision.choice === "hold" ? "維持目前部位，不收取交易費用。推進後以隔日收盤價估值。" : "決策已保存，尚未成交。推進後以隔日開盤價檢查資金與持股，再模擬成交。") : `將截至 ${s.date} 的最近 20 天行情與帳戶資料送入${s.mode === "jev" ? " Jev（一次付費請求）" : "離線規則模型"}。`);
  $("calls").textContent = `Jev 請求 ${s.model_calls} 次 · 規則決策 ${s.rule_decisions} 次`;
  $("latency").textContent = s.latency_ms === null ? "尚無決策" : `最近耗時 ${s.latency_ms} ms`;
  $("probabilities").replaceChildren();
  if (decision?.probabilities) {
    for (const action of ["buy", "sell", "hold"]) {
      const row = document.createElement("div"); row.className = "prob-row";
      const label = document.createElement("span"); label.textContent = action;
      const track = document.createElement("div"); track.className = "prob-track";
      const bar = document.createElement("div"); bar.className = "prob-bar";
      bar.style.width = (decision.probabilities[action] * 100) + "%"; track.append(bar);
      const value = document.createElement("span"); value.textContent = Math.round(decision.probabilities[action] * 100) + "%";
      row.append(label, track, value); $("probabilities").append(row);
    }
    const note = document.createElement("small"); note.textContent = "選項機率，不是獲利機率"; note.className = "muted"; $("probabilities").append(note);
  }
  $("history").replaceChildren();
  for (const event of [...s.history].reverse()) {
    const row = document.createElement("tr"), fill = event.settlement;
    cell(row, event.date); const actionCell = cell(row, "");
    const pill = document.createElement("span"); pill.className = "action-label"; pill.textContent = labels[event.decision.choice]; actionCell.append(pill);
    cell(row, fill?.date || "—"); cell(row, fill?.price ? "$" + usd(fill.price) : "—");
    cell(row, fills[fill?.status || "pending"] + (fill?.status === "rejected" ? `：${fill.reason}` : ""));
    cell(row, event.equity ? "$" + usd(event.equity) : "—");
    const inspect = document.createElement("button"); inspect.type = "button"; inspect.textContent = "查看資料";
    inspect.setAttribute("aria-label", `查看 ${event.date} 決策資料`);
    inspect.addEventListener("click", () => {
      $("decision-select").value = event.date;
      renderDecisionData();
      $("records").close();
      setView("data");
      $("decision-data").scrollTop = 0;
      $("decision-select").focus({preventScroll: true});
    });
    cell(row, "").append(inspect); $("history").append(row);
  }
  if (!s.history.length) { const row = document.createElement("tr"); cell(row, "尚無決策紀錄").colSpan = 7; $("history").append(row); }
  const selectedDate = $("decision-select").value;
  $("decision-select").replaceChildren(new Option("最新一筆（自動更新）", "latest"));
  for (const event of [...s.history].reverse()) {
    $("decision-select").append(new Option(`${event.date} · ${event.decision.choice}`, event.date));
  }
  $("decision-select").value = s.history.some(event => event.date === selectedDate) ? selectedDate : "latest";
  $("decision-select").disabled = !s.history.length;
  renderDecisionData();
  renderPrices();
  $("raw-evidence").textContent = JSON.stringify(latest?.decision || s.attempts.at(-1) || {status: "尚未呼叫模型"}, null, 2);
  $("export-json").href = exportUrl(s.run_id, "json"); $("export-csv").href = exportUrl(s.run_id, "csv");
  drawChart();
}
function renderDecisionData() {
  const date = $("decision-select").value;
  const event = date === "latest" ? current?.history.at(-1) : current?.history.find(item => item.date === date);
  const decision = event?.decision;
  $("flow-select").replaceChildren(...Array.from($("decision-select").options, option => new Option(option.text, option.value)));
  $("flow-select").value = date;
  $("flow-select").disabled = !current?.history.length;
  renderDecisionFlow(event);
  $("decision-outcome").textContent = current?.status === "error" ? "最近一次操作失敗或結果不明，未重試。下方保留之前有效的決策紀錄。" : `本次 Jev 請求 ${current?.model_calls || 0} 次 · 離線決策 ${current?.rule_decisions || 0} 次`;
  $("decision-usage").textContent = "";
  $("decision-distribution").replaceChildren();
  $("decision-payload").hidden = !decision;
  $("decision-json").hidden = !decision;
  $("decision-confidence").textContent = "";
  $("decision-json-label").textContent = "";
  $("decision-json").textContent = "";
  $("decision-response").textContent = "";
  $("decision-request").textContent = "";
  if (!decision) { $("decision-meta").textContent = "尚無決策。執行後，每筆資料會保留在這裡。"; return; }
  const answer = decision.raw_response?.answers?.action;
  $("decision-meta").textContent = `${event.date} · ${answer ? "Jev" : "離線規則"} · ${decision.model} · 分類：${decision.choice}${answer ? ` · ${decision.latency_ms} ms` : ""}`;
  if (answer) {
    $("decision-usage").textContent = `API 用量：輸入 ${decision.usage?.input_tokens ?? "未提供"} tokens · 輸出 ${decision.usage?.output_tokens ?? "未提供"} tokens`;
    for (const action of ["buy", "sell", "hold"]) {
      const row = document.createElement("div"); row.className = "prob-row structured-prob";
      const label = document.createElement("span"); label.textContent = action;
      const track = document.createElement("div"); track.className = "prob-track";
      const bar = document.createElement("div"); bar.className = "prob-bar";
      bar.style.width = (answer.probabilities[action] * 100) + "%"; track.append(bar);
      const value = document.createElement("span"); value.textContent = String(answer.probabilities[action]);
      row.append(label, track, value); $("decision-distribution").append(row);
    }
    $("decision-confidence").textContent = `confidence：${answer.confidence}（0～1，表示分布的集中程度）。上方是各分類機率，總和約為 1；兩者都不是獲利機率。`;
    $("decision-json-label").textContent = "Jev 回傳的 answers.action（保留原始數值）";
  } else {
    $("decision-confidence").textContent = "此筆由離線規則產生，未呼叫 Jev。probabilities 與 confidence 為 null，表示沒有提供機率，不是 0%。";
    $("decision-json-label").textContent = "規則模型結果（由程式整理，非 Jev 回應）";
  }
  $("decision-json").textContent = JSON.stringify(answer || {choice: decision.choice, probabilities: decision.probabilities, confidence: decision.confidence}, null, 2);
  $("decision-response").textContent = answer ? JSON.stringify(decision.raw_response, null, 2) : "未呼叫 Jev，沒有 API 回應。";
  $("decision-request").textContent = JSON.stringify(decision.request, null, 2);
}
function renderDecisionFlow(event) {
  if (!current) return;
  const decision = event?.decision;
  const state = decision?.request?.state;
  const bars = state?.bars || current.bars.slice(Math.max(0, current.index - 19), current.index + 1);
  const account = state?.account || current.account;
  const answer = decision?.raw_response?.answers?.action;
  const isJev = current.mode === "jev";
  $("flow-model").textContent = isJev ? "Jev 判斷" : "離線規則判斷";
  $("flow-provider").textContent = isJev ? "真實 API" : "未呼叫 Jev";
  $("visual-decision").classList.toggle("is-jev", isJev);
  $("flow-symbol").textContent = `${state?.symbol || current.symbol || "SPY"} 收盤價`;
  $("flow-price").textContent = "$" + usd(bars.at(-1).close);
  $("flow-range").textContent = `${bars[0].date} → ${bars.at(-1).date} · ${bars.length} 筆`;
  $("flow-cash").textContent = "$" + usd(account.cash);
  $("flow-shares").textContent = `${account.shares} 股`;
  const chart = $("market-chart"); chart.replaceChildren();
  const values = bars.map(bar => Number(bar.close));
  const low = Math.min(...values), high = Math.max(...values);
  const span = high - low || 1;
  const xy = values.map((value, i) => [8 + i * 244 / Math.max(1, values.length - 1), 64 - (value - low) / span * 51]);
  const svg = svgNode("svg", {viewBox: "0 0 260 80", role: "img", "aria-label": `${bars[0].date} 至 ${bars.at(-1).date} 的 ${state?.symbol || current.symbol || "SPY"} 收盤價，最低 ${usd(low)}，最高 ${usd(high)} 美元`});
  svg.append(svgNode("line", {x1: 8, x2: 252, y1: 68, y2: 68, stroke: "#e4e7e4"}));
  svg.append(svgNode("polyline", {points: xy.map(point => point.join(",")).join(" "), fill: "none", stroke: "#687b88", "stroke-width": 2, "stroke-linejoin": "round"}));
  xy.forEach(([cx, cy], i) => { const dot = svgNode("circle", {cx, cy, r: 2, fill: "#687b88"}); dot.append(svgNode("title", {}, `${bars[i].date} · $${usd(values[i])}`)); svg.append(dot); });
  chart.append(svg);
  for (const action of ["buy", "sell", "hold"]) {
    const value = answer?.probabilities?.[action];
    $("choice-" + action).classList.toggle("selected", decision?.choice === action);
    $("prob-" + action).textContent = typeof value === "number" ? String(value) : "—";
    $("bar-" + action).style.width = typeof value === "number" ? `${value * 100}%` : "0%";
  }
  $("flow-choice").textContent = decision ? `${decision.choice} · ${labels[decision.choice]}` : "等待判斷";
  $("flow-confidence").textContent = answer ? `confidence ${answer.confidence}` : "";
  $("flow-explanation").textContent = answer ? "上方為 Jev 回傳的分類機率（0～1），不是獲利機率；此圖呈現輸入與輸出，不代表模型內部推理。" : isJev ? "執行後，這裡會顯示 Jev 實際回傳的選項與機率。" : "目前以 Python 規則代替 Jev；機率與 confidence 為 null，沒有模型回傳值。";
  $("flow-model-meta").textContent = decision ? `${decision.model}${answer ? ` · ${decision.latency_ms} ms · 輸入 ${decision.usage?.input_tokens ?? "未提供"} tokens` : ""}` : "尚無回傳";
  const fill = event?.settlement;
  const latestOnly = current.start_at === "latest";
  $("execution-note").textContent = latestOnly ? "本次只提供判斷；沒有隔日行情，不會模擬未來成交。" : "程式檢查資金與持股，再以隔日開盤價模擬成交。";
  $("flow-result-icon").textContent = fill?.status === "filled" ? "✓" : fill?.status === "held" ? "＝" : fill?.status === "rejected" ? "×" : "…";
  $("flow-result").textContent = fill ? fills[fill.status] : decision && latestOnly ? "判斷完成，未成交" : decision ? "已決策，尚未成交" : "等待決策";
  $("flow-fill").textContent = !fill ? (latestOnly ? "可切換歷史重播評估模擬結果" : "推進下一交易日後更新") : fill.status === "filled" ? `${fill.date} · ${labels[fill.action]} ${fill.quantity} 股\n$${usd(fill.price)} / 股 · 手續費 $${usd(fill.fee)}` : `${fill.date} · ${fill.status === "held" ? "維持持股，不收交易費" : fill.reason}`;
  const equity = event?.equity;
  $("flow-equity").textContent = equity != null ? `該日資產 $${usd(equity)} · ${((Number(equity) / Number(current.initial_cash) - 1) * 100).toFixed(2)}%` : "";
  $("flow-context").textContent = event ? `正在查看 ${event.date} 的決策與對應結果；上方帳戶為目前模擬日 ${current.date}。` : `準備使用截至 ${current.date} 的行情；尚未產生決策。`;
  if (current.status === "error") $("flow-context").textContent += " 最近一次操作失敗，這裡保留先前有效紀錄。";
}
function renderPrices() {
  if (!current) return;
  const s = current, scope = $("price-scope").value;
  const from = scope === "window" ? Math.max(0, s.index - 19) : 0;
  const end = scope === "all" ? s.bars.length : s.index + 1;
  const bars = s.bars.slice(from, end).reverse();
  const pages = Math.max(1, Math.ceil(bars.length / 25));
  pricePage = Math.min(pricePage, pages - 1);
  $("price-source").textContent = `${sources[s.source]}${s.source === "synthetic" ? " · 非真實行情" : " · 日線"}`;
  $("price-range").textContent = `${s.bars.length} 筆 · 最新資料 ${s.bars.at(-1).date} · ${s.start_at === "latest" ? "判斷日" : "目前模擬日"} ${s.date}`;
  $("price-range").title = `${s.bars[0].date} — ${s.bars.at(-1).date}；歷史樣本，非即時報價。`;
  $("price-notice").textContent = scope === "all" ? "「尚未重播」僅供檢查資料，不會送進當前決策。表格價格顯示到 2 位小數。" : "決策只使用截至模擬日的最近 20 筆行情；價格顯示到 2 位小數。";
  $("price-rows").replaceChildren();
  for (const bar of bars.slice(pricePage * 25, (pricePage + 1) * 25)) {
    const row = document.createElement("tr");
    cell(row, bar.date);
    for (const field of ["open", "high", "low", "close"]) cell(row, usd(bar[field]));
    cell(row, Number(bar.volume).toLocaleString("en-US"));
    cell(row, bar.date > s.date ? "尚未重播" : bar.date === s.date ? "目前決策日" : "已發生");
    row.classList.toggle("current-price", bar.date === s.date);
    $("price-rows").append(row);
  }
  $("price-page").textContent = `${pricePage + 1} / ${pages} 頁 · ${bars.length} 筆`;
  $("price-prev").disabled = pricePage === 0;
  $("price-next").disabled = pricePage >= pages - 1;
  $("price-provenance").textContent = JSON.stringify({source: s.source, digest: s.digest, ...s.data_info}, null, 2);
}
function updateSource() {
  const symbol = selectedSymbol();
  $("source").querySelector('[value="demo"]').disabled = $("mode").value === "jev" || symbol !== "SPY";
  $("source").querySelector('[value="sample"]').disabled = symbol !== "SPY";
  $("source").querySelector('[value="cache"]').disabled = !cachedSymbols.includes(symbol);
  $("csv-field").hidden = $("source").value !== "csv";
  $("start").textContent = $("source").value === "demo" ? "載入示例" : "載入行情";
}
function svgNode(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
function drawChart() {
  const box = $("chart"); box.replaceChildren();
  if (!current) return;
  const points = current.equity_history, width = Math.max(220, box.clientWidth), height = Math.max(120, box.clientHeight);
  const svg = svgNode("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "模擬資產總值（美元），橫軸為交易日期"});
  svg.append(svgNode("title", {}, `資產總值：${points.at(-1).equity} 美元，${points.length} 個估值日期`));
  const values = points.map(p => Number(p.equity));
  let low = Math.min(...values), high = Math.max(...values);
  const pad = Math.max(1, (high - low) * .2); low -= pad; high += pad;
  const left = 66, right = width - 16, top = 15, bottom = height - 28;
  const x = i => points.length === 1 ? left + (right - left) / 2 : left + i * (right - left) / (points.length - 1);
  const y = n => bottom - (n - low) / (high - low) * (bottom - top);
  for (let i = 0; i < 3; i++) {
    const value = low + i * (high - low) / 2, yy = y(value);
    svg.append(svgNode("line", {x1: left, x2: right, y1: yy, y2: yy, stroke: "#e5ebe4"}));
    svg.append(svgNode("text", {x: left - 10, y: yy + 4, "text-anchor": "end", fill: "#64756e", "font-size": 10}, usd(value)));
  }
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(Number(p.equity))}`).join(" ");
  svg.append(svgNode("path", {d: path, fill: "none", stroke: "#17674e", "stroke-width": 2.5, "stroke-linejoin": "round"}));
  points.forEach((p, i) => { const dot = svgNode("circle", {cx: x(i), cy: y(Number(p.equity)), r: points.length > 30 ? 2 : 3.5, fill: "#17674e"}); dot.append(svgNode("title", {}, `${p.date} · $${usd(p.equity)}`)); svg.append(dot); });
  svg.append(svgNode("text", {x: left, y: height - 5, fill: "#64756e", "font-size": 10}, points[0].date));
  if (points.length > 1) svg.append(svgNode("text", {x: right, y: height - 5, "text-anchor": "end", fill: "#64756e", "font-size": 10}, points.at(-1).date));
  box.append(svg);
}
async function refreshRuns() {
  const data = await api("/api/runs"); $("runs").replaceChildren();
  if (!data.runs.length) { $("runs").textContent = "尚無執行紀錄"; return; }
  for (const run of data.runs) {
    const row = document.createElement("div"); row.className = "run-row";
    const title = document.createElement("span"); title.className = "run-title";
    title.textContent = `${run.symbol || "SPY"} · ${new Date(run.created_at).toLocaleString("zh-TW")} · ${sources[run.source] || run.source} · ${run.mode === "jev" ? "Jev" : "規則模型"} · ${phases[run.status] || run.status}`;
    const links = document.createElement("span");
    for (const format of ["json", "csv"]) { const a = document.createElement("a"); a.href = exportUrl(run.run_id, format); a.textContent = format.toUpperCase() + " ↓"; links.append(a); }
    row.append(title, links); $("runs").append(row);
  }
}
async function perform(name, body) {
  if (busy || replay?.isRunning()) return;
  if (name !== "start" && (current?.mode !== $("mode").value || (current?.symbol || "SPY") !== selectedSymbol() || (current?.start_at || "replay") !== $("start-at").value)) return;
  busy = true; controls(); message(name === "start" ? "正在載入行情…" : name === "decide" ? "正在執行決策，請勿重複操作…" : "正在推進交易日…");
  const activeStep = name === "decide" ? "decision" : name === "advance" ? "execution" : "input";
  $("visual-" + activeStep).classList.add("active");
  if (current) $("flow-context").textContent = "正在處理；圖中保留先前紀錄，收到結果後更新。";
  try {
    render(await api("/api/" + name, body));
    if (name === "start") showReplay({completed: 0, target: 5, running: false, stage: null});
    message(current?.error || (name === "start" ? (current.start_at === "latest" ? `${current.symbol} 行情已載入（截至 ${current.date}），點「執行決策」取得判斷。` : `${current.symbol} 行情已載入，可自動重播 5 天。`) : name === "decide" ? (current.start_at === "latest" ? "判斷已保存；尚無隔日行情，不模擬成交。" : "決策已保存，尚未成交。") : "已推進至下一交易日。"), !!current?.error);
    await refreshRuns();
  } catch (error) {
    message(error.message, true);
    try { render(await api("/api/state")); } catch { /* Preserve the actionable error. */ }
  } finally {
    busy = false; controls();
    $("visual-" + activeStep).classList.remove("active");
    if (current) renderDecisionData();
  }
}
function showReplay(update) {
  controls();
  $("auto-progress").value = update.completed;
  $("auto-count").textContent = `${update.completed} / ${update.target} 天`;
  $("auto-start").textContent = update.running ? "自動執行中…" : "自動執行 5 天";
  $("auto-stop").disabled = !update.running || update.stopping;
  const stages = {input: "讀取本日行情與帳戶", decision: current?.mode === "jev" ? "等待 Jev 選擇 buy / sell / hold" : "離線規則選擇 buy / sell / hold", execution: "推進下一交易日並模擬成交", settled: "帳戶與資產曲線已更新"};
  const outcomes = {complete: "已完成 5 個交易日。", finished: "已到最後一筆行情。", stopped: "已停止。可手動操作或再次自動執行。", error: `已停止：${update.error || "操作失敗"}`};
  $("auto-status").textContent = update.stopping ? "正在停止，等待目前操作完成…" : update.outcome ? outcomes[update.outcome] : update.running ? stages[update.stage] : "依序決策、成交，最多推進 5 個交易日。";
  for (const step of ["input", "decision", "execution"]) {
    const active = update.running && (update.stage === step || (step === "execution" && update.stage === "settled"));
    $("flow-" + step).classList.toggle("active", active);
    $("visual-" + step).classList.toggle("active", active);
    if (active) $("flow-" + step).setAttribute("aria-current", "step");
    else $("flow-" + step).removeAttribute("aria-current");
  }
  if (update.running) $("flow-context").textContent = "正在處理下一步；圖中保留最近一筆已收到的資料，完成後更新。";
  else if (current) {
    renderDecisionData();
    if (current.start_at === "latest") $("auto-status").textContent = "最新行情判斷：請點執行決策；自動執行僅適用歷史重播。";
  }
}
replay = PaperReplay.createReplay({
  getState: () => current,
  execute: async (name, snapshot) => {
    render(await api("/api/" + name, {run_id: snapshot.run_id, expected_version: snapshot.version}));
    return current;
  },
  onUpdate: showReplay
});
$("auto-start").addEventListener("click", async () => {
  if (busy || replay.isRunning() || !(current?.can_decide || current?.can_advance)) return;
  if (current.mode !== $("mode").value || current.start_at === "latest" || (current.symbol || "SPY") !== selectedSymbol() || (current.start_at || "replay") !== $("start-at").value) return;
  message("");
  drawChart();
  await replay.start();
  // Read once after completion or failure; never retry a mutation.
  busy = true; controls();
  try { render(await api("/api/state")); await refreshRuns(); }
  catch (error) { message(error.message, true); }
  finally { busy = false; controls(); }
});
$("auto-stop").addEventListener("click", () => replay.stop());
window.addEventListener("pagehide", () => replay.stop());
$("source").addEventListener("change", () => {
  $("start-at").value = ["sample", "demo"].includes($("source").value) ? "replay" : "latest";
  updateSource(); controls();
});
$("symbol").addEventListener("input", () => {
  if (!["yahoo", "alpha", "csv"].includes($("source").value)) $("source").value = "yahoo";
  $("start-at").value = "latest";
  updateSource(); controls();
  message("代號已變更；點「載入行情」建立該股票的新模擬，舊紀錄保留。");
});
$("symbol").addEventListener("change", () => { $("symbol").value = selectedSymbol(); });
$("start-at").addEventListener("change", () => { controls(); message("分析方式已變更，請載入行情建立新模擬。"); });
$("mode").addEventListener("change", () => {
  if ($("mode").value === "jev" && $("source").value === "demo") $("source").value = selectedSymbol() === "SPY" ? "sample" : "yahoo";
  updateSource(); controls();
  const missing = $("mode").value === "jev" && !configuration.jev;
  message(missing ? "尚未設定 TYPESAFE_API_KEY。請在本機 .env 填入並重啟服務，再載入行情。" : current?.mode === $("mode").value ? "已回到目前執行模式。" : "已選擇新模式；點「載入行情」建立新模擬，舊紀錄保留。", missing);
});
$("setup-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || replay.isRunning()) return;
  const body = {symbol: selectedSymbol(), start_at: $("start-at").value, source: $("source").value, mode: $("mode").value, fee: $("fee").value, slippage_bps: $("slippage").value};
  if (body.mode === "jev" && !configuration.jev) {
    message("尚未設定 TYPESAFE_API_KEY；目前模擬保留，沒有發送 Jev 請求。", true);
    $("settings").showModal(); return;
  }
  if (body.source === "csv") {
    const file = $("csv-file").files[0];
    if (!file || file.size > 2 * 1024 * 1024) { message("請選擇不超過 2 MiB 的 CSV。", true); $("settings").showModal(); return; }
    body.csv_text = await file.text();
  }
  perform("start", body);
});
$("decide").addEventListener("click", () => { if (current) perform("decide", {run_id: current.run_id, expected_version: current.version}); });
$("advance").addEventListener("click", () => { if (current) perform("advance", {run_id: current.run_id, expected_version: current.version}); });
$("refresh-runs").addEventListener("click", () => refreshRuns().catch(e => message(e.message, true)));
$("decision-select").addEventListener("change", renderDecisionData);
$("flow-select").addEventListener("change", () => { $("decision-select").value = $("flow-select").value; renderDecisionData(); });
$("price-scope").addEventListener("change", () => { pricePage = 0; renderPrices(); });
$("price-prev").addEventListener("click", () => { pricePage--; renderPrices(); });
$("price-next").addEventListener("click", () => { pricePage++; renderPrices(); });
new ResizeObserver(drawChart).observe($("chart"));
function setView(view) {
  document.querySelector(".dashboard").dataset.view = view;
  for (const button of document.querySelectorAll("[data-view][role=tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.view === view));
    button.tabIndex = button.dataset.view === view ? 0 : -1;
  }
  if (view === "live") drawChart();
}
for (const button of document.querySelectorAll("[data-view][role=tab]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const views = ["live", "prices", "data"];
    const index = views.indexOf(button.dataset.view);
    const view = event.key === "Home" ? "live" : event.key === "End" ? "data" : views[(index + (event.key === "ArrowRight" ? 1 : 2)) % 3];
    setView(view); $("tab-" + view).focus();
  });
}
for (const button of document.querySelectorAll("[data-open-dialog]")) button.addEventListener("click", () => $(button.dataset.openDialog).showModal());
for (const button of document.querySelectorAll("[data-close-dialog]")) button.addEventListener("click", () => button.closest("dialog").close());
$("setup-form").addEventListener("invalid", event => { if (event.target.id === "symbol") return; if (!$("settings").open) $("settings").showModal(); }, true);
api("/api/state").then(render).then(refreshRuns).catch(e => message(e.message, true));
