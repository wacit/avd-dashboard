"use strict";

const charts = {};
let autoTimer = null;
let thresholds = {};
let lastData = {}; // dataset key -> rows, for CSV export

const COLORS = ["#38bdf8", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#fb923c"];

// label -> {section, key} for the export dropdown
const DATASETS = {
  "Connections — over time": ["connections", "timeseries"],
  "Connections — active users": ["connections", "active_users"],
  "Connections — by type": ["connections", "by_type"],
  "Connections — top users": ["connections", "top_users"],
  "Errors — over time": ["errors", "timeseries"],
  "Errors — top codes": ["errors", "top_codes"],
  "Errors — by host": ["errors", "by_host"],
  "Host pool — sessions by host": ["hostpool", "sessions"],
  "Host pool — CPU over time": ["hostpool", "cpu_timeseries"],
  "Host pool — memory over time": ["hostpool", "mem_timeseries"],
  "Host pool — CPU by host": ["hostpool", "cpu_by_host"],
  "UX — RTT over time": ["ux", "rtt"],
  "UX — bandwidth over time": ["ux", "bandwidth"],
  "UX — logon duration": ["ux", "logon_duration"],
  "UX — RTT by host": ["ux", "rtt_by_host"],
  "Profile — load over time": ["files", "profile_timeseries"],
  "Profile — load by host": ["files", "profile_by_host"],
};

Chart.defaults.color = "#94a3b8";
Chart.defaults.borderColor = "#334155";
Chart.defaults.font.family = "Segoe UI, system-ui, sans-serif";

function rangeVal() { return document.getElementById("range").value; }
function hostpoolVal() { return document.getElementById("hostpool").value || "all"; }

function fmtTime(iso, range) {
  const d = new Date(iso);
  if (range === "7d" || range === "30d") {
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function destroy(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function noData(canvasId) {
  const cv = document.getElementById(canvasId);
  destroy(canvasId);
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = "#64748b";
  ctx.font = "13px Segoe UI";
  ctx.textAlign = "center";
  ctx.fillText("No data for this range", cv.width / 2, 40);
}

function lineChart(id, rows, xKey, series, range) {
  destroy(id);
  if (!rows || rows.length === 0) return noData(id);
  const labels = rows.map((r) => fmtTime(r[xKey], range));
  const datasets = series.map((s, i) => ({
    label: s.label,
    data: rows.map((r) => r[s.key]),
    borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length] + "33",
    fill: s.fill || false,
    tension: 0.3,
    pointRadius: 0,
    borderWidth: 2,
  }));
  charts[id] = new Chart(document.getElementById(id), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: series.length > 1 } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

function barChart(id, rows, labelKey, valueKey, label) {
  destroy(id);
  if (!rows || rows.length === 0) return noData(id);
  charts[id] = new Chart(document.getElementById(id), {
    type: "bar",
    data: {
      labels: rows.map((r) => String(r[labelKey] ?? "—")),
      datasets: [{
        label,
        data: rows.map((r) => r[valueKey]),
        backgroundColor: "#38bdf8aa",
        borderColor: "#38bdf8",
        borderWidth: 1,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } },
    },
  });
}

function doughnut(id, rows, labelKey, valueKey) {
  destroy(id);
  if (!rows || rows.length === 0) return noData(id);
  charts[id] = new Chart(document.getElementById(id), {
    type: "doughnut",
    data: {
      labels: rows.map((r) => String(r[labelKey] ?? "—")),
      datasets: [{
        data: rows.map((r) => r[valueKey]),
        backgroundColor: COLORS,
        borderColor: "#1e293b",
        borderWidth: 2,
      }],
    },
    options: { responsive: true, plugins: { legend: { position: "right" } } },
  });
}

function renderTable(containerId, rows, cols) {
  const el = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    el.innerHTML = '<div class="empty">No data for this range</div>';
    return;
  }
  const head = cols.map((c) => `<th>${c.label}</th>`).join("");
  const body = rows.map((r) =>
    "<tr>" + cols.map((c) => `<td>${r[c.key] ?? "—"}</td>`).join("") + "</tr>"
  ).join("");
  el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// ---- thresholds: severity for a single metric value ----
function severity(metric, value) {
  const t = thresholds[metric];
  if (t == null || value == null) return "ok";
  if (t.higher_is_better) {
    if (value < t.bad) return "bad";
    if (value < t.warn) return "warn";
    return "ok";
  }
  if (value >= t.bad) return "bad";
  if (value >= t.warn) return "warn";
  return "ok";
}

function renderKpis(k) {
  const items = [
    ["Active users", k.active_users, null],
    ["Connections", k.connections, null],
    ["Success rate", k.success_rate == null ? "—" : k.success_rate + "%", "success_rate", k.success_rate],
    ["Errors", k.errors, "errors", k.errors],
    ["Affected users", k.affected_users, null],
    ["Avg RTT", k.avg_rtt_ms == null ? "—" : k.avg_rtt_ms + " ms", "avg_rtt_ms", k.avg_rtt_ms],
    ["Avg profile load", k.avg_profile_sec == null ? "—" : k.avg_profile_sec + " s", "avg_profile_sec", k.avg_profile_sec],
  ];
  document.getElementById("kpis").innerHTML = items.map(([label, display, metric, raw]) => {
    const cls = metric ? severity(metric, raw) : "";
    return `<div class="kpi"><div class="label">${label}</div>` +
           `<div class="value ${cls}">${display}</div></div>`;
  }).join("");
}

function renderAlerts(k) {
  const checks = [
    ["success_rate", k.success_rate, "Success rate", "%"],
    ["errors", k.errors, "Errors", ""],
    ["avg_rtt_ms", k.avg_rtt_ms, "Avg RTT", " ms"],
    ["avg_profile_sec", k.avg_profile_sec, "Avg profile load", " s"],
  ];
  const msgs = [];
  let worst = "ok";
  for (const [metric, val, label, unit] of checks) {
    const sev = severity(metric, val);
    if (sev === "ok") continue;
    if (sev === "bad") worst = "bad";
    else if (worst !== "bad") worst = "warn";
    const t = thresholds[metric];
    const limit = sev === "bad" ? t.bad : t.warn;
    const cmp = t.higher_is_better ? "below" : "above";
    msgs.push(`${label} is ${val}${unit} — ${sev.toUpperCase()} (${cmp} ${limit}${unit})`);
  }
  const el = document.getElementById("alerts");
  if (msgs.length === 0) { el.classList.add("hidden"); return; }
  el.className = "alerts " + worst;
  el.innerHTML = `<h4>${msgs.length} alert${msgs.length > 1 ? "s" : ""}</h4><ul>` +
    msgs.map((m) => `<li>${m}</li>`).join("") + "</ul>";
}

let allWarnings = [];
function collectWarnings(obj, prefix) {
  (obj.warnings || []).forEach((w) => allWarnings.push(`${prefix}: ${w}`));
}
function showBanner() {
  const b = document.getElementById("banner");
  if (allWarnings.length === 0) { b.classList.add("hidden"); return; }
  b.classList.remove("hidden");
  b.textContent =
    "Some data could not be loaded (table may not exist in this workspace):\n" +
    allWarnings.join("\n");
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function toCSV(rows) {
  if (!rows || rows.length === 0) return "";
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const esc = (v) => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\r\n");
}

function exportCsv() {
  const sel = document.getElementById("dataset");
  const label = sel.value;
  if (!label || !DATASETS[label]) { alert("Pick a dataset to export."); return; }
  const [section, key] = DATASETS[label];
  const rows = (lastData[section] || {})[key];
  if (!rows || rows.length === 0) { alert("No data to export for this selection."); return; }
  const blob = new Blob([toCSV(rows)], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a");
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  a.href = URL.createObjectURL(blob);
  a.download = `avd_${section}_${key}_${rangeVal()}_${stamp}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function populateDatasetSelect() {
  const sel = document.getElementById("dataset");
  for (const label of Object.keys(DATASETS)) {
    const o = document.createElement("option");
    o.value = label;
    o.textContent = label;
    sel.appendChild(o);
  }
}

async function load() {
  const range = rangeVal();
  const hp = encodeURIComponent(hostpoolVal());
  const status = document.getElementById("status");
  status.textContent = "Loading…";
  allWarnings = [];
  const qs = `range=${range}&hostpool=${hp}`;

  try {
    const [ov, conn, err, host, ux, files] = await Promise.all([
      getJSON(`/api/overview?${qs}`),
      getJSON(`/api/connections?${qs}`),
      getJSON(`/api/errors?${qs}`),
      getJSON(`/api/hostpool?${qs}`),
      getJSON(`/api/ux?${qs}`),
      getJSON(`/api/files?${qs}`),
    ]);

    lastData = { connections: conn, errors: err, hostpool: host, ux, files };

    renderKpis(ov.kpis);
    renderAlerts(ov.kpis);
    (ov.warnings || []).forEach((w) => allWarnings.push(`overview: ${w}`));

    lineChart("c_conn", conn.timeseries, "TimeGenerated",
      [{ key: "Started", label: "Attempts" }, { key: "Connected", label: "Connected" }], range);
    lineChart("c_users", conn.active_users, "TimeGenerated",
      [{ key: "Users", label: "Active users", fill: true }], range);
    doughnut("c_ctype", conn.by_type, "ConnectionType", "Count");
    barChart("c_topusers", conn.top_users, "UserName", "Connections", "Connections");
    collectWarnings(conn, "connections");

    lineChart("e_ts", err.timeseries, "TimeGenerated",
      [{ key: "Errors", label: "Errors", fill: true }], range);
    barChart("e_codes", err.top_codes, "CodeSymbolic", "Count", "Count");
    barChart("e_host", err.by_host, "SessionHostName", "Errors", "Errors");
    collectWarnings(err, "errors");

    lineChart("h_cpu", host.cpu_timeseries, "TimeGenerated",
      [{ key: "AvgCPU", label: "Avg CPU %", fill: true }], range);
    lineChart("h_mem", host.mem_timeseries, "TimeGenerated",
      [{ key: "AvgAvailMB", label: "Avail MB", fill: true }], range);
    barChart("h_cpuhost", host.cpu_by_host, "Computer", "AvgCPU", "CPU %");
    renderTable("h_table", host.sessions, [
      { key: "SessionHostName", label: "Host" },
      { key: "Sessions", label: "Sessions" },
      { key: "Users", label: "Users" },
    ]);
    collectWarnings(host, "hostpool");

    lineChart("u_rtt", ux.rtt, "TimeGenerated",
      [{ key: "AvgRTT", label: "Avg RTT" }, { key: "P95RTT", label: "P95 RTT" }], range);
    lineChart("u_bw", ux.bandwidth, "TimeGenerated",
      [{ key: "AvgKBps", label: "Avg KBps", fill: true }], range);
    lineChart("u_logon", ux.logon_duration, "TimeGenerated",
      [{ key: "AvgConnectSec", label: "Avg s" }, { key: "P95ConnectSec", label: "P95 s" }], range);
    barChart("u_rtthost", ux.rtt_by_host, "SessionHostName", "AvgRTT", "Avg RTT");
    collectWarnings(ux, "ux");

    lineChart("f_ts", files.profile_timeseries, "TimeGenerated",
      [{ key: "AvgProfileSec", label: "Avg s" }, { key: "P95ProfileSec", label: "P95 s" }], range);
    barChart("f_host", files.profile_by_host, "SessionHostName", "AvgProfileSec", "Avg s");
    collectWarnings(files, "files");

    showBanner();
    status.textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (e) {
    status.textContent = "Error: " + e.message;
  }
}

function setupAuto() {
  if (autoTimer) clearInterval(autoTimer);
  if (document.getElementById("auto").checked) autoTimer = setInterval(load, 60000);
}

document.getElementById("refresh").addEventListener("click", load);
document.getElementById("range").addEventListener("change", load);
document.getElementById("hostpool").addEventListener("change", load);
document.getElementById("auto").addEventListener("change", setupAuto);
document.getElementById("export").addEventListener("click", exportCsv);

(async function init() {
  populateDatasetSelect();
  try {
    const meta = await getJSON("/api/meta");
    thresholds = meta.thresholds || {};
    if (!meta.workspace_configured) {
      const b = document.getElementById("banner");
      b.classList.remove("hidden");
      b.textContent =
        "AVD_WORKSPACE_ID is not set. Copy .env.example to .env, set your " +
        "Log Analytics workspace ID, run `az login`, then restart the server.";
    }
  } catch (e) { /* meta is best-effort */ }

  try {
    const hp = await getJSON("/api/hostpools");
    const sel = document.getElementById("hostpool");
    (hp.hostpools || []).forEach((name) => {
      const o = document.createElement("option");
      o.value = name;
      o.textContent = name;
      sel.appendChild(o);
    });
  } catch (e) { /* dropdown stays "All host pools" */ }

  setupAuto();
  load();
})();
