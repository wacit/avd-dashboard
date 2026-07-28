"""DEX (digital employee experience) scoring engine.

Modeled on commercial DEX tools (eG Innovations, Nexthink, ControlUp):
every entity (a user, a session host, or the whole environment) gets a
0-100 experience score computed from weighted factors.

Each factor maps a raw metric onto 0-100 linearly between a "good" band
(score 100 at or better than this) and a "bad" band (score 0 at or worse
than this). Factors whose metric is unknown simply drop out and the
remaining weights are renormalized, so scores degrade gracefully when a
data source (a Log Analytics table, or the DEX agent) is missing.

Sources:
  log-analytics - derived from the AVD Insights tables
  agent         - in-session telemetry only the AVD DEX agent can see
"""

FACTORS = [
    {"key": "success_rate",    "label": "Connection reliability",  "unit": "%",    "good": 99.0, "bad": 85.0,  "weight": 20, "higher_is_better": True,  "source": "log-analytics"},
    {"key": "logon_sec",       "label": "Logon speed",             "unit": "s",    "good": 20.0, "bad": 75.0,  "weight": 15, "higher_is_better": False, "source": "log-analytics"},
    {"key": "rtt_ms",          "label": "Session latency (RTT)",   "unit": "ms",   "good": 60.0, "bad": 200.0, "weight": 20, "higher_is_better": False, "source": "log-analytics"},
    {"key": "profile_sec",     "label": "Profile load (FSLogix)",  "unit": "s",    "good": 10.0, "bad": 45.0,  "weight": 10, "higher_is_better": False, "source": "log-analytics"},
    {"key": "errors_per_conn", "label": "Error rate",              "unit": "/conn","good": 0.1,  "bad": 2.0,   "weight": 10, "higher_is_better": False, "source": "log-analytics"},
    {"key": "short_session_pct", "label": "Connection stability",  "unit": "%",    "good": 5.0,  "bad": 40.0,  "weight": 10, "higher_is_better": False, "source": "log-analytics"},
    {"key": "packet_loss_pct", "label": "Packet loss",             "unit": "%",    "good": 0.5,  "bad": 5.0,   "weight": 5,  "higher_is_better": False, "source": "agent"},
    {"key": "input_delay_ms",  "label": "Input responsiveness",    "unit": "ms",   "good": 80.0, "bad": 600.0, "weight": 10, "higher_is_better": False, "source": "agent"},
    {"key": "fps",             "label": "Frame rate",              "unit": "fps",  "good": 24.0, "bad": 8.0,   "weight": 5,  "higher_is_better": True,  "source": "agent"},
    {"key": "host_cpu_pct",    "label": "Host CPU pressure",       "unit": "%",    "good": 60.0, "bad": 95.0,  "weight": 5,  "higher_is_better": False, "source": "agent"},
    {"key": "app_crashes",     "label": "App crashes / hangs",     "unit": "",     "good": 0.0,  "bad": 5.0,   "weight": 5,  "higher_is_better": False, "source": "agent"},
    {"key": "smb_latency_ms",  "label": "Profile share latency",   "unit": "ms",   "good": 20.0, "bad": 200.0, "weight": 5,  "higher_is_better": False, "source": "agent"},
    {"key": "cpu_queue",       "label": "Host saturation (CPU queue)", "unit": "", "good": 2.0,  "bad": 12.0,  "weight": 5,  "higher_is_better": False, "source": "agent"},
]


def band_score(value: float, good: float, bad: float, higher_is_better: bool = False) -> float:
    """Map a raw metric onto 0-100 linearly between the good/bad bands."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if higher_is_better:
        if v >= good:
            return 100.0
        if v <= bad:
            return 0.0
        return (v - bad) / (good - bad) * 100.0
    if v <= good:
        return 100.0
    if v >= bad:
        return 0.0
    return (bad - v) / (bad - good) * 100.0


def grade(score: float | None) -> str:
    if score is None:
        return "No data"
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    return "Poor"


def score_entity(metrics: dict) -> dict:
    """Score one entity from whatever metrics are available.

    metrics maps factor key -> raw value (None / missing keys are skipped).
    Returns {"score", "grade", "factors": [...]} where factors carries the
    per-factor raw value, sub-score and effective weight for the UI.
    """
    factors = []
    weighted = 0.0
    total_weight = 0.0
    for f in FACTORS:
        raw = metrics.get(f["key"])
        if raw is None:
            continue
        s = band_score(raw, f["good"], f["bad"], f["higher_is_better"])
        if s is None:
            continue
        factors.append({
            "key": f["key"],
            "label": f["label"],
            "unit": f["unit"],
            "value": round(float(raw), 1),
            "score": round(s, 1),
            "weight": f["weight"],
            "source": f["source"],
        })
        weighted += s * f["weight"]
        total_weight += f["weight"]

    score = round(weighted / total_weight, 1) if total_weight else None
    return {"score": score, "grade": grade(score), "factors": factors}
