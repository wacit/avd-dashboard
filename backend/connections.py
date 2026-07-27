"""Tenant linking for the dashboard / OS.

Lets an admin connect the dashboard to their tenant from the UI instead of
editing .env:

  * Device-code sign-in (recommended): the backend runs
    DeviceCodeCredential.authenticate() on a background thread, the UI
    shows the user code + verification link, and the resulting
    AuthenticationRecord is persisted so later runs are silent (tokens are
    cached via msal-extensions, DPAPI-protected on Windows).
  * Service principal: tenant/client/secret stored in connection.json.
  * Fallback: DefaultAzureCredential + AVD_WORKSPACE_ID (the original
    behavior) when nothing is linked.

connection.json lives in the project root and is gitignored. The
/api/connect endpoints are unauthenticated by design - the app binds to
127.0.0.1 for a local admin; do not expose the port beyond localhost.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from azure.identity import (
    AuthenticationRecord,
    ClientSecretCredential,
    DefaultAzureCredential,
    DeviceCodeCredential,
    TokenCachePersistenceOptions,
)

from .config import settings

ROOT = Path(__file__).resolve().parent.parent
CONN_PATH = ROOT / "connection.json"
CACHE_OPTS = TokenCachePersistenceOptions(
    name="avd-dex-dashboard", allow_unencrypted_storage=True
)
LOGS_SCOPE = "https://api.loganalytics.io/.default"
ARM = "https://management.azure.com"

_lock = threading.Lock()
_flows: dict[str, dict] = {}
_credential = None
# Bumped whenever the credential/config changes so azure_client can drop
# its cached LogsQueryClient.
generation = 0


# ---------------------------------------------------------------- config --

def _load() -> dict:
    try:
        return json.loads(CONN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cfg: dict) -> None:
    CONN_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def reset_credential() -> None:
    global _credential, generation
    with _lock:
        _credential = None
        generation += 1


def get_credential():
    global _credential
    with _lock:
        if _credential is not None:
            return _credential
        cfg = _load()
        mode = cfg.get("auth_mode")
        if mode == "device" and cfg.get("auth_record"):
            record = AuthenticationRecord.deserialize(cfg["auth_record"])
            _credential = DeviceCodeCredential(
                tenant_id=record.tenant_id,
                cache_persistence_options=CACHE_OPTS,
                authentication_record=record,
            )
        elif mode == "sp" and cfg.get("client_id"):
            _credential = ClientSecretCredential(
                cfg.get("tenant_id"), cfg.get("client_id"), cfg.get("client_secret")
            )
        else:
            _credential = DefaultAzureCredential()
        return _credential


def workspace_id() -> str:
    """Linked workspace wins; falls back to AVD_WORKSPACE_ID from .env."""
    return _load().get("workspace_id") or settings.workspace_id


# One cached token probe instead of letting every KQL query walk the whole
# DefaultAzureCredential chain (16+ chain walks per dashboard refresh when
# nothing is signed in, each dumping the full failure list to the log).
_health = {"gen": -1, "ts": 0.0, "error": None}


def credential_health() -> str | None:
    """Return None when a token can be acquired, else one actionable message.

    Probes at most once per minute per credential generation; queries
    short-circuit on the cached result.
    """
    now = time.monotonic()
    if _health["gen"] == generation and now - _health["ts"] < 60:
        return _health["error"]
    error = None
    try:
        get_credential().get_token(LOGS_SCOPE)
    except Exception as exc:  # noqa: BLE001 - any failure means "not signed in"
        name = type(exc).__name__
        mode = _load().get("auth_mode")
        if mode == "device":
            error = (f"Cached sign-in expired ({name}) - open the Connections "
                     "app and link the tenant again.")
        elif mode == "sp":
            error = (f"Service principal sign-in failed ({name}) - re-enter "
                     "it in the Connections app.")
        else:
            error = ("Not signed in to Azure - link your tenant in the "
                     "Connections app (device code), or install Azure CLI and "
                     "run 'az login', or set AZURE_TENANT_ID / AZURE_CLIENT_ID "
                     "/ AZURE_CLIENT_SECRET.")
    _health.update(gen=generation, ts=now, error=error)
    return error


def status() -> dict:
    cfg = _load()
    out = {
        "mode": cfg.get("auth_mode") or ("env" if settings.workspace_id else "none"),
        "tenant_id": cfg.get("tenant_id"),
        "account": cfg.get("account"),
        "workspace_id": cfg.get("workspace_id") or settings.workspace_id or None,
        "workspace_name": cfg.get("workspace_name"),
        "workspace_configured": bool(workspace_id()),
    }
    # Only probe when queries would actually run (a workspace is set);
    # otherwise the UI's "not linked" state is message enough.
    out["credential_error"] = credential_health() if out["workspace_configured"] else None
    return out


# ----------------------------------------------------------- device flow --

def start_device_flow(tenant_id: str | None) -> str:
    flow_id = uuid.uuid4().hex[:12]
    info = {
        "status": "starting", "user_code": None, "verification_uri": None,
        "error": None,
    }
    _flows[flow_id] = info
    if len(_flows) > 20:  # drop oldest finished flows
        for k in list(_flows)[:-20]:
            _flows.pop(k, None)

    def prompt(verification_uri, user_code, expires_on):
        info.update(status="pending", user_code=user_code,
                    verification_uri=verification_uri)

    def run():
        try:
            cred = DeviceCodeCredential(
                tenant_id=tenant_id or "organizations",
                prompt_callback=prompt,
                cache_persistence_options=CACHE_OPTS,
            )
            record = cred.authenticate(scopes=[LOGS_SCOPE])
            cfg = _load()
            cfg.update({
                "auth_mode": "device",
                "tenant_id": record.tenant_id,
                "account": record.username,
                "auth_record": record.serialize(),
            })
            cfg.pop("client_id", None)
            cfg.pop("client_secret", None)
            _save(cfg)
            reset_credential()
            info["status"] = "authenticated"
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            info.update(status="error", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=run, daemon=True, name="avd-device-flow").start()
    return flow_id


def poll_flow(flow_id: str) -> dict:
    return _flows.get(flow_id) or {"status": "unknown",
                                   "error": "Unknown or expired flow id."}


# ------------------------------------------------------ service principal --

def connect_sp(tenant_id: str, client_id: str, client_secret: str) -> None:
    cred = ClientSecretCredential(tenant_id, client_id, client_secret)
    cred.get_token(LOGS_SCOPE)  # validate before persisting; raises on failure
    cfg = _load()
    cfg.update({
        "auth_mode": "sp",
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "account": f"app:{client_id[:8]}…",
    })
    cfg.pop("auth_record", None)
    _save(cfg)
    reset_credential()


# ------------------------------------------------- workspace enumeration --

def _arm_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        ARM + path, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def list_workspaces() -> list[dict]:
    """All Log Analytics workspaces visible to the linked identity."""
    token = get_credential().get_token(f"{ARM}/.default").token
    subs = _arm_get("/subscriptions?api-version=2022-12-01", token).get("value", [])
    out = []
    for sub in subs:
        sid = sub.get("subscriptionId")
        try:
            wss = _arm_get(
                f"/subscriptions/{sid}/providers/Microsoft.OperationalInsights"
                "/workspaces?api-version=2023-09-01",
                token,
            ).get("value", [])
        except Exception:  # noqa: BLE001 - a locked-down sub shouldn't kill the list
            continue
        for ws in wss:
            props = ws.get("properties") or {}
            rid = ws.get("id", "")
            rg = ""
            parts = rid.split("/")
            if "resourceGroups" in parts:
                rg = parts[parts.index("resourceGroups") + 1]
            out.append({
                "name": ws.get("name"),
                "customer_id": props.get("customerId"),
                "subscription": sub.get("displayName") or sid,
                "resource_group": rg,
                "location": ws.get("location"),
            })
    out.sort(key=lambda w: (w["subscription"] or "", w["name"] or ""))
    return out


def set_workspace(customer_id: str, name: str | None) -> None:
    cfg = _load()
    cfg["workspace_id"] = customer_id
    cfg["workspace_name"] = name
    _save(cfg)
    reset_credential()


def disconnect() -> None:
    try:
        CONN_PATH.unlink()
    except OSError:
        pass
    reset_credential()
