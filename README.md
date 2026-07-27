# AVD DEX Dashboard

Two front ends over the same backend:

- **`/`** — the classic single-page dashboard.
- **`/os`** — **AVD Ops OS**, a browser-desktop shell: each monitoring view
  is a draggable/resizable app window (Experience Score, Score Trend, User
  and Host Experience, Logon Waterfall, Agent Telemetry, Network &
  Graphics, Errors, Host Performance, FSLogix Profiles, Settings), with a
  taskbar, start menu, desktop icons, DEX-score tray pill, grade-change
  toasts, and per-window layout persistence (localStorage). Same data,
  same auth, no build step.

A self-hosted **digital employee experience (DEX)** dashboard for Azure
Virtual Desktop, modeled on commercial tools like eG Innovations / Nexthink:
instead of only charting raw metrics, it scores every **user**, **session
host** and the **whole environment** 0-100 from weighted experience factors
and ranks the worst experiences first.

Data comes from two sources:

1. **Log Analytics** — the same AVD Insights tables your workbook uses
   (`WVDConnections`, `WVDErrors`, `WVDCheckpoints`,
   `WVDConnectionNetworkData`, `WVDConnectionGraphicsDataPreview`,
   `WVDAgentHealthStatus`, `Perf`).
2. **The AVD DEX agent** (this repo, `agent/`) — a lightweight PowerShell
   collector on each session host that reports the in-session signals Log
   Analytics cannot see: input delay per user, frame rate / encoding time,
   RDP RTT from the host side, disk latency, per-session memory, app
   crashes/hangs and FSLogix profile-load events.

## What's on the page

- **Experience score hero** — 0-100 gauge, per-factor breakdown (each factor
  shows its raw value, sub-score and source), and score trend over time.
- **DEX drill-down** — users and session hosts ranked worst-first with
  factor columns (logon, RTT, errors, profile load, input delay, crashes);
  logon milestone waterfall from `WVDCheckpoints`; graphics FPS /
  end-to-end delay.
- **In-session telemetry** — agent panels: input delay by user/over time,
  host resources, app crashes, FSLogix loads. Shows a deploy hint until the
  agent reports.
- **Classic panels** — connections, errors, host pool performance (now with
  AVD agent health), network UX, FSLogix profile load.
- Host-pool filter, time range, CSV export of any dataset, alert
  thresholds, and a **light/dark theme toggle**.

## The scoring model

Each factor maps a raw metric linearly onto 0-100 between a "good" and
"bad" band; the entity score is the weighted average of available factors
(missing data drops the factor and renormalizes weights — see
`backend/dex.py` to tune bands/weights):

| Factor | Source | Good | Bad | Weight |
|---|---|---|---|---|
| Connection reliability (success %) | Log Analytics | 99% | 85% | 20 |
| Session latency (avg RTT) | Log Analytics | 60 ms | 200 ms | 20 |
| Logon speed | Log Analytics | 20 s | 75 s | 15 |
| Profile load (FSLogix) | Log Analytics | 10 s | 45 s | 10 |
| Error rate (per connection) | Log Analytics | 0.1 | 2 | 10 |
| Connection stability (short-session %) | Log Analytics | 5% | 40% | 10 |
| Input responsiveness | DEX agent | 80 ms | 600 ms | 10 |
| Frame rate | DEX agent | 24 fps | 8 fps | 5 |
| Host CPU pressure | DEX agent | 60% | 95% | 5 |
| App crashes / hangs | DEX agent | 0 | 5 | 5 |
| Packet loss | DEX agent | 0.5% | 5% | 5 |

The factor set follows the DEX failure modes described by eG Innovations and
RDPSoft (Remote Desktop Commander): latency, packet loss, UDP (Shortpath)
availability, input delay, host saturation, connection churn, and
client-side constraints. The agent also reports session state and idle
time, so the dashboard lists disconnected / long-idle sessions that hold
host resources.

## Data-robustness assessment (why the agent exists)

What AVD Insights (Log Analytics) can and cannot tell you about experience:

| DEX signal | Log Analytics | Covered by |
|---|---|---|
| Connection success / errors | Yes (`WVDConnections`, `WVDErrors`) | dashboard |
| Network RTT / bandwidth | Yes, per connection (`WVDConnectionNetworkData`) | dashboard |
| Logon duration + milestones | Approximate (`WVDConnections` + `WVDCheckpoints`) | dashboard |
| FPS / end-to-end delay | Only if the *preview* graphics table is enabled | dashboard (optional) |
| Host CPU / memory | Only with AMA + perf DCR configured | dashboard (optional) |
| **Input delay per user** | **No** | **DEX agent** |
| **App crashes / hangs** | **No** (unless you collect the Application event log) | **DEX agent** |
| **Per-session memory** | **No** | **DEX agent** |
| **Disk latency on the host** | **No** (not in default DCRs) | **DEX agent** |
| **FSLogix load detail** | Only coarse checkpoint timing | **DEX agent** |

