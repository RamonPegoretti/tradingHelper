const $ = (id) => document.getElementById(id);
const AMOUNTS = [25, 50, 100, 250];
const LEVS = [1, 2, 3, 5, 10, 20];
const state = { kind: "futures", coin: null, amount: 100, lev: 5, pending: null, ttl: 60, timer: null };

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tradebot": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("logged out"); }
  return res.json();
}

function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---- chips ------------------------------------------------------------------

function chips(container, values, current, label, onPick, custom) {
  container.replaceChildren();
  for (const v of values) {
    const b = el("button", { type: "button", class: v === current ? "on" : "" }, label(v));
    b.onclick = () => onPick(v);
    container.appendChild(b);
  }
  if (custom) {
    const inp = el("input", { inputmode: "decimal", placeholder: "other", class: "other" });
    if (!values.includes(current) && current) inp.value = current;
    inp.oninput = () => { const n = parseFloat(inp.value); if (n > 0) onPick(n, true); };
    container.appendChild(inp);
  }
}

function renderChips() {
  chips($("amounts"), AMOUNTS, state.amount, (v) => v, (v, typed) => { state.amount = v; if (!typed) renderChips(); }, true);
  chips($("levs"), LEVS.filter((l) => l <= state.maxLev), state.lev, (v) => v + "x", (v) => { state.lev = v; renderChips(); });
  for (const b of $("coins").children) b.classList.toggle("on", b.dataset.coin === state.coin);
}

function setKind(kind) {
  state.kind = kind;
  for (const b of $("market-kind").children) b.classList.toggle("on", b.dataset.kind === kind);
  const fut = kind === "futures";
  $("lev-row").hidden = !fut;
  document.querySelectorAll(".fut").forEach((n) => (n.hidden = !fut));
  $("long").textContent = fut ? "Long" : "Buy";
  $("short").textContent = fut ? "Short" : "Sell";
  $("amount-hint").textContent = fut ? "USDT margin" : "USDT";
}

// ---- confirm bar (shared by the panel and the text box) ---------------------

function showConfirm(reply) {
  clearInterval(state.timer);
  state.pending = reply.token;
  $("summary").textContent = reply.message;
  $("confirm").hidden = false;
  $("result").hidden = true;
  const deadline = Date.now() + state.ttl * 1000 - 500;
  const tick = () => {
    const left = Math.round((deadline - Date.now()) / 1000);
    if (left <= 0) { hideConfirm(); showResult("error", "Expired, nothing was placed. Press again for a fresh price."); return; }
    $("timer").textContent = left + "s";
  };
  tick();
  state.timer = setInterval(tick, 250);
  $("yes").focus();
}

function hideConfirm() {
  clearInterval(state.timer);
  state.pending = null;
  $("confirm").hidden = true;
}

function showResult(kind, text) {
  const r = $("result");
  r.className = "result " + kind;
  r.textContent = text;
  r.hidden = false;
}

async function preview(text) {
  hideConfirm();
  const r = await api("/api/preview", { text });
  if (r.status === "confirm") showConfirm(r);
  else showResult(r.status === "error" ? "error" : r.status === "incomplete" ? "ask" : "info", r.message);
  return r;
}

async function place() {
  const token = state.pending;
  if (!token) return;
  hideConfirm();
  showResult("info", "Placing…");
  const r = await api("/api/confirm", { token });
  showResult(r.status === "done" ? "ok" : "error", r.message);
  refresh();
}

async function cancel() {
  const token = state.pending;
  hideConfirm();
  if (token) await api("/api/cancel", { token });
}

$("yes").onclick = place;
$("no").onclick = cancel;
document.addEventListener("keydown", (e) => {
  if (!state.pending) return;
  if (e.key === "Escape") { e.preventDefault(); cancel(); $("text").focus(); }
  if (e.key === "Enter" && document.activeElement !== $("text")) { e.preventDefault(); place(); }
});

// ---- text box: Enter previews, Enter again places ---------------------------

let lastText = "";
$("quick").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (state.pending && text === lastText) { await place(); $("text").value = ""; lastText = ""; return; }
  if (!text) return;
  lastText = text;
  const r = await preview(text);
  if (r.status !== "confirm") lastText = "";
  if (r.status === "info") $("text").value = "";
  $("text").focus();
});
$("text").addEventListener("keydown", (e) => { if (e.key === "Escape" && state.pending) { cancel(); } });

