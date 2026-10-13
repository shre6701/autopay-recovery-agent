from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import Store  # noqa: E402

SECRET = "test-secret"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        db_path=str(tmp_path / "test.db"),
        customers_file=str(ROOT / "data" / "customers.json"),
        tool_secret=SECRET,
        public_base_url="http://testserver",
        demo_phone_number="",
        razorpay_key_id="", razorpay_key_secret="", razorpay_webhook_secret="whsec_test",
        sarvam_api_key="", sarvam_org_id="", sarvam_workspace_id="", sarvam_app_id="",
        sarvam_connection_id="", sarvam_agent_phone_number="",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def store(settings):
    st = Store(settings)
    st.load_seed(reset=True)
    return st


@pytest.fixture
def client(settings):
    from app.main import create_app
    return TestClient(create_app(settings))


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {SECRET}"}


def ist_today_at(hour: int, minute: int = 0) -> datetime:
    tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
