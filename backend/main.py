"""FastAPI app: serves the dashboard UI and the AVD data API.

Each /api/* endpoint runs a small set of KQL queries against Log Analytics
and returns JSON shaped for the frontend charts. Query failures (e.g. a
table that doesn't exist in this workspace) are returned per-section as
`warnings` rather than failing the whole request.
"""

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import queries as Q
from .azure_client import RANGES, normalize_range, run_query
from .config import settings

app = FastAPI(title="AVD Insights Dashboard")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _scalar(rows: list[dict], key: str, default=0):
    if rows and key in rows[0] and rows[0][key] is not None:
        return rows[0][key]
    return default


def _section(specs: dict[str, str], range_key: str, hostpool: str | None) -> dict:
    """Run a group of named queries; collect data + any warnings."""
    out: dict = {"warnings": []}
    for name, kql in specs.items():
        rows, err = run_query(kql, range_key, hostpool)
        out[name] = rows
        if err:
            out["warnings"].append(f"{name}: {err}")
    return out


@app.get("/api/meta")
def meta():
    return {
        "ranges": list(RANGES.keys()),
        "default_range": settings.default_range,
        "workspace_configured": bool(settings.workspace_id),
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
    conn, e1 = run_query(Q.KPI_CONNECTIONS, rk, hostpool)
    err, e2 = run_query(Q.KPI_ERRORS, rk, hostpool)
    rtt, e3 = run_query(Q.KPI_RTT, rk, hostpool)
    prof, e4 = run_query(Q.KPI_PROFILE, rk, hostpool)

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
            "avg_rtt_ms": _scalar(rtt, "AvgRTT", None),
            "avg_profile_sec": _scalar(prof, "AvgProfileSec", None),
        },
        "warnings": [w for w in (e1, e2, e3, e4) if w],
    }


@app.get("/api/connections")
def connections(range: str = Query("24h"), hostpool: str | None = Query(None)):
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


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")
