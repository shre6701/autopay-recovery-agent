from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import Any


class FailureReason(str, Enum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_EXPIRED = "card_expired"
    MANDATE_REVOKED = "mandate_revoked"
    BANK_TECHNICAL_DECLINE = "bank_technical_decline"
    AMOUNT_ABOVE_MANDATE_LIMIT = "amount_above_mandate_limit"
    AUTHENTICATION_FAILED = "authentication_failed"
    DISPUTED = "disputed"


class Action(str, Enum):
    CALL = "call"                    # place a voice call now
    SCHEDULE = "schedule"            # call later, inside an allowed window
    SILENT_RETRY = "silent_retry"    # retry the debit, do not bother the customer
    HUMAN_HANDOFF = "human_handoff"  # a person must handle it, not a bot
    SKIP = "skip"                    # do not contact (no consent, DND, too many attempts)
    WAIT = "wait"                    # already agreed a next step; don't call again


class Outcome(str, Enum):
    PAID_VIA_LINK = "paid_via_link"            # set by Razorpay webhook / poll, not by the agent
    LINK_SENT = "link_sent"
    PROMISED_TO_PAY = "promised_to_pay"
    RETRY_SCHEDULED = "retry_scheduled"
    WANTS_TO_CANCEL = "wants_to_cancel"
    ALREADY_PAID = "already_paid"
    DISPUTES_CHARGE = "disputes_charge"
    CALLBACK_REQUESTED = "callback_requested"
    WRONG_PERSON = "wrong_person"
    DO_NOT_CALL = "do_not_call"
    NO_RESOLUTION = "no_resolution"
    ESCALATED = "escalated"


@dataclass
class Customer:
    customer_id: str
    name: str
    phone: str
    email: str
    preferred_language: str
    plan_name: str
    amount_due_inr: int
    mandate_type: str
    failure_reason: str
    failed_at: str
    silent_retries_done: int
    call_attempts_7d: int
    consent_to_call: bool
    dnd: bool
    preferred_call_window: str | None
    payday_day: int | None
    tenure_months: int
    failures_last_12m: int

    @property
    def failed_on(self) -> date:
        return date.fromisoformat(self.failed_at)

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Customer":
        fields = cls.__dataclass_fields__.keys()
        return cls(**{k: d[k] for k in fields if k in d})
