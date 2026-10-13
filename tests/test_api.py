from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest

from app.campaign import dial
from app.db import Store
from app.razorpay_client import build_payload
from app.sarvam_client import SarvamError

from .conftest import ist_today_at, make_settings


def test_tools_require_secret(client):
    assert client.post("/tools/create_payment_link", json={"customer_id": "CUST-006"}).status_code == 401
    assert client.post("/tools/create_payment_link", json={"customer_id": "CUST-006"},
                       headers={"X-Tool-Secret": "test-secret"}).status_code == 200


def test_payment_link_is_idempotent(client, auth):
    first = client.post("/tools/create_payment_link", json={"customer_id": "CUST-006"}, headers=auth).json()
    second = client.post("/tools/create_payment_link", json={"customer_id": "CUST-006"}, headers=auth).json()
    assert first["ok"] and first["mode"] == "mock" and first["amount_inr"] == 18000
    assert second["reused"] is True and second["link_id"] == first["link_id"]


def test_unknown_customer_is_handled_gracefully(client, auth):
    r = client.post("/tools/create_payment_link", json={"customer_id": "NOPE"}, headers=auth).json()
    assert r["ok"] is False and "team member" in r["message"]


def test_no_link_for_dnd_customer(client, auth):
    r = client.post("/tools/create_payment_link", json={"customer_id": "CUST-008"}, headers=auth).json()
    assert r["ok"] is False


def test_schedule_retry_only_inside_grace(client, auth):
    today = ist_today_at(11).date()
    bad = client.post("/tools/schedule_retry", json={"customer_id": "CUST-001", "retry_date": (today + timedelta(days=30)).isoformat()}, headers=auth).json()
    assert bad["ok"] is False and "latest" in bad
    junk = client.post("/tools/schedule_retry", json={"customer_id": "CUST-001", "retry_date": "next friday"}, headers=auth).json()
    assert junk["ok"] is False
    good = client.post("/tools/schedule_retry", json={"customer_id": "CUST-001", "retry_date": (today + timedelta(days=2)).isoformat()}, headers=auth).json()
    assert good["ok"] is True


def test_do_not_call_is_respected_immediately(client, auth):
    r = client.post("/tools/record_outcome", json={"customer_id": "CUST-001", "outcome": "do_not_call"}, headers=auth).json()
    assert r["ok"]
    state = client.get("/api/state", params={"now": ist_today_at(11).isoformat()}).json()
    row = next(c for c in state["customers"] if c["customer_id"] == "CUST-001")
    assert row["dnd"] is True and row["decision"]["action"] == "skip"


def test_cancellation_is_recorded_not_fought(client, auth):
    client.post("/tools/record_outcome", json={"customer_id": "CUST-003", "outcome": "wants_to_cancel"}, headers=auth)
    state = client.get("/api/state").json()
    assert next(c for c in state["customers"] if c["customer_id"] == "CUST-003")["status"] == "cancelled"
    assert state["escalations"][0]["customer_id"] == "CUST-003"


def test_agent_cannot_mark_itself_paid(client, auth):
    r = client.post("/tools/record_outcome", json={"customer_id": "CUST-001", "outcome": "paid_via_link"}, headers=auth).json()
    assert r["ok"] is False


def test_sarvam_webhook(client):
    payload = {
        "attempt_id": "a-1", "status": "connected", "duration": 60.0, "interaction_id": "i-1",
        "channel_info": {"channel_type": "v2v", "channel_provider": "exotel", "agent_phone_number": "+910000000000"},
        "failure_reason": None,
        "final_agent_variables": {"customer_id": "CUST-001", "call_outcome": "promised_to_pay", "promise_date": "2026-10-20"},
        "webhook_config": {"url": "x", "metadata": {"customer_id": "CUST-001"}},
        "interaction_transcript": [{"role": "agent", "en_text": "Hello"}],
    }
    assert client.post("/webhooks/sarvam/outbound", json=payload).status_code == 401
    assert client.post("/webhooks/sarvam/outbound?token=test-secret", json=payload).status_code == 200
    state = client.get("/api/state").json()
    assert state["metrics"]["calls_connected"] == 1
    row = next(c for c in state["customers"] if c["customer_id"] == "CUST-001")
    assert row["last_outcome"]["outcome"] == "promised_to_pay"


def test_razorpay_webhook_marks_recovered(client, auth):
    link = client.post("/tools/create_payment_link", json={"customer_id": "CUST-006"}, headers=auth).json()
    body = json.dumps({"event": "payment_link.paid",
                       "payload": {"payment_link": {"entity": {"id": link["link_id"]}}}}).encode()
    assert client.post("/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": "bad"}).status_code == 400
    sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sig}).status_code == 200
    state = client.get("/api/state").json()
    assert state["metrics"]["recovered_inr"] == 18000
    assert next(c for c in state["customers"] if c["customer_id"] == "CUST-006")["status"] == "recovered"


def test_live_razorpay_keys_are_refused(tmp_path, auth):
    from fastapi.testclient import TestClient
    from app.main import create_app
    s = make_settings(tmp_path, razorpay_key_id="rzp_live_abc", razorpay_key_secret="x")
    r = TestClient(create_app(s)).post("/tools/create_payment_link", json={"customer_id": "CUST-006"}, headers=auth).json()
    assert r["ok"] is False


def test_fictional_numbers_never_leave_the_system(store, settings):
    payload = build_payload(store.customer("CUST-006"), "Kaveri Fitness", settings)
    assert "contact" not in payload["customer"] and payload["amount"] == 1_800_000


def test_dial_dry_run_targets_demo_number_and_matches_sarvam_schema(tmp_path):
    s = make_settings(tmp_path, demo_phone_number="+919999999999")
    st = Store(s)
    st.load_seed(reset=True)
    results = dial(st, s, ist_today_at(11))
    assert [r["customer_id"] for r in results] == ["CUST-006", "CUST-010", "CUST-001", "CUST-003"]
    req = results[0]["request"]
    assert req["user_config"]["user_phone_number"] == "+919999999999"
    assert set(req["app_config"]) >= {"app_id", "app_version", "connection_config", "agent_variables"}
    assert req["webhook_config"]["metadata"] == {"customer_id": "CUST-006"}
    assert req["app_config"]["agent_variables"]["amount_due"] == "18000 rupees"


def test_live_dial_refuses_without_demo_number(store, settings):
    with pytest.raises(SarvamError):
        dial(store, settings, ist_today_at(11), live=True)
