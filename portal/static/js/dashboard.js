// Dashboard: refresh global stats + datacenter table periodically.
async function refresh() {
  try {
    const [stats, dcs] = await Promise.all([
      getJSON("/api/v1/stats"),
      getJSON("/api/v1/datacenters"),
    ]);

    document.getElementById("stat-dcs").textContent = stats.datacenters;
    document.getElementById("stat-hosts").textContent = stats.hosts;

    const osWrap = document.getElementById("os-breakdown");
    osWrap.innerHTML = stats.os_breakdown.map(
      (o) => `<span class="os-chip" data-family="${esc(o.os_family)}">${esc(o.os_family)} <b>${o.count}</b></span>`
    ).join("") || '<span class="muted">—</span>';

    const rows = dcs.datacenters.map((dc) => {
      const f = freshness(dc.last_seen);
      const subnets = (dc.subnets || []).join(", ") || "—";
      return `<tr>
        <td><a href="/dc/${encodeURIComponent(dc.id)}"><b>${esc(dc.name || dc.id)}</b></a>
            <div class="muted small">${esc(dc.id)}</div></td>
        <td>${esc(dc.location || "—")}</td>
        <td class="num">${dc.host_count}</td>
        <td class="small">${esc(subnets)}</td>
        <td class="small">${esc(relTime(dc.last_scan_finished || dc.last_seen))}</td>
        <td><span class="badge ${f.cls}">${f.label}</span></td>
        <td><a class="btn" href="/dc/${encodeURIComponent(dc.id)}">조회 →</a></td>
      </tr>`;
    }).join("");

    document.getElementById("dc-rows").innerHTML = rows ||
      '<tr><td colspan="7" class="empty">아직 수집된 데이터가 없습니다. collector를 실행하세요.</td></tr>';
    document.getElementById("dc-updated").textContent = "업데이트 " + relTime(new Date().toISOString());
  } catch (e) {
    console.error(e);
  }
}

refresh();
setInterval(refresh, 30000);
