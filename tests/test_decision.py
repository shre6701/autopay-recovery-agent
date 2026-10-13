from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from app.campaign import current_plan
from app.decision import decide, next_allowed_time, suggested_retry_date

from .conftest import ist_today_at


def actions(store, settings, now):
    return {d.customer_id: d.action for d in current_plan(store, settings, now)}


def test_seed_plan_at_11am(store, settings):
    got = actions(store, settings, ist_today_at(11))
    assert got == {
        "CUST-001": "call",           # low balance
        "CUST-002": "schedule",       # prefers 17:00-19:00
        "CUST-003": "call",           # mandate revoked: find out why
        "CUST-004": "silent_retry",   # bank-side decline, retry first
        "CUST-005": "skip",           # no consent
        "CUST-006": "call",           # above UPI AutoPay limit
        "CUST-007": "human_handoff",  # disputed
        "CUST-008": "skip",           # DND
        "CUST-009": "skip",           # already called 3 times this week
        "CUST-010": "call",           # silent retries exhausted
    }


def test_calls_are_ordered_by_expected_value(store, settings):
    calls = [d for d in current_plan(store, settings, ist_today_at(11)) if d.action == "call"]
    scores = [d.priority_score for d in calls]
    assert scores == sorted(scores, reverse=True)
    assert calls[0].customer_id == "CUST-006"  # 18,000 x 0.75


def test_no_calls_outside_calling_hours(store, settings):
    late = ist_today_at(20, 30)
    plan = current_plan(store, settings, late)
    assert not [d for d in plan if d.action == "call"]
    nxt = next(d for d in plan if d.customer_id == "CUST-001").next_contact_at
    assert nxt.startswith((late + timedelta(days=1)).date().isoformat() + "T09:00")


def test_preferred_window_narrows_policy_window(store, settings):
    priya = store.customer("CUST-002")
    assert next_allowed_time(priya, ist_today_at(17, 30), settings) is None
    assert next_allowed_time(priya, ist_today_at(16, 59), settings).hour == 17


def test_safety_rules_beat_everything(store, settings):
    c = replace(store.customer("CUST-006"), consent_to_call=False)
    assert decide(c, ist_today_at(11), settings).reason_code == "no_consent"
    c = replace(store.customer("CUST-006"), dnd=True)
    assert decide(c, ist_today_at(11), settings).reason_code == "dnd"


def test_grace_period_over_goes_to_human(store, settings):
    c = store.customer("CUST-001")
    c = replace(c, failed_at=(ist_today_at(11).date() - timedelta(days=settings.grace_period_days + 1)).isoformat())
    assert decide(c, ist_today_at(11), settings).action == "human_handoff"


def test_bank_decline_calls_only_after_silent_retries(store, settings):
    c = store.customer("CUST-004")
    assert decide(c, ist_today_at(11), settings).action == "silent_retry"
    c = replace(c, silent_retries_done=settings.max_silent_retries)
    assert decide(c, ist_today_at(11), settings).action == "call"


def test_no_second_call_after_agreed_next_step(store, settings):
    c = store.customer("CUST-001")
    assert decide(c, ist_today_at(11), settings, last_outcome="retry_scheduled").action == "wait"
    assert decide(c, ist_today_at(11), settings, last_outcome="no_resolution").action == "call"


def test_payday_suggestion_stays_inside_grace(store, settings):
    c = store.customer("CUST-001")  # payday is 2 days from today in the seed
    today = ist_today_at(11).date()
    assert suggested_retry_date(c, today, settings) == today + timedelta(days=2)


def test_no_redial_within_min_gap(store, settings):
    c = store.customer("CUST-010")
    now = ist_today_at(11)
    d = decide(c, now, settings, last_call_at=now - timedelta(hours=1))
    assert d.action == "schedule" and d.reason_code == "too_soon"
    assert decide(c, now, settings, last_call_at=now - timedelta(hours=5)).action == "call"


def test_our_own_calls_count_towards_weekly_cap(store, settings):
    now = ist_today_at(11)
    for i in range(3):
        store.add_attempt(f"a-{i}", "CUST-006")  # 3 attempts today (no previous history in seed)
    d = next(x for x in current_plan(store, settings, now + timedelta(hours=12)) if x.customer_id == "CUST-006")
    assert d.reason_code == "max_attempts"
