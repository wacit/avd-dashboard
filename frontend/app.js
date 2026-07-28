"use strict";

const charts = {};
let autoTimer = null;
let thresholds = {};
let lastData = {}; // dataset key -> rows, for CSV export

// label -> {section, key} for the export dropdown
const DATASETS = {
  "DEX — users ranked by score": ["dex", "users"],
  "DEX — hosts ranked by score": ["dex", "hosts"],
  "DEX — score trend": ["dex", "trend"],
  "DEX — logon milestones": ["dex", "logon_phases"],
  "DEX — graphics quality": ["dex", "graphics"],
  "Agent — input delay by user": ["dexagent", "input_delay_by_user"],
  "Agent — input delay over time": ["dexagent", "input_delay_timeseries"],
  "Agent — host resources": ["dexagent", "host_resources"],
  "Agent — idle/disconnected sessions": ["dexagent", "idle_sessions"],
  "Agent — top app consumers": ["dexagent", "top_apps"],
  "Agent — GPO processing": ["dexagent", "gpo_times"],
  "Agent — unhealthy services": ["dexagent", "services"],
  "Agent — app crashes": ["dexagent", "crashes"],
  "Agent — profile loads": ["dexagent", "profile_loads"],
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
  "Host pool — AVD agent health": ["hostpool", "agent_health"],
  "UX — RTT over time": ["ux", "rtt"],
  "UX — bandwidth over time": ["ux", "bandwidth"],
  "UX — logon duration": ["ux", "logon_duration"],
  "UX — RTT by host": ["ux", "rtt_by_host"],
  "Profile — load over time": ["files", "profile_timeseries"],
  "Profile — load by host": ["files", "profile_by_host"],
};

// ---- theming ----
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
let COLORS = [];
function applyChartTheme() {
  COLORS = [cssVar("--accent"), cssVar("--ok"), cssVar("--warn"), cssVar("--bad"), "#a78bfa", "#fb923c"];
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--border");
  Chart.defaults.font.family = "Segoe UI, system-ui, sans-serif";
}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("avd-theme", theme); } catch (e) { /* ignore */ }
  applyChartTheme();
}
function toggleTheme() {
  const cur = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  setTheme(cur === "light" ? "dark" : "light");
  load();
}

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
  ctx.fillStyle = cssVar("--muted");
  ctx.font = "13px Segoe UI";
  ctx.textAlign = "center";
  ctx.fillText("No data for this range", cv.width / 2, 40);
}

function lineChart(id, rows, xKey, series, range, opts = {}) {
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
    yAxisID: s.y2 ? "y2" : "y",
  }));
  const scales = { y: { beginAtZero: !opts.noZero } };
  if (series.some((s) => s.y2)) {
    scales.y2 = { position: "right", beginAtZero: true, grid: { drawOnChartArea: false } };
  }
  if (opts.yMax != null) scales.y.max = opts.yMax;
  charts[id] = new Chart(document.getElementById(id), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: series.length > 1 } },
      scales,
    },
  });
}

