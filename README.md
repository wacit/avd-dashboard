# AVD Insights Dashboard

A self-hosted dashboard that queries the same Log Analytics workspace your
**AVD Insights** workbook uses, and surfaces the signals you care about on
one page:

- **Connection health** – attempts vs connected, active users, by type, top users
- **Errors & issues** – error trend, top error codes, errors by session host
- **Host pool performance** – CPU %, available memory, CPU by host, sessions by host
- **User experience** – round-trip time, bandwidth, logon/connect time, RTT by host
- **Azure file / FSLogix profile performance** – profile load time trend and by host

Plus:

- **Host-pool filter** – dropdown in the header (auto-populated from your
  data) scopes every panel to one host pool, or "All host pools".
- **Alert thresholds** – KPI cards are colour-coded green/amber/red and an
  alerts strip lists every breached threshold. Thresholds are configurable
  via `AVD_THR_*` env vars (see `.env.example`).
- **CSV export** – pick any dataset from the header dropdown and download it
  as CSV (respects the current time range and host-pool filter).

Failures for any single section (e.g. a table that doesn't exist in your
workspace) show as a banner instead of breaking the dashboard.

## Prerequisites

- Python 3.10+ (uses the Windows `py` launcher)
- An identity with the **Log Analytics Reader** role on the workspace
- Your Log Analytics **Workspace ID** (GUID) — Azure Portal → Log Analytics
  workspace → Overview → *Workspace ID*

## Setup & run

```powershell
cd C:\code\avd-dashboard
Copy-Item .env.example .env
notepad .env          # set AVD_WORKSPACE_ID
az login              # sign in with an account that can read the workspace
.\run.ps1
```

Then open <http://127.0.0.1:8000>.

`run.ps1` creates a `.venv`, installs dependencies, and starts the server
with auto-reload. To run it manually instead:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

## Authentication

Auth uses `DefaultAzureCredential`, so any of these work with no code change:

- **`az login`** (simplest for local use)
- **Service principal** – set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
  `AZURE_CLIENT_SECRET` in `.env` (app needs Log Analytics Reader)
- **Managed identity** – if you later host this in Azure

## Notes / tuning

- Queries target the standard AVD Insights tables: `WVDConnections`,
  `WVDErrors`, `WVDCheckpoints`, `WVDConnectionNetworkData`, `Perf`.
- **FSLogix / Azure Files**: profile-load timing is derived from
  `WVDCheckpoints`. The checkpoint *names* depend on your AVD agent / FSLogix
  version, so `backend/queries.py` matches names containing `Profile`/`FSLogix`.
  If your environment uses different names, edit the `PROFILE_*` queries.
- `WVDConnectionNetworkData` and `Perf` require the network-data feature and
  the VM Insights agent respectively; if you don't collect them those panels
  will simply show "No data" and list a warning in the banner.
- Edit any KQL in `backend/queries.py` to match how your tenant is set up.
- **Host-pool filter**: host pool is derived from `_ResourceId` on the WVD*
  tables. `Perf` has no host-pool column, so when a pool is selected, host
  CPU/memory is scoped to that pool's session hosts (matched by short
  hostname via `WVDConnections`).
- **Thresholds**: override defaults in `.env`, e.g. `AVD_THR_RTT_BAD=200` or
  `AVD_THR_SUCCESS_WARN=99`. For success rate higher is better (warn = OK
  floor, bad = WARN floor); for the rest higher is worse.
