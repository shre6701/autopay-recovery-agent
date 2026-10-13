"""Razorpay payment links.

rzp_test_ keys create real test-mode links. With no keys, links are mocked
locally. rzp_live_ keys are refused.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .models import Customer

API = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    pass


def reference_id_for(customer: Customer) -> str:
    """Same failed payment, same reference, so a repeated tool call can't create a second link."""
    return f"{customer.customer_id}-{customer.failed_at}"[:40]


def build_payload(customer: Customer, merchant_name: str, s: Settings) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "amount": customer.amount_due_inr * 100,  # paise
        "currency": "INR",
        "accept_partial": False,
        "description": f"{merchant_name}: {customer.plan_name} (failed autopay on {customer.failed_at})",
        "customer": {"name": customer.name, "email": customer.email},
        "notify": {"sms": s.razorpay_notify_sms, "email": False},
        "reminder_enable": False,
        "reference_id": reference_id_for(customer),
        "expire_by": int(time.time()) + 3 * 24 * 3600,
        "notes": {
            "customer_id": customer.customer_id,
            "failure_reason": customer.failure_reason,
            "source": "autopay-recovery-voice-agent",
        },
    }
    # Only the demo number is ever sent to Razorpay, never the fictional ones.
    if s.demo_phone_number:
        payload["customer"]["contact"] = s.demo_phone_number
    return payload


def create_payment_link(customer: Customer, merchant_name: str, s: Settings, client: httpx.Client | None = None) -> tuple[dict[str, Any], str]:
    mode = s.razorpay_mode
    if mode == "refused_live":
        raise RazorpayError("Live Razorpay keys detected. This project only runs with test keys (rzp_test_...).")
    payload = build_payload(customer, merchant_name, s)

    if mode == "mock":
        link_id = f"plink_mock_{uuid.uuid4().hex[:12]}"
        return ({
            "id": link_id,
            "short_url": f"{s.public_base_url}/mock/pay/{link_id}",
            "amount": payload["amount"],
            "reference_id": payload["reference_id"],
            "status": "created",
        }, mode)

    own_client = client is None
    client = client or httpx.Client(timeout=8.0)
    try:
        r = client.post(f"{API}/payment_links", json=payload, auth=(s.razorpay_key_id, s.razorpay_key_secret))
    finally:
        if own_client:
            client.close()
    if r.status_code >= 400:
        try:
            desc = r.json().get("error", {}).get("description", r.text)
        except ValueError:
            desc = r.text
        raise RazorpayError(f"Razorpay error {r.status_code}: {desc}")
    return r.json(), mode


def fetch_payment_link(link_id: str, s: Settings) -> dict[str, Any]:
    with httpx.Client(timeout=8.0) as client:
        r = client.get(f"{API}/payment_links/{link_id}", auth=(s.razorpay_key_id, s.razorpay_key_secret))
    if r.status_code >= 400:
        raise RazorpayError(f"Razorpay error {r.status_code}: {r.text}")
    return r.json()


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 of the raw body with the webhook secret, as Razorpay signs it."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