function barChart(id, rows, labelKey, valueKey, label, extraSeries) {
  destroy(id);
  if (!rows || rows.length === 0) return noData(id);
  const datasets = [{
    label,
    data: rows.map((r) => r[valueKey]),
    backgroundColor: COLORS[0] + "aa",
    borderColor: COLORS[0],
    borderWidth: 1,
  }];
  (extraSeries || []).forEach((s, i) => datasets.push({
    label: s.label,
    data: rows.map((r) => r[s.key]),
    backgroundColor: COLORS[(i + 2) % COLORS.length] + "aa",
    borderColor: COLORS[(i + 2) % COLORS.length],
    borderWidth: 1,
  }));
  charts[id] = new Chart(document.getElementById(id), {
    type: "bar",
    data: { labels: rows.map((r) => String(r[labelKey] ?? "—")), datasets },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: datasets.length > 1 } },
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
        borderColor: cssVar("--card"),
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
  const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${c.label}</th>`).join("");
  const body = rows.map((r) =>
    "<tr>" + cols.map((c) => `<td class="${c.num ? "num" : ""}">${r[c.key] ?? "—"}</td>`).join("") + "</tr>"
  ).join("");
  el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// ---- DEX rendering ----
function scoreSeverity(score) {
  if (score == null) return "na";
  if (score >= 80) return "ok";
  if (score >= 60) return "warn";
  return "bad";
}
function scoreColor(score) {
  return cssVar("--" + (scoreSeverity(score) === "na" ? "muted" : scoreSeverity(score)));
}
function chip(score) {
  return `<span class="chip ${scoreSeverity(score)}">${score == null ? "—" : score}</span>`;
}

function renderGauge(env) {
  const score = env && env.score;
  const el = document.getElementById("dex_score");
  el.textContent = score == null ? "—" : score;
  el.className = "gauge-score " + scoreSeverity(score);
  document.getElementById("dex_grade").textContent = env ? env.grade : "";
  destroy("dex_gauge");
  const val = score == null ? 0 : score;
  charts["dex_gauge"] = new Chart(document.getElementById("dex_gauge"), {
    type: "doughnut",
    data: {
      datasets: [{
        data: [val, 100 - val],
        backgroundColor: [scoreColor(score), cssVar("--track")],
        borderWidth: 0,
      }],
    },
    options: {
      cutout: "78%",
      rotation: -135,
      circumference: 270,
      responsive: true,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

function renderFactors(env) {
  const el = document.getElementById("dex_factors");
  if (!env || !env.factors || env.factors.length === 0) {
    el.innerHTML = '<div class="empty">No factor data for this range</div>';
    return;
  }
  el.innerHTML = env.factors.map((f) => {
    const sev = scoreSeverity(f.score);
    const src = f.source === "agent" ? "agent" : "logs";
    return `<div class="factor">
      <div class="flabel" title="weight ${f.weight}">${f.label}</div>
      <div class="fbar"><span style="width:${f.score}%;background:var(--${sev === "na" ? "muted" : sev})"></span></div>
      <div class="fval">${f.value}${f.unit} <span class="src">· ${src}</span></div>
    </div>`;
  }).join("");
}

const SCORE_HELP = {
  score: "0-100 weighted experience score: 90+ Excellent, 75+ Good, 60+ Fair, below 60 Poor",
  sessions: "Distinct connections in the selected time range",
  logon: "Average seconds from connection start to connected (good <= 20s, bad >= 75s)",
  rtt: "Average network round-trip time (good <= 60ms, bad >= 200ms)",
  errors: "AVD service errors recorded in the range",
  short: "Share of sessions under 5 minutes - disconnect/reconnect churn (good <= 5%)",
  profile: "Average FSLogix profile load seconds (good <= 10s, bad >= 45s)",
  input: "Average max input delay from the DEX agent (good <= 80ms); blank until the agent reports",
  crashes: "App crashes + hangs seen by the DEX agent",
};

function renderScoreTable(containerId, rows, nameLabel, kind) {
  const el = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    el.innerHTML = '<div class="empty">No data for this range</div>';
    return;
  }
  const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d).replace(/\.0$/, ""));
  const H = SCORE_HELP;
  const head = `<tr><th title="Click a name for a detail view (opens the Ops OS)">${nameLabel}</th>` +
    `<th class="num" title="${H.score}">Score</th><th class="num" title="${H.sessions}">Sessions</th>` +
    `<th class="num" title="${H.logon}">Logon s</th><th class="num" title="${H.rtt}">RTT ms</th>` +
    `<th class="num" title="${H.errors}">Errors</th><th class="num" title="${H.short}">Short %</th>` +
    `<th class="num" title="${H.profile}">Profile s</th><th class="num" title="${H.input}">Input ms</th>` +
    `<th class="num" title="${H.crashes}">Crashes</th></tr>`;
  const body = rows.map((r) =>
    `<tr><td><a class="drill" href="/os#${kind}=${encodeURIComponent(r.name)}">${r.name}</a></td>` +
    `<td class="num">${chip(r.score)}</td>` +
    `<td class="num">${r.sessions ?? "—"}</td><td class="num">${fmt(r.logon_sec)}</td>` +
    `<td class="num">${fmt(r.rtt_ms)}</td><td class="num">${r.errors ?? "—"}</td>` +
    `<td class="num">${fmt(r.short_session_pct)}</td>` +
    `<td class="num">${fmt(r.profile_sec)}</td><td class="num">${fmt(r.input_delay_ms)}</td>` +
    `<td class="num">${r.app_crashes ?? "—"}</td></tr>`
  ).join("");
  el.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
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

function renderAlerts(k, envScore) {
  const checks = [
    ["success_rate", k.success_rate, "Success rate", "%"],
    ["errors", k.errors, "Errors", ""],
    ["avg_rtt_ms", k.avg_rtt_ms, "Avg RTT", " ms"],
    ["avg_profile_sec", k.avg_profile_sec, "Avg profile load", " s"],
  ];
  const msgs = [];
  let worst = "ok";
  const bump = (sev) => {
    if (sev === "bad") worst = "bad";
    else if (worst !== "bad") worst = "warn";
  };
  if (envScore != null && envScore < 60) {
    bump("bad");
    msgs.push(`Experience score is ${envScore} — POOR (below 60)`);
  } else if (envScore != null && envScore < 75) {
    bump("warn");
    msgs.push(`Experience score is ${envScore} — FAIR (below 75)`);
  }
  for (const [metric, val, label, unit] of checks) {
    const sev = severity(metric, val);
    if (sev === "ok") continue;
    bump(sev);
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

function renderAgentNote(status) {
  const el = document.getElementById("agent_note");
  if (status && status.available) {
    const last = status.last_sample ? new Date(status.last_sample).toLocaleString() : "unknown";
    el.textContent = `DEX agent feed: ${status.hosts_reporting} host(s) reporting in the last 10 min · last sample ${last}`;
    el.classList.remove("hidden");
  } else {
    el.textContent =
      "No DEX agent telemetry yet. Deploy agent\\Install-AvdDexAgent.ps1 to your session hosts " +
      "to add input delay, frame rate, per-session and app-crash data to the experience score.";
    el.classList.remove("hidden");
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
    const [dexData, dexAgent, ov, conn, err, host, ux, files] = await Promise.all([
      getJSON(`/api/dex?${qs}`),
      getJSON(`/api/dex/agent?${qs}`),
      getJSON(`/api/overview?${qs}`),
      getJSON(`/api/connections?${qs}`),
      getJSON(`/api/errors?${qs}`),
      getJSON(`/api/hostpool?${qs}`),
      getJSON(`/api/ux?${qs}`),
      getJSON(`/api/files?${qs}`),
    ]);

    lastData = { dex: dexData, dexagent: dexAgent, connections: conn, errors: err, hostpool: host, ux, files };

    // ---- DEX hero + drill-down ----
    renderGauge(dexData.environment);
    renderFactors(dexData.environment);
    lineChart("dex_trend", dexData.trend, "TimeGenerated",
      [{ key: "Score", label: "Score", fill: true }], range, { yMax: 100 });
    renderScoreTable("dex_users", dexData.users, "User", "user");
    renderScoreTable("dex_hosts", dexData.hosts, "Session host", "host");
    barChart("dex_logon_phases", dexData.logon_phases, "Name", "AvgSec", "Avg s",
      [{ key: "P95Sec", label: "P95 s" }]);
    lineChart("dex_graphics", dexData.graphics, "TimeGenerated",
      [{ key: "AvgFps", label: "FPS" }, { key: "AvgE2EMs", label: "E2E delay ms", y2: true }], range);
    collectWarnings(dexData, "dex");

    // ---- agent panels ----
    renderAgentNote(dexAgent.status || dexData.agent);
    barChart("a_input_user", dexAgent.input_delay_by_user, "User", "AvgInputDelayMs", "Avg ms",
      [{ key: "MaxInputDelayMs", label: "Max ms" }]);
    lineChart("a_input_ts", dexAgent.input_delay_timeseries, "TimeGenerated",
      [{ key: "AvgInputDelayMs", label: "Avg ms" }, { key: "MaxInputDelayMs", label: "Max ms" }], range);
    renderTable("a_hosts", dexAgent.host_resources, [
      { key: "Host", label: "Host" },
      { key: "AvgCpuPct", label: "CPU %", num: true },
      { key: "AvgCpuQueue", label: "Queue", num: true },
      { key: "MinMemFreeMb", label: "Min free MB", num: true },
      { key: "AvgCommitPct", label: "Commit %", num: true },
      { key: "AvgSmbMs", label: "SMB ms", num: true },
      { key: "MinDiskFreePct", label: "Free disk %", num: true },
      { key: "AvgRttMs", label: "RTT ms", num: true },
      { key: "AvgLossPct", label: "Loss %", num: true },
      { key: "AvgRetransPs", label: "Retrans/s", num: true },
      { key: "AvgFps", label: "FPS", num: true },
      { key: "UdpPct", label: "UDP %", num: true },
      { key: "MaxActive", label: "Act", num: true },
      { key: "MaxDisc", label: "Disc", num: true },
    ]);
    renderTable("a_topapps", dexAgent.top_apps, [
      { key: "App", label: "Process" },
      { key: "Users", label: "Users", num: true },
      { key: "MaxCpuSec", label: "Max CPU s", num: true },
      { key: "MaxMemMb", label: "Max mem MB", num: true },
    ]);
    renderTable("a_gpo", dexAgent.gpo_times, [
      { key: "User", label: "User" },
      { key: "AvgGpoSec", label: "Avg s", num: true },
      { key: "MaxGpoSec", label: "Max s", num: true },
      { key: "Logons", label: "Logons", num: true },
    ]);
    renderTable("a_services", dexAgent.services, [
      { key: "Host", label: "Host" },
      { key: "Services", label: "Stopped services" },
      { key: "LastSeen", label: "Last seen" },
    ]);
    renderTable("a_idle", dexAgent.idle_sessions, [
      { key: "User", label: "User" },
      { key: "Host", label: "Host" },
      { key: "State", label: "State" },
      { key: "IdleMin", label: "Idle min", num: true },
      { key: "MemMb", label: "Mem MB", num: true },
    ]);
    renderTable("a_crashes", dexAgent.crashes, [
      { key: "App", label: "Application" },
      { key: "Kind", label: "Kind" },
      { key: "Count", label: "Count", num: true },
      { key: "LastSeen", label: "Last seen" },
    ]);
    renderTable("a_profiles", dexAgent.profile_loads, [
      { key: "User", label: "User" },
      { key: "AvgLoadSec", label: "Avg load s", num: true },
      { key: "Loads", label: "Loads", num: true },
    ]);

    // ---- overview / classic panels ----
    renderKpis(ov.kpis);
    renderAlerts(ov.kpis, dexData.environment && dexData.environment.score);
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
    doughnut("h_agent", host.agent_health, "Status", "Hosts");
    renderTable("h_table", host.sessions, [
      { key: "SessionHostName", label: "Host" },
      { key: "Sessions", label: "Sessions", num: true },
      { key: "Users", label: "Users", num: true },
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
document.getElementById("theme").addEventListener("click", toggleTheme);

(async function init() {
  let theme = "dark";
  try { theme = localStorage.getItem("avd-theme") || "dark"; } catch (e) { /* ignore */ }
  setTheme(theme);
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
