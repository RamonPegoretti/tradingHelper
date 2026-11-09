document.getElementById("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("err");
  err.textContent = "";
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tradebot": "1" },
    body: JSON.stringify({
      password: document.getElementById("password").value,
      code: document.getElementById("code").value,
    }),
  });
  if (res.ok) { location.href = "/"; return; }
  const body = await res.json().catch(() => ({}));
  err.textContent = body.detail || "Login failed";
  document.getElementById("code").value = "";
});