// ---- panel buttons -> the same command text ---------------------------------

function panelText(side) {
  if (!state.coin) return null;
  const fut = state.kind === "futures";
  const verb = fut ? side : side === "long" ? "buy" : "sell";
  let t = `${verb} ${state.coin} ${state.amount}`;
  if (fut) t += ` ${state.lev}x`;
  const price = $("price").value.trim(), tp = $("tp").value.trim(), sl = $("sl").value.trim();
  if (price) t += ` at ${price}`;
  if (fut && tp) t += ` tp ${tp}`;
  if (fut && sl) t += ` sl ${sl}`;
  return t;
}

for (const side of ["long", "short"]) {
  $(side).onclick = () => {
    const t = panelText(side);
    if (!t) { showResult("ask", "Pick a coin first."); return; }
    preview(t);
  };
}
for (const b of $("market-kind").children) b.onclick = () => setKind(b.dataset.kind);

// ---- positions, prices, balance ---------------------------------------------

function fmtNum(x) {
  const n = Number(x);
  if (!isFinite(n)) return x;
  return n.toLocaleString("en-US", { maximumFractionDigits: n >= 100 ? 2 : 6 });
}

async function refreshPositions() {
  const r = await api("/api/positions");
  const box = $("positions");
  box.replaceChildren();
  if (!Array.isArray(r.data) || !r.data.length) { box.textContent = "No open positions."; return; }
  const table = el("table");
  table.appendChild(el("tr")).append(...["", "Size", "Entry", "Mark", "PnL", ""].map((h) => el("th", {}, h)));
  for (const p of r.data) {
    const amt = Number(p.positionAmt), pnl = Number(p.unRealizedProfit);
    const coin = p.symbol.replace(/USDT$/, "");
    const tr = el("tr");
    tr.append(
      el("td", { class: amt > 0 ? "long-t" : "short-t" }, `${coin} ${amt > 0 ? "LONG" : "SHORT"} ${p.leverage || ""}x`),
      el("td", {}, fmtNum(Math.abs(amt))),
      el("td", {}, fmtNum(p.entryPrice)),
      el("td", {}, fmtNum(p.markPrice)),
      el("td", { class: pnl >= 0 ? "long-t" : "short-t" }, (pnl >= 0 ? "+" : "") + pnl.toFixed(2)),
    );
    const td = el("td");
    const btn = el("button", { type: "button", class: "secondary small" }, "Close");
    btn.onclick = () => preview(`close ${coin}`);
    td.appendChild(btn);
    tr.appendChild(td);
    table.appendChild(tr);
  }
  box.appendChild(table);
}

async function refreshOther() {
  const [orders, bal, prices] = await Promise.all([api("/api/orders"), api("/api/balance"), api("/api/prices")]);
  $("orders").textContent = orders.message;
  const f = bal.data && bal.data.futures;
  $("bal").textContent = f ? `Futures ${fmtNum(f.available)} USDT available` : "";
  for (const b of $("coins").children) {
    const p = prices[b.dataset.coin];
    b.querySelector("small").textContent = p ? fmtNum(p) : "—";
  }
}

function refresh() { refreshPositions().catch(() => {}); refreshOther().catch(() => {}); }

$("refresh").onclick = refresh;
$("logout").onclick = async () => { await api("/api/logout", {}); location.href = "/login"; };

(async () => {
  const info = await api("/api/info");
  state.ttl = info.confirm_ttl;
  state.maxLev = info.max_leverage;
  if (state.lev > state.maxLev) state.lev = state.maxLev;
  $("env").textContent = info.env.toUpperCase();
  $("env").classList.add(info.env);
  for (const c of info.whitelist) {
    const b = el("button", { type: "button", "data-coin": c });
    b.append(el("strong", {}, c), el("small", { class: "muted" }, "…"));
    b.onclick = () => { state.coin = c; renderChips(); };
    $("coins").appendChild(b);
  }
  state.coin = info.whitelist[0] || null;
  setKind("futures");
  renderChips();
  refresh();
  setInterval(refresh, 10000);
})();
