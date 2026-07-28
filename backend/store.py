"""SQLite store for AVD DEX agent telemetry.

The agent (agent/AvdDexAgent.ps1) POSTs one payload per session host per
minute to /api/agent/ingest. Payloads land in two tables:

  samples - one row per host cycle (host-level metrics, user NULL) plus
            one row per interactive session (input delay, memory)
  events  - discrete events: app crashes/hangs, FSLogix profile loads

Everything here is stdlib sqlite3; a connection is opened per call which
is plenty for a dashboard-scale write rate (1 payload/host/minute).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    host TEXT NOT NULL,
    hostpool TEXT,
    user TEXT,
    session_id INTEGER,
    state TEXT,
    idle_min REAL,
    input_delay_ms REAL,
    mem_mb REAL,
    host_cpu_pct REAL,
    host_mem_free_mb REAL,
    disk_read_ms REAL,
    disk_write_ms REAL,
    rtt_ms REAL,
    bandwidth_kbps REAL,
    encoding_ms REAL,
    fps REAL,
    frames_skipped_ps REAL,
    packet_loss_pct REAL,
    udp_active INTEGER,
    cpu_queue REAL,
    context_switches_ps REAL,
    pages_ps REAL,
    mem_committed_pct REAL,
    tcp_retrans_ps REAL,
    smb_latency_ms REAL,
    disk_queue REAL,
    disk_free_pct REAL,
    sessions_active INTEGER,
    sessions_disconnected INTEGER,
    unhealthy_services TEXT,
    top_proc TEXT,
    top_proc_mem_mb REAL,
    top_cpu_proc TEXT,
    top_cpu_proc_s REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    host TEXT NOT NULL,
    hostpool TEXT,
    user TEXT,
    kind TEXT NOT NULL,
    source TEXT,
    duration_ms REAL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


def _db_path() -> Path:
    p = Path(settings.agent_db)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript(SCHEMA)
        con.execute("PRAGMA journal_mode=WAL")
        # Upgrade pre-existing DBs created before these columns existed.
        for col, typ in (("state", "TEXT"), ("idle_min", "REAL"),
                         ("packet_loss_pct", "REAL"), ("udp_active", "INTEGER"),
                         ("cpu_queue", "REAL"), ("context_switches_ps", "REAL"),
                         ("pages_ps", "REAL"), ("mem_committed_pct", "REAL"),
                         ("tcp_retrans_ps", "REAL"), ("smb_latency_ms", "REAL"),
                         ("disk_queue", "REAL"), ("disk_free_pct", "REAL"),
                         ("sessions_active", "INTEGER"),
                         ("sessions_disconnected", "INTEGER"),
                         ("unhealthy_services", "TEXT"), ("top_proc", "TEXT"),
                         ("top_proc_mem_mb", "REAL"), ("top_cpu_proc", "TEXT"),
                         ("top_cpu_proc_s", "REAL")):
            try:
                con.execute(f"ALTER TABLE samples ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # already present


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def insert_payload(payload: dict) -> int:
    """Store one agent payload; returns the number of rows written."""
    host = str(payload.get("host") or "").strip()
    if not host:
        raise ValueError("payload is missing 'host'")
    hostpool = (payload.get("hostpool") or None)
    ts = str(payload.get("timestamp") or _utcnow().isoformat())
    hm = payload.get("host_metrics") or {}
    rows = 0
    with _conn() as con:
        con.execute(
            """INSERT INTO samples (ts, host, hostpool, user, session_id,
                 host_cpu_pct, host_mem_free_mb, disk_read_ms, disk_write_ms,
                 rtt_ms, bandwidth_kbps, encoding_ms, fps, frames_skipped_ps,
                 packet_loss_pct, udp_active, cpu_queue, context_switches_ps,
                 pages_ps, mem_committed_pct, tcp_retrans_ps, smb_latency_ms,
                 disk_queue, disk_free_pct, sessions_active,
                 sessions_disconnected, unhealthy_services)
               VALUES (?,?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, host, hostpool,
             _num(hm.get("cpu_pct")), _num(hm.get("mem_free_mb")),
             _num(hm.get("disk_read_ms")), _num(hm.get("disk_write_ms")),
             _num(hm.get("rtt_ms")), _num(hm.get("bandwidth_kbps")),
             _num(hm.get("encoding_ms")), _num(hm.get("fps")),
             _num(hm.get("frames_skipped_ps")),
             _num(hm.get("packet_loss_pct")),
             1 if hm.get("udp_active") else 0,
             _num(hm.get("cpu_queue")), _num(hm.get("context_switches_ps")),
             _num(hm.get("pages_ps")), _num(hm.get("mem_committed_pct")),
             _num(hm.get("tcp_retrans_ps")), _num(hm.get("smb_latency_ms")),
             _num(hm.get("disk_queue")), _num(hm.get("disk_free_pct")),
             _num(hm.get("sessions_active")),
             _num(hm.get("sessions_disconnected")),
             (str(hm.get("unhealthy_services"))
              if hm.get("unhealthy_services") else None)),
        )
        rows += 1
        for s in payload.get("sessions") or []:
            user = str(s.get("user") or "").strip() or None
            con.execute(
                """INSERT INTO samples (ts, host, hostpool, user, session_id,
                     state, idle_min, input_delay_ms, mem_mb,
                     top_proc, top_proc_mem_mb, top_cpu_proc, top_cpu_proc_s)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts, host, hostpool, user, s.get("session_id"),
                 (str(s.get("state")) if s.get("state") else None),
                 _num(s.get("idle_min")),
                 _num(s.get("input_delay_ms")), _num(s.get("mem_mb")),
                 (str(s.get("top_proc")) if s.get("top_proc") else None),
                 _num(s.get("top_proc_mem_mb")),
                 (str(s.get("top_cpu_proc")) if s.get("top_cpu_proc") else None),
                 _num(s.get("top_cpu_proc_s"))),
            )
            rows += 1
        for e in payload.get("events") or []:
            kind = str(e.get("kind") or "").strip()
            if not kind:
                continue
            con.execute(
                """INSERT INTO events (ts, host, hostpool, user, kind, source,
                     duration_ms, message)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (str(e.get("ts") or ts), host, hostpool,
                 (e.get("user") or None), kind, (e.get("source") or None),
                 _num(e.get("duration_ms")),
                 str(e.get("message") or "")[:500]),
            )
            rows += 1
        # Retention: cheap enough to run on every ingest given the indexes.
        cutoff = (_utcnow() - timedelta(days=settings.agent_retention_days)).isoformat()
        con.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        con.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    return rows


