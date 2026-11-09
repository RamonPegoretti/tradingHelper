const $ = (id) => document.getElementById(id);
const logEl = $("log");
let confirmTtl = 60;

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

function add(cls, text) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function confirmCard(reply) {
  const node = $("confirm-tpl").content.firstElementChild.cloneNode(true);
  node.querySelector(".summary").textContent = reply.message;
  const yes = node.querySelector(".yes"), no = node.querySelector(".no"), timer = node.querySelector(".timer");
  // Count down from the server's TTL (not its clock) so a skewed laptop clock doesn't matter.
  const deadline = Date.now() + confirmTtl * 1000 - 500;
  let done = false;
  const finish = (label) => {
    done = true; clearInterval(tick);
    yes.disabled = no.disabled = true; timer.textContent = label;
  };
  const tick = setInterval(() => {
    const left = Math.round((deadline - Date.now()) / 1000);
    if (left <= 0) { finish("expired"); return; }
    timer.textContent = left + "s left";
  }, 250);
  yes.onclick = async () => {
    if (done) return; finish("sending…");
    const r = await api("/api/confirm", { token: reply.token });
    timer.textContent = r.status === "done" ? "placed" : "failed";
    add(r.status === "done" ? "ok" : "error", r.message);
    refresh();
  };
  no.onclick = async () => {
    if (done) return; finish("cancelled");
    await api("/api/cancel", { token: reply.token });
  };
  logEl.appendChild(node);
  logEl.scrollTop = logEl.scrollHeight;
  yes.focus();
}

$("cmd").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  $("text").value = "";
  add("me", text);
  try {
    const r = await api("/api/preview", { text });
    if (r.status === "confirm") confirmCard(r);
    else add(r.status === "error" ? "error" : r.status === "incomplete" ? "ask" : "info", r.message);
  } catch (err) { add("error", String(err)); }
});

async function panel(id, path) {
  try {
    const r = await api(path);
    $(id).textContent = r.message;
  } catch (err) { $(id).textContent = "—"; }
}

function refresh() {
  panel("positions", "/api/positions");
  panel("orders", "/api/orders");
  panel("balance", "/api/balance");
}

document.querySelector("[data-refresh]").onclick = refresh;
$("logout").onclick = async () => { await api("/api/logout", {}); location.href = "/login"; };

(async () => {
  const info = await api("/api/info");
  confirmTtl = info.confirm_ttl;
  $("env").textContent = info.env.toUpperCase();
  $("env").classList.add(info.env);
  $("limits").textContent = `${info.whitelist.join(", ")} · max ${info.max_leverage}x · ${info.margin_type.toLowerCase()}`;
  refresh();
  setInterval(refresh, 15000);
})();
