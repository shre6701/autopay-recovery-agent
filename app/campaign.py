"""Builds the call plan from stored data and places calls."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Store
from .decision import Decision, agent_variables, plan
from .models import Action
from .sarvam_client import build_outbound_request, place_call


def parse_now(value: str | None, s: Settings) -> datetime:
    """None -> now. 'HH:MM' -> today at that time. Otherwise an ISO datetime. All in the calling timezone."""
    tz = ZoneInfo(s.timezone)
    if not value:
        return datetime.now(tz)
    if len(value) <= 5 and ":" in value:
        h, m = (int(x) for x in value.split(":"))
        return datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def current_plan(store: Store, s: Settings, now: datetime) -> list[Decision]:
    statuses = store.statuses()
    last: dict[str, str] = {}
    for o in store.outcomes():  # newest first
        last.setdefault(o["customer_id"], o["outcome"])

    # Attempts made by this system count towards the 7-day cap on top of the seed history.
    week_ago = now - timedelta(days=7)
    recent: dict[str, int] = {}
    last_call: dict[str, datetime] = {}
    for a in store.attempts():
        at = datetime.fromisoformat(a["created_at"])
        if at >= week_ago:
            recent[a["customer_id"]] = recent.get(a["customer_id"], 0) + 1
        last_call[a["customer_id"]] = max(at, last_call.get(a["customer_id"], at))

    open_customers = [
        replace(c, call_attempts_7d=c.call_attempts_7d + recent.get(c.customer_id, 0))
        for c in store.customers() if statuses.get(c.customer_id, "open") == "open"
    ]
    return plan(open_customers, now, s, last, last_call)


def dial(store: Store, s: Settings, now: datetime, only: list[str] | None = None,
         live: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
    """Calls everyone marked "call", highest expected value first. Dry run unless live=True."""
    merchant = store.merchant()["name"]
    results: list[dict[str, Any]] = []
    to_call = [d for d in current_plan(store, s, now) if d.action == Action.CALL.value]
    if only:
        to_call = [d for d in to_call if d.customer_id in only]
    if limit:
        to_call = to_call[:limit]

    for d in to_call:
        customer = store.customer(d.customer_id)
        assert customer is not None
        variables = agent_variables(customer, d, merchant, now.date(), s)
        if not live:
            body = build_outbound_request(variables, s.demo_phone_number or "<DEMO_PHONE_NUMBER>", customer.customer_id, s)
            results.append({"customer_id": d.customer_id, "mode": "dry_run", "request": body})
            continue
        attempt_id = place_call(variables, customer.customer_id, s)
        store.add_attempt(attempt_id, customer.customer_id)  # counts towards the 7-day cap
        results.append({"customer_id": d.customer_id, "mode": "live", "attempt_id": attempt_id})
    return results


def new_simulated_attempt_id() -> str:
    return f"sim-{uuid.uuid4()}"
