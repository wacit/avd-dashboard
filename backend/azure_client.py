"""Thin wrapper around the Azure Monitor Logs query API.

Auth is handled by DefaultAzureCredential, so it works with `az login`,
a service principal (AZURE_* env vars), or managed identity without code
changes.
"""

import re
from datetime import timedelta

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from .config import settings

# range key -> (timespan, KQL bin size used for time-series grouping)
RANGES: dict[str, tuple[timedelta, str]] = {
    "1h": (timedelta(hours=1), "5m"),
    "24h": (timedelta(hours=24), "1h"),
    "7d": (timedelta(days=7), "6h"),
    "30d": (timedelta(days=30), "1d"),
}

_client: LogsQueryClient | None = None


def get_client() -> LogsQueryClient:
    global _client
    if _client is None:
        _client = LogsQueryClient(DefaultAzureCredential())
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

    if not settings.workspace_id:
        return [], "AVD_WORKSPACE_ID is not set (see .env.example)."

    try:
        resp = get_client().query_workspace(
            workspace_id=settings.workspace_id,
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
