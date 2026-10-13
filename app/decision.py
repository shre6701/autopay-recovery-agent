"""Decides, per customer, whether to call, wait, retry silently, hand over, or skip.

Checks run in order: consent and contact limits first, then cases that
shouldn't get a bot call, then the cheapest fix, then calling hours.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .models import Action, Customer, FailureReason

# Guessed chance that a call recovers the payment. Only used to order calls.
# With real data these would come from the merchant's history.
RECOVERY_LIKELIHOOD: dict[str, float] = {
    FailureReason.AMOUNT_ABOVE_MANDATE_LIMIT: 0.75,
    FailureReason.CARD_EXPIRED: 0.70,
    FailureReason.AUTHENTICATION_FAILED: 0.65,
    FailureReason.BANK_TECHNICAL_DECLINE: 0.60,
    FailureReason.INSUFFICIENT_FUNDS: 0.55,
    FailureReason.MANDATE_REVOKED: 0.35,
    FailureReason.DISPUTED: 0.0,
}

PLAYBOOKS: dict[str, str] = {
    FailureReason.INSUFFICIENT_FUNDS: "payday_retry",
    FailureReason.CARD_EXPIRED: "update_card",
    FailureReason.MANDATE_REVOKED: "understand_cancellation",
    FailureReason.AMOUNT_ABOVE_MANDATE_LIMIT: "one_time_link",
    FailureReason.AUTHENTICATION_FAILED: "approve_again",
    FailureReason.BANK_TECHNICAL_DECLINE: "alternate_method",
}

# What the agent says happened.
EXPLANATIONS: dict[str, str] = {
    FailureReason.INSUFFICIENT_FUNDS: "the automatic payment could not go through because there was not enough balance in the account at the time",
    FailureReason.CARD_EXPIRED: "the card saved for automatic payments has expired",
    FailureReason.MANDATE_REVOKED: "the UPI AutoPay mandate for this subscription was cancelled, so the payment was not collected",
    FailureReason.AMOUNT_ABOVE_MANDATE_LIMIT: "the amount was higher than the limit set on the UPI AutoPay mandate, so the bank did not allow it automatically",
    FailureReason.AUTHENTICATION_FAILED: "the payment needed an approval from the customer's UPI app that was not completed",
    FailureReason.BANK_TECHNICAL_DECLINE: "the bank declined the payment because of a technical issue on the bank's side, even after retrying",
    FailureReason.DISPUTED: "the customer has raised a dispute about this charge",
}


@dataclass
class Decision:
    customer_id: str
    action: str
    reason_code: str
    reason: str
    playbook: str | None
    priority_score: float
    grace_end: str
    next_contact_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _window(customer: Customer, s: Settings) -> tuple[time, time]:
    """Merchant calling window, narrowed to the customer's preferred window if set."""
    start, end = _parse_hhmm(s.call_window_start), _parse_hhmm(s.call_window_end)
    if customer.preferred_call_window:
        p_start, p_end = (_parse_hhmm(x) for x in customer.preferred_call_window.split("-"))
        start, end = max(start, p_start), min(end, p_end)
    return start, end


def next_allowed_time(customer: Customer, now: datetime, s: Settings) -> datetime | None:
    """Return None if we may call right now, else the next moment we may call."""
    start, end = _window(customer, s)
    if start >= end:  # preferred window is entirely outside policy hours, ignore it
        start, end = _parse_hhmm(s.call_window_start), _parse_hhmm(s.call_window_end)
    local_t = now.timetz().replace(tzinfo=None)
    if start <= local_t < end:
        return None
    day = now.date() if local_t < start else now.date() + timedelta(days=1)
    return datetime.combine(day, start, tzinfo=now.tzinfo)


def grace_end(customer: Customer, s: Settings) -> date:
    return customer.failed_on + timedelta(days=s.grace_period_days)


def suggested_retry_date(customer: Customer, today: date, s: Settings) -> date | None:
    """Next payday inside the grace period, if the customer has a known payday."""
    if not customer.payday_day:
        return None
    end = grace_end(customer, s)
    d = today + timedelta(days=1)
    while d <= end:
        if d.day == customer.payday_day:
            return d
        d += timedelta(days=1)
    return None


WAITING_OUTCOMES = {
    "retry_scheduled": "Retry already booked with the customer. Wait for the retry result.",
    "promised_to_pay": "Customer promised to pay. Wait until the promised date.",
    "link_sent": "Payment link already sent. Wait for payment before contacting again.",
}