Also: Insights data lags minutes and only exists if AMA/DCR + diagnostics
are configured; the agent reports every minute and works even while the
Log Analytics pipeline is still being set up.

## Architecture

```
Session hosts                     Dashboard server (this repo)
+---------------------+           +----------------------------------+
| AvdDexAgent.ps1     |  HTTPS    | FastAPI (backend/)               |
| scheduled task,     +---------->|  POST /api/agent/ingest          |
| 1/min as SYSTEM     | X-Agent-  |   -> SQLite (dex-agent.db)       |
+---------------------+   Key     |  GET /api/dex  (scores)          |
                                  |   -> KQL via azure-monitor-query |
Log Analytics workspace <---------+  GET /api/*    (classic panels)  |
(AVD Insights tables)             |  frontend/ (Chart.js SPA)        |
                                  +----------------------------------+
```

## Prerequisites

- Python 3.10+ (uses the Windows `py` launcher)
- An identity with the **Log Analytics Reader** role on the workspace
- Your Log Analytics **Workspace ID** (GUID) — Azure Portal → Log Analytics
  workspace → Overview → *Workspace ID*

## Setup & run (server)

```powershell
cd avd-dashboard
Copy-Item .env.example .env
notepad .env          # set AVD_WORKSPACE_ID (and AVD_AGENT_KEY for the agent)
az login              # sign in with an account that can read the workspace
.\run.ps1
```

Then open <http://127.0.0.1:8000>.

## Deploy the DEX agent (session hosts)

1. Set `AVD_AGENT_KEY` in the server's `.env` (e.g.
   `[guid]::NewGuid().ToString('N')`) and restart the server. The server
   must be reachable from the session hosts — bind `AVD_APP_HOST=0.0.0.0`
   and put TLS in front of it for production.
2. On each session host, from an elevated prompt:

```powershell
.\agent\Install-AvdDexAgent.ps1 -ServerUrl http://<dashboard-host>:8000 `
    -ApiKey <the key> -HostPool <hostpool-name>
