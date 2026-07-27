"""FastAPI app: serves the dashboard UI, the AVD data API and the DEX
agent ingest endpoint.

Each /api/* endpoint runs a set of KQL queries against Log Analytics
(concurrently, via run_queries) and returns JSON shaped for the frontend.
Query failures (e.g. a table that doesn't exist in this workspace) are
returned per-section as `warnings` rather than failing the whole request.

/api/dex merges Log Analytics factors with in-session telemetry from the
AVD DEX agent (see agent/) and scores every user / host 0-100.
"""

import hmac
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import connections, dex, queries as Q, store
from .azure_client import RANGES, normalize_range, run_queries, run_query
from .config import settings

app = FastAPI(title="AVD DEX Dashboard")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

RANGE_HOURS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}


@app.on_event("startup")
def _startup():
    store.init_db()


def _scalar(rows: list[dict] | None, key: str, default=0):
    if rows and key in rows[0] and rows[0][key] is not None:
        return rows[0][key]
    return default


def _section(specs: dict[str, str], range_key: str, hostpool: str | None) -> dict:
    """Run a group of named queries; collect data + any warnings."""
    results, warnings = run_queries(specs, range_key, hostpool)
    out: dict = {name: (rows or []) for name, rows in results.items()}
    out["warnings"] = warnings
    return out


# ---------------------------------------------------------------- DEX ----

def _short_name(name: str) -> str:
    """Normalize identities for LA<->agent matching.

    Log Analytics reports UPNs (user@contoso.com) and FQDN hosts
    (avd-01.contoso.com); the agent reports SAM names (CONTOSO\\user or
    user) and short hostnames. Compare on the lowercase short form.
    """
    n = (name or "").lower()
    n = n.split("@")[0].split("\\")[-1]
    return n.split(".")[0] if "." in n else n


def _build_entities(data: dict, kind: str, agent_map: dict) -> list[dict]:
    """Merge the per-entity factor query results into scored rows."""
    key = "UserName" if kind == "user" else "SessionHostName"
    conn = data.get(f"{kind}_conn")
    logon = data.get(f"{kind}_logon")
    rtt = data.get(f"{kind}_rtt")
    errors = data.get(f"{kind}_errors")
    profile = data.get(f"{kind}_profile")
    stability = data.get(f"{kind}_stability")

    ents: dict[str, dict] = defaultdict(dict)
    if conn is not None:
        for r in conn:
            n = r.get(key)
            if not n:
                continue
            ents[n]["attempts"] = r.get("Attempts") or 0
            ents[n]["connected"] = r.get("Connected") or 0
    if logon is not None:
        for r in logon:
            if r.get(key):
                ents[r[key]]["logon_sec"] = r.get("AvgSec")
    if rtt is not None:
        for r in rtt:
            if r.get(key):
                ents[r[key]]["rtt_ms"] = r.get("AvgRTT")
    if errors is not None:
        for r in errors:
            if r.get(key):
                ents[r[key]]["errors"] = r.get("Errors") or 0
    if profile is not None:
        for r in profile:
            if r.get(key):
                ents[r[key]]["profile_sec"] = r.get("AvgSec")
    if stability is not None:
        for r in stability:
            if r.get(key):
                ents[r[key]]["short_session_pct"] = r.get("ShortPct")

    rows = []
    for name, e in ents.items():
        metrics = {
            "logon_sec": e.get("logon_sec"),
            "rtt_ms": e.get("rtt_ms"),
            "profile_sec": e.get("profile_sec"),
            "short_session_pct": e.get("short_session_pct"),
        }
        attempts, connected = e.get("attempts"), e.get("connected")
        if attempts:
            metrics["success_rate"] = min(100.0, (connected or 0) / attempts * 100)
        if errors is not None and connected is not None:
            metrics["errors_per_conn"] = (e.get("errors") or 0) / max(connected, 1)

        agent = agent_map.get(_short_name(name)) or {}
        for k in ("input_delay_ms", "fps", "host_cpu_pct", "app_crashes", "packet_loss_pct"):
            if agent.get(k) is not None:
                metrics[k] = agent[k]

        scored = dex.score_entity(metrics)
        rows.append({
            "name": name,
            "score": scored["score"],
            "grade": scored["grade"],
            "sessions": connected,
            "short_session_pct": metrics.get("short_session_pct"),
            "logon_sec": metrics.get("logon_sec"),
            "rtt_ms": metrics.get("rtt_ms"),
            "errors": e.get("errors"),
            "profile_sec": metrics.get("profile_sec"),
            "input_delay_ms": round(agent["input_delay_ms"], 1)
                if agent.get("input_delay_ms") is not None else None,
            "app_crashes": agent.get("app_crashes"),
        })
    # Worst experience first; unscored entities last.
    rows.sort(key=lambda r: (r["score"] is None, r["score"]))
    return rows[:50]


