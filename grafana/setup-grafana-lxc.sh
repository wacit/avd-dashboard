#!/usr/bin/env bash
# =============================================================================
# setup-grafana-lxc.sh - Grafana OSS in a Proxmox LXC for the AVD DEX Dashboard
# =============================================================================
# Run ON THE PROXMOX HOST (WALTAI / casaai style). Creates a Debian 12 CT,
# installs Grafana OSS + the Infinity datasource plugin, and provisions:
#   - Azure Monitor datasource  -> AVD Insights tables (KQL panels)
#   - Infinity datasource       -> the dashboard's /api/dex JSON (score, agent)
#   - The AVD DEX dashboard from ./dashboards/avd-dex.json
#
# Usage (copy the whole grafana/ folder to the Proxmox host first):
#   ./setup-grafana-lxc.sh \
#     --ctid 240 \
#     --tenant-id  <entra tenant guid> \
#     --client-id  <app registration client id> \
#     --client-secret '<secret>' \
#     [--hostname grafana] [--storage local-lvm] [--bridge vmbr0]
#     [--ip dhcp | --ip 192.168.1.240/24 --gw 192.168.1.1]
#     [--admin-pass '<grafana admin password>']
#
# The service principal needs: Monitoring Reader (or Log Analytics Reader) on
# the workspace + Reader on the subscription. Same SP as the dashboard's
# Connections app works fine.
# =============================================================================
set -euo pipefail

CTID="" HOSTNAME="grafana" STORAGE="local-lvm" BRIDGE="vmbr0"
IP="dhcp" GW="" ADMIN_PASS="admin"
TENANT_ID="" CLIENT_ID="" CLIENT_SECRET=""
DISK_GB=8 MEM_MB=1024 CORES=2
TEMPLATE_STORE="local"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ctid) CTID="$2"; shift 2 ;;
    --hostname) HOSTNAME="$2"; shift 2 ;;
    --storage) STORAGE="$2"; shift 2 ;;
    --bridge) BRIDGE="$2"; shift 2 ;;
    --ip) IP="$2"; shift 2 ;;
    --gw) GW="$2"; shift 2 ;;
    --admin-pass) ADMIN_PASS="$2"; shift 2 ;;
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --client-id) CLIENT_ID="$2"; shift 2 ;;
    --client-secret) CLIENT_SECRET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$CTID" ]] || { echo "--ctid is required" >&2; exit 1; }
[[ -n "$TENANT_ID" && -n "$CLIENT_ID" && -n "$CLIENT_SECRET" ]] || {
  echo "--tenant-id / --client-id / --client-secret are required (Azure Monitor datasource)" >&2
  exit 1
}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$SCRIPT_DIR/dashboards/avd-dex.json" ]] || {
  echo "dashboards/avd-dex.json not found next to this script - copy the whole grafana/ folder." >&2
  exit 1
}

# ---------------------------------------------------------------- template --
TEMPLATE="$(pveam list "$TEMPLATE_STORE" 2>/dev/null | awk '/debian-12-standard/ {print $1; exit}')"
if [[ -z "$TEMPLATE" ]]; then
  echo ">> Downloading Debian 12 template..."
  pveam update
  LATEST="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)"
  pveam download "$TEMPLATE_STORE" "$LATEST"
  TEMPLATE="$TEMPLATE_STORE:vztmpl/$LATEST"
fi
echo ">> Template: $TEMPLATE"

# ---------------------------------------------------------------- create CT --
NETCFG="name=eth0,bridge=$BRIDGE"
if [[ "$IP" == "dhcp" ]]; then
  NETCFG+=",ip=dhcp"
else
  NETCFG+=",ip=$IP"
  [[ -n "$GW" ]] && NETCFG+=",gw=$GW"
fi

echo ">> Creating CT $CTID ($HOSTNAME)..."
pct create "$CTID" "$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" --memory "$MEM_MB" --swap 512 \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "$NETCFG" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1 \
  --start 1

echo ">> Waiting for network..."
for i in $(seq 1 30); do
  pct exec "$CTID" -- ping -c1 -W2 deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done

# ------------------------------------------------------------ install Grafana
echo ">> Installing Grafana OSS..."
pct exec "$CTID" -- bash -c '
  set -e
  apt-get update -qq
  apt-get install -y -qq apt-transport-https software-properties-common wget gpg >/dev/null
  mkdir -p /etc/apt/keyrings
  wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
    > /etc/apt/sources.list.d/grafana.list
  apt-get update -qq
  apt-get install -y -qq grafana >/dev/null
  grafana-cli plugins install yesoreyeram-infinity-datasource >/dev/null
'

# ------------------------------------------------------------- provisioning --
echo ">> Provisioning datasources + dashboard..."
TMP="$(mktemp -d)"
sed -e "s|__TENANT_ID__|$TENANT_ID|g" \
    -e "s|__CLIENT_ID__|$CLIENT_ID|g" \
    -e "s|__CLIENT_SECRET__|$CLIENT_SECRET|g" \
    "$SCRIPT_DIR/provisioning/datasources/avd-dex.yaml" > "$TMP/avd-dex.yaml"

pct exec "$CTID" -- mkdir -p /var/lib/grafana/dashboards
pct push "$CTID" "$TMP/avd-dex.yaml" /etc/grafana/provisioning/datasources/avd-dex.yaml
pct push "$CTID" "$SCRIPT_DIR/provisioning/dashboards/provider.yaml" /etc/grafana/provisioning/dashboards/provider.yaml
pct push "$CTID" "$SCRIPT_DIR/dashboards/avd-dex.json" /var/lib/grafana/dashboards/avd-dex.json
rm -rf "$TMP"

pct exec "$CTID" -- bash -c "
  set -e
  chown -R grafana:grafana /var/lib/grafana/dashboards /etc/grafana/provisioning
  grafana-cli admin reset-admin-password '$ADMIN_PASS' >/dev/null 2>&1 || true
  systemctl enable --now grafana-server
  systemctl restart grafana-server
"

CTIP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
echo ""
echo "=============================================================="
echo " Grafana is up:  http://$CTIP:3000   (admin / $ADMIN_PASS)"
echo "=============================================================="
echo " Next steps (in the dashboard's variables, top of the page):"
echo "  1. Set 'workspace' to the FULL ARM resource ID of your Log"
echo "     Analytics workspace, e.g."
echo "     /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<name>"
echo "  2. Set 'dex_url' to the AVD DEX Dashboard base URL reachable"
echo "     FROM THIS CT, e.g. http://<dashboard-host>:8010"
echo "     (bind the dashboard to 0.0.0.0 and open the port on the LAN)."
echo "  3. Save the dashboard, then build alert rules on any panel."
echo "=============================================================="
