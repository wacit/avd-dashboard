# Grafana future state — self-hosted in a Proxmox LXC

Long-range trending, alerting and wallboard views for the AVD DEX
Dashboard, running on your own Proxmox box (WALTAI / casaai style) at zero
license cost. Grafana **complements** the app — the Ops OS stays the
interactive triage tool; Grafana adds what it can't do well:

| Capability | Ops OS (`/os`) | Grafana LXC |
|---|---|---|
| Live triage / drill-down / tenant linking | ✅ | — |
| DEX score computation | ✅ (source of truth) | reads it via API |
| 30–90 day trending, annotations | limited | ✅ |
| Alert rules → Teams / email | shows alerts only | ✅ dispatches |
| Kiosk / TV wallboard, RBAC, folders | — | ✅ |

## What's in this folder

- `setup-grafana-lxc.sh` — run on the **Proxmox host**; creates a Debian 12
  CT (2 vCPU / 1 GB / 8 GB), installs Grafana OSS + the Infinity
  datasource plugin, and provisions everything below.
- `provisioning/datasources/avd-dex.yaml` — two datasources:
  - **Azure Monitor (AVD)** (`azmon-avd`) — KQL against the AVD Insights
    tables using a service principal.
  - **AVD DEX API** (`infinity-avd`) — reads the dashboard's own JSON API
    (`/api/dex`, `/api/dex/agent`) so the score and agent telemetry come
    from the single source of truth.
- `provisioning/dashboards/provider.yaml` + `dashboards/avd-dex.json` —
  the **AVD DEX Overview** dashboard: score stat + trend + factor table,
  ranked users/hosts, connections, RTT, errors, logon and profile-load
  KQL panels, and agent input-delay / host-saturation tables.

## Deploy

1. Create (or reuse) a service principal with **Log Analytics Reader** on
   the workspace. The same SP used by the dashboard's Connections app
   works.
2. Copy this `grafana/` folder to the Proxmox host, then:

```bash
chmod +x setup-grafana-lxc.sh
./setup-grafana-lxc.sh \
  --ctid 240 \
  --tenant-id  <tenant guid> \
  --client-id  <app client id> \
  --client-secret '<secret>' \
  --ip 192.168.1.240/24 --gw 192.168.1.1 \
  --admin-pass '<pick one>'
```

3. Open `http://<ct-ip>:3000`, log in, open **AVD DEX → AVD DEX
   Overview**, and set the two variables at the top:
   - `workspace` — the **full ARM resource ID** of the Log Analytics
     workspace (Portal → workspace → Properties → Resource ID).
   - `dex_url` — the AVD DEX Dashboard base URL **reachable from the CT**
     (e.g. `http://192.168.1.50:8010`). The dashboard must bind
     `AVD_APP_HOST=0.0.0.0` for that; its API is unauthenticated, so keep
     both on the trusted LAN or put a reverse proxy in front.
4. Save the dashboard. Panels refresh every minute.

## Suggested alert rules (build on these panels)

| Rule | Condition | Panel |
|---|---|---|
| Experience score poor | score < 60 for 15 min | Experience score |
| Success rate drop | Connected/Attempts < 0.9 for 30 min | Attempts vs connected |
| RTT degraded | AvgRTT > 150 ms for 30 min | Round-trip time |
| Profile loads slow | AvgProfileSec > 30 for 30 min | FSLogix profile load |
| Error burst | Errors > 25 per interval | Errors over time |

Point a Teams/email contact point at them under Alerting → Contact points.

## Phase 3 (later): long-retention agent data

The agent's SQLite store keeps ~14 days. When you want 90-day agent
history inside Grafana/KQL, dual-write the agent payloads to Azure
Monitor's **Logs Ingestion API** (DCR + custom table `AvdDex_CL`); then
agent signals become KQL-queryable next to the WVD* tables and this
dashboard's Infinity panels can be swapped to KQL. Volume is tiny
(a few MB/host/day at ~$2.76/GB analytics ingestion). Not implemented
yet — tracked as future work.

## Notes

- Update Grafana inside the CT with plain `apt upgrade`.
- The CT is unprivileged, `onboot 1`; back up via normal vzdump jobs.
- Multi-tenant: add a second Azure Monitor datasource per tenant (or a
  second SP), duplicate the dashboard per customer folder, and use
  Grafana folder permissions for per-customer access.