def _wavg(rows: list[dict] | None, value_key: str, count_key: str = "Count"):
    if not rows:
        return None
    num = den = 0.0
    for r in rows:
        v, c = r.get(value_key), r.get(count_key) or 0
        if v is None or not c:
            continue
        num += float(v) * c
        den += c
    return num / den if den else None


def _env_metrics(data: dict, agent_env: dict) -> dict:
    metrics: dict = {}
    conn = data.get("user_conn")
    if conn:
        attempts = sum(r.get("Attempts") or 0 for r in conn)
        connected = sum(r.get("Connected") or 0 for r in conn)
        if attempts:
            metrics["success_rate"] = min(100.0, connected / attempts * 100)
        if data.get("user_errors") is not None:
            total_err = sum(r.get("Errors") or 0 for r in data["user_errors"])
            metrics["errors_per_conn"] = total_err / max(connected, 1)
    metrics["logon_sec"] = _wavg(data.get("user_logon"), "AvgSec")
    metrics["rtt_ms"] = _wavg(data.get("user_rtt"), "AvgRTT")
    metrics["profile_sec"] = _wavg(data.get("user_profile"), "AvgSec")
    metrics["short_session_pct"] = _wavg(data.get("user_stability"), "ShortPct", "Sessions")
    for k in ("input_delay_ms", "fps", "host_cpu_pct", "app_crashes", "packet_loss_pct"):
        if agent_env.get(k) is not None:
            metrics[k] = agent_env[k]
    return metrics


def _dex_trend(data: dict) -> list[dict]:
    """Score the environment per time bucket from the binned queries."""
    bins: dict[str, dict] = {}

    def slot(r):
        return bins.setdefault(str(r.get("TimeGenerated")), {"t": r.get("TimeGenerated")})

    for r in data.get("trend_conn") or []:
        b = slot(r)
        b["started"], b["connected"] = r.get("Started"), r.get("Connected")
    for r in data.get("trend_rtt") or []:
        slot(r)["rtt_ms"] = r.get("AvgRTT")
    for r in data.get("trend_logon") or []:
        slot(r)["logon_sec"] = r.get("AvgConnectSec")
    for r in data.get("trend_err") or []:
        slot(r)["errors"] = r.get("Errors")

    out = []
    for k in sorted(bins):
        b = bins[k]
        metrics: dict = {"rtt_ms": b.get("rtt_ms"), "logon_sec": b.get("logon_sec")}
        if b.get("started"):
            metrics["success_rate"] = min(100.0, (b.get("connected") or 0) / b["started"] * 100)
            if b.get("errors") is not None:
                metrics["errors_per_conn"] = b["errors"] / max(b.get("connected") or 1, 1)
        s = dex.score_entity(metrics)
        if s["score"] is not None:
            out.append({"TimeGenerated": b["t"], "Score": s["score"]})
    return out