def _range_clause(hours: int, hostpool: str | None):
    cutoff = (_utcnow() - timedelta(hours=hours)).isoformat()
    where = "ts >= ?"
    args: list = [cutoff]
    if hostpool and hostpool != "all":
        where += " AND lower(hostpool) = ?"
        args.append(hostpool.lower())
    return where, args


def status() -> dict:
    """Overall agent-feed status regardless of range."""
    with _conn() as con:
        last = con.execute("SELECT MAX(ts) AS t FROM samples").fetchone()["t"]
        recent = (_utcnow() - timedelta(minutes=10)).isoformat()
        hosts = con.execute(
            "SELECT COUNT(DISTINCT host) AS n FROM samples WHERE ts >= ?", (recent,)
        ).fetchone()["n"]
    return {"available": last is not None, "last_sample": last, "hosts_reporting": hosts}


def summaries(hours: int, hostpool: str | None) -> dict:
    """Aggregates used by the DEX scorer: env-level plus per-user/per-host."""
    where, args = _range_clause(hours, hostpool)
    out = {"env": {}, "users": {}, "hosts": {}, "status": status()}
    with _conn() as con:
        env = con.execute(
            f"""SELECT AVG(input_delay_ms) AS input_delay_ms, AVG(fps) AS fps,
                       AVG(host_cpu_pct) AS host_cpu_pct,
                       AVG(packet_loss_pct) AS packet_loss_pct,
                       AVG(smb_latency_ms) AS smb_latency_ms,
                       AVG(cpu_queue) AS cpu_queue
                FROM samples WHERE {where}""", args
        ).fetchone()
        crashes = con.execute(
            f"""SELECT COUNT(*) AS n FROM events
                WHERE kind IN ('app_crash','app_hang') AND {where}""", args
        ).fetchone()["n"]
        out["env"] = {
            "input_delay_ms": env["input_delay_ms"],
            "fps": env["fps"],
            "host_cpu_pct": env["host_cpu_pct"],
            "packet_loss_pct": env["packet_loss_pct"],
            "smb_latency_ms": env["smb_latency_ms"],
            "cpu_queue": env["cpu_queue"],
            "app_crashes": crashes if env["input_delay_ms"] is not None or crashes else None,
        }
        for r in con.execute(
            f"""SELECT user, AVG(input_delay_ms) AS input_delay_ms
                FROM samples WHERE user IS NOT NULL AND {where} GROUP BY user""", args
        ):
            out["users"][r["user"].lower()] = {"input_delay_ms": r["input_delay_ms"]}
        for r in con.execute(
            f"""SELECT user, COUNT(*) AS n FROM events
                WHERE kind IN ('app_crash','app_hang') AND user IS NOT NULL AND {where}
                GROUP BY user""", args
        ):
            out["users"].setdefault(r["user"].lower(), {})["app_crashes"] = r["n"]
        for r in con.execute(
            f"""SELECT host, AVG(host_cpu_pct) AS host_cpu_pct, AVG(fps) AS fps,
                       AVG(input_delay_ms) AS input_delay_ms,
                       AVG(packet_loss_pct) AS packet_loss_pct,
                       AVG(smb_latency_ms) AS smb_latency_ms,
                       AVG(cpu_queue) AS cpu_queue
                FROM samples WHERE {where} GROUP BY host""", args
        ):
            out["hosts"][r["host"].lower()] = {
                "host_cpu_pct": r["host_cpu_pct"],
                "fps": r["fps"],
                "input_delay_ms": r["input_delay_ms"],
                "packet_loss_pct": r["packet_loss_pct"],
                "smb_latency_ms": r["smb_latency_ms"],
                "cpu_queue": r["cpu_queue"],
            }
        for r in con.execute(
            f"""SELECT host, COUNT(*) AS n FROM events
                WHERE kind IN ('app_crash','app_hang') AND {where} GROUP BY host""", args
        ):
            out["hosts"].setdefault(r["host"].lower(), {})["app_crashes"] = r["n"]
    return out