def decide(customer: Customer, now: datetime, s: Settings, last_outcome: str | None = None,
           last_call_at: datetime | None = None) -> Decision:
    tz = ZoneInfo(s.timezone)
    now = now.astimezone(tz)
    reason = customer.failure_reason
    ge = grace_end(customer, s).isoformat()
    score = round(customer.amount_due_inr * RECOVERY_LIKELIHOOD.get(reason, 0.5), 2)

    def d(action: Action, code: str, text: str, playbook: str | None = None, nxt: datetime | None = None) -> Decision:
        return Decision(
            customer_id=customer.customer_id, action=action.value, reason_code=code, reason=text,
            playbook=playbook, priority_score=score if action == Action.CALL else 0.0,
            grace_end=ge, next_contact_at=nxt.isoformat() if nxt else None,
        )

    # Consent and contact limits.
    if not customer.consent_to_call:
        return d(Action.SKIP, "no_consent", "No consent to be called. Use email or in-app reminder instead.")
    if customer.dnd:
        return d(Action.SKIP, "dnd", "Customer is on the do-not-call list.")
    if customer.call_attempts_7d >= s.max_call_attempts_7d:
        return d(Action.SKIP, "max_attempts",
                 f"Already called {customer.call_attempts_7d} times in 7 days. Passing to the merchant team instead of calling again.")
    if now.date() > grace_end(customer, s):
        return d(Action.HUMAN_HANDOFF, "grace_expired", "Grace period is over. Merchant decides whether to pause the subscription.")

    # Something is already agreed.
    if last_outcome in WAITING_OUTCOMES:
        return d(Action.WAIT, last_outcome, WAITING_OUTCOMES[last_outcome])

    # Disputes go to a person.
    if reason == FailureReason.DISPUTED:
        return d(Action.HUMAN_HANDOFF, "disputed", "Customer disputes the charge. Route to the merchant's support team, do not chase payment.")

    # Bank-side declines usually clear on retry, so retry before calling.
    if reason == FailureReason.BANK_TECHNICAL_DECLINE and customer.silent_retries_done < s.max_silent_retries:
        return d(Action.SILENT_RETRY, "bank_issue_retry_first",
                 f"Bank-side technical decline. Retry silently ({customer.silent_retries_done}/{s.max_silent_retries} done) before calling.")

    # Not too soon after the last call, and inside calling hours.
    playbook = PLAYBOOKS.get(reason)
    if playbook is None:
        return d(Action.HUMAN_HANDOFF, "unknown_reason", f"Unknown failure reason '{reason}'. A person should review it.")
    if last_call_at is not None:
        earliest = last_call_at.astimezone(tz) + timedelta(hours=s.min_hours_between_calls)
        if now < earliest:
            nxt = next_allowed_time(customer, earliest, s) or earliest
            return d(Action.SCHEDULE, "too_soon", f"Called less than {s.min_hours_between_calls:g} hours ago. Try again later.", playbook, nxt)
    nxt = next_allowed_time(customer, now, s)
    if nxt is not None:
        return d(Action.SCHEDULE, "outside_window", "Outside the allowed calling window for this customer.", playbook, nxt)

    return d(Action.CALL, "eligible", "Eligible for a recovery call.", playbook)


def plan(customers: list[Customer], now: datetime, s: Settings,
         last_outcomes: dict[str, str] | None = None,
         last_calls: dict[str, datetime] | None = None) -> list[Decision]:
    """Decide for everyone; calls are ordered by expected rupees recovered (highest first)."""
    last_outcomes, last_calls = last_outcomes or {}, last_calls or {}
    decisions = [decide(c, now, s, last_outcomes.get(c.customer_id), last_calls.get(c.customer_id)) for c in customers]
    order = {Action.CALL.value: 0, Action.SCHEDULE.value: 1, Action.WAIT.value: 2, Action.SILENT_RETRY.value: 3,
             Action.HUMAN_HANDOFF.value: 4, Action.SKIP.value: 5}
    return sorted(decisions, key=lambda x: (order[x.action], -x.priority_score, x.customer_id))


def agent_variables(customer: Customer, decision: Decision, merchant_name: str, today: date, s: Settings) -> dict[str, str]:
    """Variables passed to the Sarvam agent when the call is created."""
    retry_hint = suggested_retry_date(customer, today, s)
    return {
        "customer_id": customer.customer_id,
        "customer_name": customer.first_name,
        "merchant_name": merchant_name,
        "plan_name": customer.plan_name,
        "amount_due": f"{customer.amount_due_inr} rupees",
        "failure_reason": customer.failure_reason,
        "failure_explained": EXPLANATIONS.get(customer.failure_reason, "the automatic payment did not go through"),
        "playbook": decision.playbook or "",
        "latest_retry_date": decision.grace_end,
        "suggested_retry_date": retry_hint.isoformat() if retry_hint else "",
        "preferred_language": customer.preferred_language,
    }