@app.get("/api/dex")
def dex_view(range: str = Query("24h"), hostpool: str | None = Query(None)):
    rk = normalize_range(range)
    data, warnings = run_queries(
        {
            "user_conn": Q.DEX_USER_CONN,
            "user_logon": Q.DEX_USER_LOGON,
            "user_rtt": Q.DEX_USER_RTT,
            "user_errors": Q.DEX_USER_ERRORS,
            "user_profile": Q.DEX_USER_PROFILE,
            "user_stability": Q.DEX_USER_STABILITY,
            "host_conn": Q.DEX_HOST_CONN,
            "host_logon": Q.DEX_HOST_LOGON,
            "host_rtt": Q.DEX_HOST_RTT,
            "host_errors": Q.DEX_HOST_ERRORS,
            "host_profile": Q.DEX_HOST_PROFILE,
            "host_stability": Q.DEX_HOST_STABILITY,
            "trend_conn": Q.CONN_TIMESERIES,
            "trend_err": Q.ERR_TIMESERIES,
            "trend_rtt": Q.UX_RTT_TIMESERIES,
            "trend_logon": Q.UX_LOGON_DURATION,
            "logon_phases": Q.DEX_LOGON_PHASES,
            "graphics": Q.DEX_GRAPHICS_TIMESERIES,
        },
        rk,
        hostpool,
    )
    agent = store.summaries(RANGE_HOURS.get(rk, 24), hostpool)

    env = dex.score_entity(_env_metrics(data, agent["env"]))
    return {
        "range": rk,
        "environment": env,
        "trend": _dex_trend(data),
        "users": _build_entities(data, "user", agent["users"]),
        "hosts": _build_entities(data, "host", agent["hosts"]),
        "logon_phases": data.get("logon_phases") or [],
        "graphics": data.get("graphics") or [],
        "agent": agent["status"],
        "factor_model": dex.FACTORS,
        "warnings": warnings,
    }


@app.get("/api/dex/agent")
def dex_agent(range: str = Query("24h"), hostpool: str | None = Query(None)):
    rk = normalize_range(range)
    return store.panels(RANGE_HOURS.get(rk, 24), hostpool)


# ------------------------------------------------------- agent ingest ----

@app.post("/api/agent/ingest")
def agent_ingest(payload: dict, x_agent_key: str = Header(default="")):
    if not settings.agent_key:
        raise HTTPException(503, "Ingest disabled: set AVD_AGENT_KEY on the server.")
    if not hmac.compare_digest(x_agent_key, settings.agent_key):
        raise HTTPException(401, "Invalid X-Agent-Key.")
    try:
        rows = store.insert_payload(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"accepted": rows}


@app.get("/api/agent/status")
def agent_status():
    return store.status()


# ------------------------------------------------------ tenant linking ----

@app.get("/api/connect/status")
def connect_status():
    return connections.status()


@app.post("/api/connect/device/start")
def connect_device_start(payload: dict | None = None):
    tenant = (payload or {}).get("tenant_id") or None
    return {"flow_id": connections.start_device_flow(tenant)}


@app.get("/api/connect/device/poll")
def connect_device_poll(flow_id: str = Query(...)):
    return connections.poll_flow(flow_id)


@app.post("/api/connect/sp")
def connect_sp(payload: dict):
    tenant = (payload.get("tenant_id") or "").strip()
    client = (payload.get("client_id") or "").strip()
    secret = payload.get("client_secret") or ""
    if not (tenant and client and secret):
        raise HTTPException(422, "tenant_id, client_id and client_secret are required.")
    try:
        connections.connect_sp(tenant, client, secret)
    except Exception as exc:  # noqa: BLE001 - auth errors go back to the UI
        raise HTTPException(400, f"Sign-in failed: {exc}") from exc
    return connections.status()


