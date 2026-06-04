(() => {
  const storeId = window.STORE_ID;
  const $ = id => document.getElementById(id);

  // --- Funnel chart ---
  const funnelCtx = $("funnel-chart").getContext("2d");
  const funnelChart = new Chart(funnelCtx, {
    type: "bar",
    data: {
      labels: ["Entry", "Zone Visit", "Billing Queue", "Purchase"],
      datasets: [{
        label: "Visitors",
        data: [0, 0, 0, 0],
        backgroundColor: ["#7c3aed", "#6d28d9", "#5b21b6", "#4c1d95"],
        borderRadius: 6,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: "#2a2d3e" } },
        y: { ticks: { color: "#e2e8f0" }, grid: { display: false } },
      },
    },
  });

  function updateFunnel(stages) {
    funnelChart.data.labels = stages.map(s => `${s.stage} (${s.pct_of_entry}%)`);
    funnelChart.data.datasets[0].data = stages.map(s => s.count);
    funnelChart.update("none");
  }

  // --- Heatmap ---
  function updateHeatmap(zones) {
    const container = $("heatmap-container");
    if (!zones || zones.length === 0) {
      container.innerHTML = '<div class="no-data">No zone data yet.</div>';
      return;
    }
    container.innerHTML = zones.map(z => `
      <div class="heatmap-row">
        <span class="heatmap-label" title="${z.zone_id}">${z.zone_id}</span>
        <div class="heatmap-bar-wrap">
          <div class="heatmap-bar" style="width:${z.score}%"></div>
        </div>
        <span class="heatmap-score">${Math.round(z.score)}</span>
      </div>`).join("");
  }

  // --- Anomalies ---
  function updateAnomalies(anomalies) {
    const list = $("anomaly-list");
    if (!anomalies || anomalies.length === 0) {
      list.innerHTML = '<span class="muted">No anomalies detected.</span>';
      return;
    }
    list.innerHTML = anomalies.map(a => `
      <div class="anomaly-item ${a.severity}">
        <span class="anomaly-type">${a.anomaly_type.replace(/_/g, " ")}</span>
        <span>${a.description}</span>
      </div>`).join("");
  }

  // --- KPIs ---
  function fmt(n, isPercent = false) {
    if (n === null || n === undefined) return "—";
    return isPercent ? (n * 100).toFixed(1) + "%" : n.toLocaleString();
  }

  function updateKPIs(metrics) {
    $("kpi-visitors").textContent = fmt(metrics.unique_visitors);
    $("kpi-conversion").textContent = fmt(metrics.conversion_rate, true);
    $("kpi-queue").textContent = fmt(metrics.queue_depth);
    $("kpi-abandon").textContent = fmt(metrics.abandonment_rate, true);
  }

  // --- SSE connection ---
  const badge = $("connection-status");
  let es;

  function connect(sid) {
    if (es) es.close();
    badge.className = "badge badge-connecting";
    badge.textContent = "Connecting…";

    es = new EventSource(`/sse/metrics/${sid}`);

    es.onopen = () => {
      badge.className = "badge badge-live";
      badge.textContent = "Live";
    };

    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.error) return;
        updateKPIs(payload.metrics);
        updateFunnel(payload.funnel.stages);
        updateAnomalies(payload.anomalies.anomalies);
        $("last-updated").textContent = "Updated " + new Date(payload.server_time).toLocaleTimeString();
        // Heatmap via separate fetch (not in SSE payload to keep it lightweight)
        fetchHeatmap(sid);
      } catch (_) {}
    };

    es.onerror = () => {
      badge.className = "badge badge-error";
      badge.textContent = "Disconnected";
      es.close();
      setTimeout(() => connect(sid), 5000);
    };
  }

  async function fetchHeatmap(sid) {
    try {
      const res = await fetch(`/stores/${sid}/heatmap`);
      if (!res.ok) return;
      const data = await res.json();
      updateHeatmap(data.zones);
    } catch (_) {}
  }

  // Store selector
  $("store-select").addEventListener("change", e => {
    connect(e.target.value);
  });

  connect(storeId);
})();