def panels(hours: int, hostpool: str | None) -> dict:
    """Rows for the in-session telemetry panels on the dashboard."""
    where, args = _range_clause(hours, hostpool)
    # Bucket size: minutes for short ranges, hours otherwise.
    bucket = "strftime('%Y-%m-%dT%H:%M:00Z', ts)" if hours <= 1 \
        else "strftime('%Y-%m-%dT%H:00:00Z', ts)"
    with _conn() as con:
        input_by_user = [dict(r) for r in con.execute(
            f"""SELECT user AS User,
                       ROUND(AVG(input_delay_ms), 1) AS AvgInputDelayMs,
                       ROUND(MAX(input_delay_ms), 1) AS MaxInputDelayMs
                FROM samples
                WHERE user IS NOT NULL AND input_delay_ms IS NOT NULL AND {where}
                GROUP BY user ORDER BY AvgInputDelayMs DESC LIMIT 15""", args)]
        input_ts = [dict(r) for r in con.execute(
            f"""SELECT {bucket} AS TimeGenerated,
                       ROUND(AVG(input_delay_ms), 1) AS AvgInputDelayMs,
                       ROUND(MAX(input_delay_ms), 1) AS MaxInputDelayMs
                FROM samples WHERE input_delay_ms IS NOT NULL AND {where}
                GROUP BY 1 ORDER BY 1""", args)]
        host_resources = [dict(r) for r in con.execute(
            f"""SELECT host AS Host,
                       ROUND(AVG(host_cpu_pct), 1) AS AvgCpuPct,
                       ROUND(AVG(cpu_queue), 1) AS AvgCpuQueue,
                       ROUND(MIN(host_mem_free_mb), 0) AS MinMemFreeMb,
                       ROUND(AVG(mem_committed_pct), 1) AS AvgCommitPct,
                       ROUND(AVG(disk_read_ms), 1) AS AvgDiskReadMs,
                       ROUND(AVG(smb_latency_ms), 1) AS AvgSmbMs,
                       ROUND(MIN(disk_free_pct), 1) AS MinDiskFreePct,
                       ROUND(AVG(rtt_ms), 1) AS AvgRttMs,
                       ROUND(AVG(packet_loss_pct), 2) AS AvgLossPct,
                       ROUND(AVG(tcp_retrans_ps), 1) AS AvgRetransPs,
                       ROUND(AVG(fps), 1) AS AvgFps,
                       ROUND(100.0 * AVG(udp_active), 0) AS UdpPct,
                       MAX(sessions_active) AS MaxActive,
                       MAX(sessions_disconnected) AS MaxDisc
                FROM samples
                WHERE user IS NULL AND {where}
                GROUP BY host ORDER BY AvgCpuPct DESC LIMIT 25""", args)]
        # Latest unhealthy-services report per host (10th signal: service health)
        services = [dict(r) for r in con.execute(
            f"""SELECT host AS Host, unhealthy_services AS Services,
                       MAX(ts) AS LastSeen
                FROM samples
                WHERE unhealthy_services IS NOT NULL AND {where}
                GROUP BY host ORDER BY LastSeen DESC LIMIT 15""", args)]
        top_apps = [dict(r) for r in con.execute(
            f"""SELECT top_cpu_proc AS App,
                       COUNT(DISTINCT user) AS Users,
                       ROUND(MAX(top_cpu_proc_s), 0) AS MaxCpuSec,
                       ROUND(MAX(top_proc_mem_mb), 0) AS MaxMemMb
                FROM samples
                WHERE top_cpu_proc IS NOT NULL AND {where}
                GROUP BY top_cpu_proc ORDER BY MaxCpuSec DESC LIMIT 12""", args)]
        gpo_times = [dict(r) for r in con.execute(
            f"""SELECT user AS User,
                       ROUND(AVG(duration_ms) / 1000.0, 1) AS AvgGpoSec,
                       ROUND(MAX(duration_ms) / 1000.0, 1) AS MaxGpoSec,
                       COUNT(*) AS Logons
                FROM events
                WHERE kind = 'gpo_processing' AND {where}
                GROUP BY user ORDER BY AvgGpoSec DESC LIMIT 15""", args)]
        # Latest sample per still-connected session: disconnected/idle
        # sessions holding resources (RDPSoft-style idle tracking).
        idle_sessions = [dict(r) for r in con.execute(
            f"""SELECT user AS User, host AS Host,
                       MAX(state) AS State,
                       ROUND(MAX(idle_min), 0) AS IdleMin,
                       ROUND(MAX(mem_mb), 0) AS MemMb
                FROM samples
                WHERE user IS NOT NULL AND {where}
                  AND ts = (SELECT MAX(ts) FROM samples s2
                            WHERE s2.host = samples.host AND s2.user IS NOT NULL)
                GROUP BY user, host
                HAVING State = 'Disc' OR IdleMin >= 30
                ORDER BY IdleMin DESC LIMIT 20""", args)]
        crashes = [dict(r) for r in con.execute(
            f"""SELECT source AS App, kind AS Kind, COUNT(*) AS Count,
                       MAX(ts) AS LastSeen
                FROM events
                WHERE kind IN ('app_crash','app_hang') AND {where}
                GROUP BY source, kind ORDER BY Count DESC LIMIT 15""", args)]
        profile_loads = [dict(r) for r in con.execute(
            f"""SELECT user AS User,
                       ROUND(AVG(duration_ms) / 1000.0, 1) AS AvgLoadSec,
                       COUNT(*) AS Loads
                FROM events
                WHERE kind = 'profile_load' AND {where}
                GROUP BY user ORDER BY AvgLoadSec DESC LIMIT 15""", args)]
    return {
        "input_delay_by_user": input_by_user,
        "input_delay_timeseries": input_ts,
        "host_resources": host_resources,
        "idle_sessions": idle_sessions,
        "services": services,
        "top_apps": top_apps,
        "gpo_times": gpo_times,
        "crashes": crashes,
        "profile_loads": profile_loads,
        "status": status(),
        "warnings": [],
    }