@app.get("/api/connect/workspaces")
def connect_workspaces():
    try:
        return {"workspaces": connections.list_workspaces()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not list workspaces: {exc}") from exc


@app.post("/api/connect/workspace")
def connect_workspace(payload: dict):
    cid = (payload.get("workspace_id") or "").strip()
    if not cid:
        raise HTTPException(422, "workspace_id is required.")
    connections.set_workspace(cid, payload.get("name"))
    return connections.status()


@app.post("/api/connect/disconnect")
def connect_disconnect():
    connections.disconnect()
    return connections.status()


# ------------------------------------------------------ classic panels ----

@app.get("/api/meta")
def meta():
    return {
        "ranges": list(RANGES.keys()),
        "default_range": settings.default_range,
        "workspace_configured": bool(connections.workspace_id()),
        "agent_ingest_configured": bool(settings.agent_key),
        "thresholds": settings.thresholds,
    }


@app.get("/api/hostpools")
def hostpools():
    # Use the widest range so the dropdown is stable regardless of the
    # currently selected time window.
    rows, err = run_query(Q.HOSTPOOLS, "30d")
    return {
        "hostpools": [r["HostPool"] for r in rows if r.get("HostPool")],
        "warnings": [err] if err else [],
    }


@app.get("/api/overview")
def overview(range: str = Query("24h"), hostpool: str | None = Query(None)):
    rk = normalize_range(range)
    results, warnings = run_queries(
        {"conn": Q.KPI_CONNECTIONS, "err": Q.KPI_ERRORS,
         "rtt": Q.KPI_RTT, "prof": Q.KPI_PROFILE},
        rk, hostpool,
    )
    conn, err = results["conn"], results["err"]
    attempts = _scalar(conn, "Attempts")
    connected = _scalar(conn, "Connected")
    success_rate = round(connected / attempts * 100, 1) if attempts else None

    return {
        "range": rk,
        "kpis": {
            "active_users": _scalar(conn, "ActiveUsers"),
            "connections": connected,
            "success_rate": success_rate,
            "errors": _scalar(err, "Errors"),
            "affected_users": _scalar(err, "AffectedUsers"),
            "avg_rtt_ms": _scalar(results["rtt"], "AvgRTT", None),
            "avg_profile_sec": _scalar(results["prof"], "AvgProfileSec", None),
        },
        "warnings": warnings,
    }


@app.get("/api/connections")
def connections_section(range: str = Query("24h"), hostpool: str | None = Query(None)):
    return _section(
        {
            "timeseries": Q.CONN_TIMESERIES,
            "active_users": Q.CONN_ACTIVE_USERS,
            "by_type": Q.CONN_BY_TYPE,
            "top_users": Q.CONN_TOP_USERS,
        },
        range,
        hostpool,
    )


@app.get("/api/errors")
def errors(range: str = Query("24h"), hostpool: str | None = Query(None)):
    return _section(
        {
            "timeseries": Q.ERR_TIMESERIES,
            "top_codes": Q.ERR_TOP_CODES,
            "by_host": Q.ERR_BY_HOST,
        },
        range,
        hostpool,
    )


@app.get("/api/hostpool")
def hostpool_metrics(range: str = Query("24h"), hostpool: str | None = Query(None)):
    return _section(
        {
            "sessions": Q.HOST_SESSIONS,
            "cpu_timeseries": Q.PERF_CPU_TIMESERIES,
            "mem_timeseries": Q.PERF_MEM_TIMESERIES,
            "cpu_by_host": Q.PERF_CPU_BY_HOST,
            "agent_health": Q.DEX_AGENT_HEALTH,
        },
        range,
        hostpool,
    )


@app.get("/api/ux")
def ux(range: str = Query("24h"), hostpool: str | None = Query(None)):
    return _section(
        {
            "rtt": Q.UX_RTT_TIMESERIES,
            "bandwidth": Q.UX_BANDWIDTH_TIMESERIES,
            "rtt_by_host": Q.UX_RTT_BY_HOST,
            "logon_duration": Q.UX_LOGON_DURATION,
        },
        range,
        hostpool,
    )


@app.get("/api/files")
def files(range: str = Query("24h"), hostpool: str | None = Query(None)):
    return _section(
        {
            "profile_timeseries": Q.PROFILE_TIMESERIES,
            "profile_by_host": Q.PROFILE_BY_HOST,
        },
        range,
        hostpool,
    )


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/os")
def os_shell():
    """Browser-OS shell: windowed desktop UI over the same API."""
    return FileResponse(FRONTEND_DIR / "os.html")


@app.get("/runbook")
def runbook():
    """Per-tenant onboarding runbook (docs/Tenant-Onboarding-Runbook.html)."""
    return FileResponse(
        FRONTEND_DIR.parent / "docs" / "Tenant-Onboarding-Runbook.html"
    )


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")
