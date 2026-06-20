// Per-datacenter host inventory with client-side filtering.
const qEl = document.getElementById("f-q");
const osEl = document.getElementById("f-os");
let debounce;

function osCell(h) {
  const name = h.os_name || "Unknown";
  const acc = h.os_accuracy ? `${h.os_accuracy}%` : "—";
  return { name, acc };
}

async function load() {
  const params = new URLSearchParams({ dc: window.DC_ID, limit: "5000" });
  if (qEl.value.trim()) params.set("q", qEl.value.trim());
  if (osEl.value) params.set("os_family", osEl.value);

  const data = await getJSON("/api/v1/hosts?" + params.toString());
  const rows = data.hosts.map((h) => {
    const os = osCell(h);
    const ports = (h.open_ports || []).map((p) => `<span class="port">${p}</span>`).join("") || "—";
    return `<tr>
      <td class="mono"><b>${esc(h.ip)}</b></td>
      <td>${esc(h.hostname || "—")}</td>
      <td>${esc(os.name)} <span class="muted small">(${esc(h.os_family)})</span></td>
      <td class="num">${os.acc}</td>
      <td>${fmtUptime(h.uptime_seconds)}</td>
      <td class="small">${esc(h.last_boot || "—")}</td>
      <td>${ports}</td>
      <td class="small">${esc(h.subnet || "—")}</td>
    </tr>`;
  }).join("");

  document.getElementById("host-rows").innerHTML = rows ||
    '<tr><td colspan="8" class="empty">조건에 맞는 호스트가 없습니다.</td></tr>';
  document.getElementById("host-count").textContent =
    `${data.count} / ${data.total} 호스트`;
}

async function loadOsFilter() {
  // Populate OS family options from the stats endpoint scoped via hosts.
  try {
    const data = await getJSON("/api/v1/hosts?dc=" + encodeURIComponent(window.DC_ID) + "&limit=5000");
    const fams = [...new Set(data.hosts.map((h) => h.os_family).filter(Boolean))].sort();
    osEl.innerHTML = '<option value="">전체 OS</option>' +
      fams.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  } catch (e) { console.error(e); }
}

qEl.addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(load, 250);
});
osEl.addEventListener("change", load);

(async () => { await loadOsFilter(); await load(); })();
setInterval(load, 30000);
