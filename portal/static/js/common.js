// Shared helpers for the portal UI.
async function getJSON(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function fmtUptime(seconds) {
  if (!seconds || seconds < 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const parts = [];
  if (d) parts.push(d + "d");
  if (h) parts.push(h + "h");
  if (m || !parts.length) parts.push(m + "m");
  return parts.join(" ");
}

function esc(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Relative time + freshness badge based on an ISO timestamp.
function freshness(iso) {
  if (!iso) return { label: "데이터 없음", cls: "offline" };
  const then = new Date(iso).getTime();
  if (isNaN(then)) return { label: "—", cls: "" };
  const mins = (Date.now() - then) / 60000;
  if (mins < 30) return { label: "정상", cls: "online" };
  if (mins < 180) return { label: "지연", cls: "stale" };
  return { label: "오프라인", cls: "offline" };
}

function relTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return iso;
  const s = Math.floor((Date.now() - then) / 1000);
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}
