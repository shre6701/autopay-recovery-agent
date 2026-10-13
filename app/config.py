"""Settings, read from environment variables. See .env.example."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # backend
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    tool_secret: str = field(default_factory=lambda: os.getenv("TOOL_SECRET", ""))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(ROOT / "data" / "recovery.db")))
    customers_file: str = field(default_factory=lambda: os.getenv("CUSTOMERS_FILE", str(ROOT / "data" / "customers.json")))

    # every live call goes to this number
    demo_phone_number: str = field(default_factory=lambda: os.getenv("DEMO_PHONE_NUMBER", ""))

    # calling policy
    timezone: str = field(default_factory=lambda: os.getenv("CALL_TIMEZONE", "Asia/Kolkata"))
    call_window_start: str = field(default_factory=lambda: os.getenv("CALL_WINDOW_START", "09:00"))
    call_window_end: str = field(default_factory=lambda: os.getenv("CALL_WINDOW_END", "19:00"))
    max_call_attempts_7d: int = field(default_factory=lambda: int(os.getenv("MAX_CALL_ATTEMPTS_7D", "3")))
    min_hours_between_calls: float = field(default_factory=lambda: float(os.getenv("MIN_HOURS_BETWEEN_CALLS", "4")))
    max_silent_retries: int =field(default_factory=lambda: int(os.getenv("MAX_SILENT_RETRIES", "2")))
    grace_period_days: int = field(default_factory=lambda: int(os.getenv("GRACE_PERIOD_DAYS", "7")))

    # cost per minute, for the dashboard
    agent_cost_per_min_inr: float = field(default_factory=lambda: float(os.getenv("AGENT_COST_PER_MIN_INR", "3.50")))
    telephony_cost_per_min_inr: float = field(default_factory=lambda: float(os.getenv("TELEPHONY_COST_PER_MIN_INR", "0.0")))

    # razorpay, test keys only
    razorpay_key_id: str = field(default_factory=lambda: os.getenv("RAZORPAY_KEY_ID", ""))
    razorpay_key_secret: str = field(default_factory=lambda: os.getenv("RAZORPAY_KEY_SECRET", ""))
    razorpay_webhook_secret: str = field(default_factory=lambda: os.getenv("RAZORPAY_WEBHOOK_SECRET", ""))
    razorpay_notify_sms: bool = field(default_factory=lambda: _bool("RAZORPAY_NOTIFY_SMS", False))

    # sarvam voice agents
    sarvam_api_key: str = field(default_factory=lambda: os.getenv("SARVAM_API_KEY", ""))
    sarvam_org_id: str = field(default_factory=lambda: os.getenv("SARVAM_ORG_ID", ""))
    sarvam_workspace_id: str = field(default_factory=lambda: os.getenv("SARVAM_WORKSPACE_ID", ""))
    sarvam_app_id: str = field(default_factory=lambda: os.getenv("SARVAM_APP_ID", ""))
    sarvam_app_version: int = field(default_factory=lambda: int(os.getenv("SARVAM_APP_VERSION", "1")))
    sarvam_connection_id: str = field(default_factory=lambda: os.getenv("SARVAM_CONNECTION_ID", ""))
    sarvam_agent_phone_number: str = field(default_factory=lambda: os.getenv("SARVAM_AGENT_PHONE_NUMBER", ""))
    sarvam_base_url: str = field(default_factory=lambda: os.getenv("SARVAM_BASE_URL", "https://apps.sarvam.ai/api/outbounds/v1"))

    @property
    def razorpay_mode(self) -> str:
        """'test' with real test keys, 'mock' when no keys are set. Live keys are refused."""
        if not self.razorpay_key_id or not self.razorpay_key_secret:
            return "mock"
        if self.razorpay_key_id.startswith("rzp_live_"):
            return "refused_live"
        return "test"

    @property
    def sarvam_ready(self) -> bool:
        return all([
            self.sarvam_api_key, self.sarvam_org_id, self.sarvam_workspace_id, self.sarvam_app_id,
            self.sarvam_connection_id, self.sarvam_agent_phone_number,
        ])


def get_settings() -> Settings:
    return Settings()