```

3. Verify: `GET /api/agent/status` shows `hosts_reporting`, and the
   "In-session telemetry" section fills in. Agent factors then start
   contributing to every score automatically.

The agent is a scheduled task (`AvdDexAgent`, SYSTEM, every minute) reading
perf counters and event logs — no services, no external dependencies.
Failed uploads spool to `C:\Program Files\AvdDexAgent\spool` and retry.
Uninstall with `Install-AvdDexAgent.ps1 -Uninstall`.

## Onboarding a tenant

Follow **[docs/Tenant-Onboarding-Runbook.html](docs/Tenant-Onboarding-Runbook.html)**
(also served at `/runbook`, linked from the OS Settings app) — phases 0–5
per tenant: prerequisites, server install, tenant linking, workspace
selection, DEX agent rollout, verification, plus multi-tenant layout and
troubleshooting.

## Linking your tenant from the UI (Connections app)

The **Connections** app in `/os` lets an admin link a tenant without
touching `.env`:

1. Open **Connections** (it auto-opens when no workspace is linked).
2. *Sign in with device code* — optionally enter a Tenant ID, then enter
   the shown code at <https://microsoft.com/devicelogin> with an account
   that has **Log Analytics Reader** on the workspace (and **Reader** to
   enumerate workspaces). Or expand *service principal* and enter
   tenant/client/secret.
3. Pick a workspace from the discovered list and click **Use selected
   workspace**. Data loads immediately — no restart.

State persists in `connection.json` (gitignored) plus an MSAL token cache
(DPAPI-protected on Windows), so later runs are silent. **Disconnect**
reverts to `.env` / `DefaultAzureCredential`. The `/api/connect/*`
endpoints are unauthenticated by design — keep the app bound to
`127.0.0.1` (the default) or put real auth in front before exposing it.

## Authentication

Auth to Log Analytics uses `DefaultAzureCredential`: `az login`, a service
principal (`AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET` in `.env`), or managed
identity all work unchanged. Agent ingest is authenticated by the
`X-Agent-Key` shared secret and disabled until `AVD_AGENT_KEY` is set.

## Module map

- `backend/main.py` — FastAPI app; serves both front ends and the JSON API,
  merges Log Analytics factors with agent telemetry into DEX scores.
- `backend/dex.py` — the scoring engine: factor definitions (bands,
  weights, sources) and the 0-100 composite.
- `backend/queries.py` — named KQL constants (`CONN_*`, `ERR_*`, `PERF_*`,
  `UX_*`, `PROFILE_*`, `KPI_*`, `DEX_*`) with `{bin}`/`{HP}` placeholders.
- `backend/azure_client.py` — `LogsQueryClient` wrapper; `run_query()` plus
  the concurrent `run_queries()` used by the DEX endpoint.
- `backend/connections.py` — tenant linking: device-code / service-principal
  sign-in, ARM workspace discovery, `connection.json` persistence.
- `backend/store.py` — SQLite store for agent samples/events + panel queries.
- `backend/config.py` — Pydantic settings bound to `AVD_*` env vars.
- `frontend/` — `index.html`+`app.js`+`styles.css` (classic SPA) and
  `os.html` (AVD Ops OS shell). Vanilla JS, Chart.js via CDN, 60s refresh.
- `agent/` — `AvdDexAgent.ps1` collector + `Install-AvdDexAgent.ps1`.
- `docs/` — tenant onboarding runbook (served at `/runbook`).

## API endpoints

All return JSON; data endpoints take `?range=` (1h|24h|7d|30d) and `?hostpool=`.

| Endpoint | Returns |
|---|---|
| `GET /` · `GET /os` · `GET /runbook` | Classic SPA · Ops OS shell · onboarding runbook |
| `GET /api/meta` | Ranges, workspace/agent-ingest configured flags, thresholds |
| `GET /api/hostpools` | Host pool names (from `_ResourceId`) for the filter |
| `GET /api/dex` | Environment score + factors, score trend, ranked users/hosts, logon milestones, graphics, factor model |
| `GET /api/dex/agent` | Agent panels: input delay (by user / over time), host resources, idle/disconnected sessions, crashes, profile loads |
| `POST /api/agent/ingest` | Agent telemetry intake (requires `X-Agent-Key`) |
| `GET /api/agent/status` | Agent feed health: last sample, hosts reporting |
| `GET /api/connect/status` · `POST …/device/start` · `GET …/device/poll` · `POST …/sp` · `GET …/workspaces` · `POST …/workspace` · `POST …/disconnect` | Tenant linking (Connections app) |
| `GET /api/overview` | KPI scalars for the strip |
| `GET /api/connections` / `errors` / `hostpool` / `ux` / `files` | Classic panel datasets |

Each data endpoint includes a `warnings` array so a missing table degrades
to a banner instead of a failure.

## Deploying to Azure

No Dockerfile is included, but it containerizes cleanly: Python 3.10+ base,
`pip install -r requirements.txt`, run
`uvicorn backend.main:app --host 0.0.0.0 --port 8000`. Use a **managed
identity** (grant it Log Analytics Reader) instead of a secret when hosted
in Azure, and put authentication in front of the app — the `/api/connect/*`
endpoints are unauthenticated by design for localhost use.

## Troubleshooting

See the troubleshooting matrix in the
[tenant onboarding runbook](docs/Tenant-Onboarding-Runbook.html) (`/runbook`).
Quick hits:

- **Every panel "No data"** — no workspace linked: use the Connections app,
  or check `AVD_WORKSPACE_ID` is the workspace *Workspace ID* GUID (not the
  resource ID) and the identity has Log Analytics Reader.
- **CPU / memory / profile panels empty, connections work** — `Perf` /
  `WVDConnectionNetworkData` / `WVDCheckpoints` aren't being collected;
  fix the AVD Insights DCR (see `avd-insite-dashboard` repo's
  `Enable-AvdInsights.ps1`).
- **Agent section empty** — deploy `agent\Install-AvdDexAgent.ps1`; verify
  `GET /api/agent/status` and that `AVD_AGENT_KEY` matches.

## Notes / tuning

- Score bands and weights: `backend/dex.py`. KQL: `backend/queries.py`.
  Alert thresholds for the KPI strip: `AVD_THR_*` env vars.
- Identity matching between Log Analytics (UPN / FQDN) and the agent
  (SAM account / short hostname) is done on the lowercase short name —
  see `_short_name` in `backend/main.py`.
- `WVDConnectionGraphicsDataPreview` is a preview feature; the graphics
  panel and FPS factor simply stay empty if it isn't in your workspace.
- FSLogix checkpoint names vary by agent version; the `PROFILE_*` /
  `DEX_*_PROFILE` queries match names containing `Profile`/`FSLogix`.
- Any failing table shows as a banner warning; the rest of the dashboard
  (and the score, with renormalized weights) keeps working.
