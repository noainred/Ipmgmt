// Web settings: manage per-datacenter scan config via the config API.
// The admin token is kept only in localStorage and sent as X-Admin-Token.
const TOKEN_KEY = "ipmgmt_admin_token";
let editingId = null;

function token() { return localStorage.getItem(TOKEN_KEY) || ""; }

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "X-Admin-Token": token(), Accept: "application/json" },
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${method} ${path} -> ${r.status}`);
  return data;
}

function setAuthState() {
  const el = document.getElementById("auth-state");
  if (token()) { el.textContent = "인증됨 ✓"; el.style.color = "var(--good)"; }
  else { el.textContent = "미인증"; el.style.color = "var(--muted)"; }
}

document.getElementById("auth-save").addEventListener("click", () => {
  const v = document.getElementById("admin-token").value.trim();
  if (v) localStorage.setItem(TOKEN_KEY, v);
  setAuthState();
  loadConfigs();
});
document.getElementById("auth-clear").addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY);
  document.getElementById("admin-token").value = "";
  setAuthState();
  document.getElementById("cfg-rows").innerHTML =
    '<tr><td colspan="7" class="empty">인증 후 표시됩니다.</td></tr>';
});

async function loadConfigs() {
  if (!token()) return;
  try {
    const data = await api("GET", "/api/v1/config");
    const rows = data.configs.map((c) => {
      const subnets = (c.subnets || []).join(", ") || "—";
      return `<tr>
        <td class="mono"><b>${esc(c.id)}</b></td>
        <td>${esc(c.name || "—")}</td>
        <td>${esc(c.location || "—")}</td>
        <td class="small">${esc(subnets)}</td>
        <td class="num">${c.scan_interval_seconds}s</td>
        <td><span class="badge ${c.enabled ? "online" : "offline"}">${c.enabled ? "사용" : "중지"}</span></td>
        <td class="row-actions">
          <button class="btn" data-edit="${esc(c.id)}">편집</button>
          <button class="btn danger" data-del="${esc(c.id)}">삭제</button>
        </td>
      </tr>`;
    }).join("");
    document.getElementById("cfg-rows").innerHTML = rows ||
      '<tr><td colspan="7" class="empty">설정된 데이터센터가 없습니다. 아래에서 추가하세요.</td></tr>';
    document.getElementById("cfg-count").textContent = `${data.configs.length}개`;
    wireRowButtons(data.configs);
  } catch (e) {
    document.getElementById("cfg-rows").innerHTML =
      `<tr><td colspan="7" class="empty">불러오기 실패: ${esc(e.message)}</td></tr>`;
  }
}

function wireRowButtons(configs) {
  document.querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => fillForm(configs.find((c) => c.id === b.dataset.edit))));
  document.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => removeConfig(b.dataset.del)));
}

function fillForm(c) {
  if (!c) return;
  editingId = c.id;
  document.getElementById("form-title").textContent = `데이터센터 편집: ${c.id}`;
  const idEl = document.getElementById("f-id");
  idEl.value = c.id; idEl.disabled = true;
  document.getElementById("f-name").value = c.name || "";
  document.getElementById("f-location").value = c.location || "";
  document.getElementById("f-interval").value = c.scan_interval_seconds || 900;
  document.getElementById("f-subnets").value = (c.subnets || []).join("\n");
  document.getElementById("f-enabled").checked = !!c.enabled;
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function resetForm() {
  editingId = null;
  document.getElementById("form-title").textContent = "새 데이터센터 추가";
  const idEl = document.getElementById("f-id");
  idEl.disabled = false; idEl.value = "";
  ["f-name", "f-location", "f-subnets"].forEach((i) => (document.getElementById(i).value = ""));
  document.getElementById("f-interval").value = 900;
  document.getElementById("f-enabled").checked = true;
  document.getElementById("save-msg").textContent = "";
}
document.getElementById("reset-form").addEventListener("click", resetForm);

document.getElementById("save-cfg").addEventListener("click", async () => {
  const msg = document.getElementById("save-msg");
  if (!token()) { msg.textContent = "먼저 관리자 인증을 하세요."; return; }
  const id = (editingId || document.getElementById("f-id").value).trim();
  if (!id) { msg.textContent = "ID는 필수입니다."; return; }
  const body = {
    name: document.getElementById("f-name").value.trim(),
    location: document.getElementById("f-location").value.trim(),
    subnets: document.getElementById("f-subnets").value,
    scan_interval_seconds: parseInt(document.getElementById("f-interval").value, 10) || 900,
    enabled: document.getElementById("f-enabled").checked,
  };
  try {
    await api("PUT", `/api/v1/config/${encodeURIComponent(id)}`, body);
    msg.textContent = "저장됨 ✓"; msg.style.color = "var(--good)";
    resetForm();
    loadConfigs();
  } catch (e) {
    msg.textContent = "저장 실패: " + e.message; msg.style.color = "var(--bad)";
  }
});

async function removeConfig(id) {
  if (!confirm(`'${id}' 설정을 삭제할까요? (수집된 호스트 데이터는 유지됩니다)`)) return;
  try {
    await api("DELETE", `/api/v1/config/${encodeURIComponent(id)}`);
    if (editingId === id) resetForm();
    loadConfigs();
  } catch (e) { alert("삭제 실패: " + e.message); }
}

// init
document.getElementById("admin-token").value = token();
setAuthState();
loadConfigs();
