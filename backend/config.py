from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, loaded from environment / .env (prefix AVD_)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AVD_",
        extra="ignore",
    )

    # Log Analytics workspace GUID that AVD Insights writes to.
    workspace_id: str = ""

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    default_range: str = "24h"

    # ---- DEX agent ingest ----
    # Shared secret the AVD DEX agent must send in X-Agent-Key. Ingest is
    # disabled (503) until this is set.
    agent_key: str = ""
    # SQLite file for agent telemetry (relative paths resolve to project root).
    agent_db: str = "dex-agent.db"
    agent_retention_days: int = 14

    # Alert thresholds (override via AVD_THR_* env vars). For success_rate
    # higher is better, so "warn" is the floor for OK and "bad" is the floor
    # for WARN. For the rest, higher is worse: >= warn is WARN, >= bad is BAD.
    thr_success_warn: float = 98.0
    thr_success_bad: float = 90.0
    thr_errors_warn: int = 1
    thr_errors_bad: int = 50
    thr_rtt_warn: float = 80.0
    thr_rtt_bad: float = 150.0
    thr_profile_warn: float = 15.0
    thr_profile_bad: float = 30.0

    @property
    def thresholds(self) -> dict:
        return {
            "success_rate": {"warn": self.thr_success_warn, "bad": self.thr_success_bad, "higher_is_better": True},
            "errors": {"warn": self.thr_errors_warn, "bad": self.thr_errors_bad, "higher_is_better": False},
            "avg_rtt_ms": {"warn": self.thr_rtt_warn, "bad": self.thr_rtt_bad, "higher_is_better": False},
            "avg_profile_sec": {"warn": self.thr_profile_warn, "bad": self.thr_profile_bad, "higher_is_better": False},
        }


settings = Settings()
