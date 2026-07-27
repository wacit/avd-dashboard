"""Thin wrapper around the Azure Monitor Logs query API.

Auth is handled by DefaultAzureCredential, so it works with `az login`,
a service principal (AZURE_* env vars), or managed identity without code
changes.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from . import connections
from .config import settings  # noqa: F401 - re-exported for callers

# range key -> (timespan, KQL bin size used for time-series grouping)
RANGES: dict[str, tuple[timedelta, str]] = {
    "1h": (timedelta(hours=1), "5m"),
    "24h": (timedelta(hours=24), "1h"),
    "7d": (timedelta(days=7), "6h"),
    "30d": (timedelta(days=30), "1d"),
}

_client: LogsQueryClient | None = None
_client_gen = -1


def get_client() -> LogsQueryClient:
    """LogsQueryClient over the linked credential.

    Rebuilt whenever the connection config changes (connections.generation
    bumps), so linking a tenant in the UI takes effect without a restart.
    """
    global _client, _client_gen
    if _client is None or _client_gen != connections.generation:
        _client = LogsQueryClient(connections.get_credential())
        _client_gen = connections.generation
    return _client


def normalize_range(range_key: str) -> str:
    return range_key if range_key in RANGES else settings.default_range


# Host pool names are Azure resource names: letters, digits, '.', '_', '-'.
# Anything else is rejected so the value can be safely embedded in KQL.
_HP_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _hostpool_clauses(hostpool: str | None) -> dict[str, str]:
    """Build KQL fragments for the selected host pool.

    Returns empty fragments (no filtering) when hostpool is falsy, "all", or
    fails validation.
    """
    if not hostpool or hostpool == "all" or not _HP_RE.match(hostpool):
        return {"HP": "", "HPLET": "", "HPWHERE": ""}

    name = hostpool.lower()
    hp = f'| where tolower(_ResourceId) has "{name}"'
    # Perf has no host-pool column, so derive the pool's session hosts from
    # WVDConnections and filter Perf.Computer by short hostname.
    hplet = (
        "let HPHosts = WVDConnections\n"
        f'| where tolower(_ResourceId) has "{name}"\n'
        "| extend H = tolower(tostring(split(SessionHostName, '.')[0]))\n"
        "| where isnotempty(H)\n"
        "| distinct H;"
    )
    hpwhere = "| where tolower(tostring(split(Computer, '.')[0])) in (HPHosts)"
    return {"HP": hp, "HPLET": hplet, "HPWHERE": hpwhere}


def run_query(
    kql: str, range_key: str, hostpool: str | None = None
) -> tuple[list[dict], str | None]:
    """Run a KQL query for the given range.

    `{bin}` in the query is replaced with the range's bin size. Returns
    (rows, error). On any failure (missing table, auth, etc.) rows is empty
    and error holds a short message so the UI can degrade gracefully instead
    of 500-ing the whole dashboard.
    """
    range_key = normalize_range(range_key)
    span, bin_size = RANGES[range_key]
    query = kql.replace("{bin}", bin_size)
    for token, fragment in _hostpool_clauses(hostpool).items():
        query = query.replace("{" + token + "}", fragment)

    ws = connections.workspace_id()
    if not ws:
        return [], "No workspace linked (use the Connections app, or set AVD_WORKSPACE_ID)."

    try:
        resp = get_client().query_workspace(
            workspace_id=ws,
            query=query,
            timespan=span,
        )
        if resp.status == LogsQueryStatus.SUCCESS:
            tables = resp.tables
        else:
            # Partial results still carry usable rows.
            tables = getattr(resp, "partial_data", None) or []
        if not tables:
            return [], None
        table = tables[0]
        return [dict(zip(table.columns, row)) for row in table.rows], None
    except Exception as exc:  # noqa: BLE001 - surface message, never crash UI
        return [], f"{type(exc).__name__}: {exc}"


def run_queries(
    specs: dict[str, str], range_key: str, hostpool: str | None = None
) -> tuple[dict[str, list[dict] | None], list[str]]:
    """Run several named queries concurrently.

    Returns (results, warnings). results maps name -> rows, or None when
    that query failed — callers use None to drop a factor entirely rather
    than treating "query broke" as "zero rows".
    """
    results: dict[str, list[dict] | None] = {}
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            name: ex.submit(run_query, kql, range_key, hostpool)
            for name, kql in specs.items()
        }
        for name, fut in futures.items():
            rows, err = fut.result()
            if err:
                results[name] = None
                warnings.append(f"{name}: {err}")
            else:
                results[name] = rows
    return results, warnings
