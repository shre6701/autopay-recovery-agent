"""Fakes four calls against the running backend. No Sarvam account needed, no cost.

For each call it hits the same /tools/* endpoints the agent would, then posts a
webhook in the format from Sarvam's docs (instant-outbound/webhook-payload).

    uvicorn app.main:app --port 8000          # terminal 1
    python scripts/simulate_call.py            # terminal 2, then open http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.decision import suggested_retry_date  # noqa: E402
from app.models import Customer  # noqa: E402


class Sim:
    def __init__(self, base: str, secret: str):
        self.base = base.rstrip("/")
        self.secret = secret
        self.http = httpx.Client(timeout=10.0)

    def tool(self, name: str, body: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.secret}"} if self.secret else {}
        r = self.http.post(f"{self.base}/tools/{name}", json=body, headers=headers)
        r.raise_for_status()
        out = r.json()
        print(f"   tool {name}({', '.join(f'{k}={v}' for k, v in body.items())}) -> ok={out['ok']}: {out['message']}")
        return out

    def webhook(self, customer_id: str, status: str, transcript: list[tuple[str, str]] | None,
                final_vars: dict | None, duration: float | None) -> None:
        token = f"?token={self.secret}" if self.secret else ""
        url = f"{self.base}/webhooks/sarvam/outbound{token}"
        payload = {  # format: https://docs.sarvam.ai/voice-agents/api/instant-outbound/webhook-payload
            "attempt_id": f"sim-{uuid.uuid4()}",
            "status": status,
            "channel_info": {"channel_type": "v2v", "channel_provider": "simulated", "agent_phone_number": "+910000000000"},
            "duration": duration,
            "interaction_id": f"sim/{uuid.uuid4().hex[:16]}" if status == "connected" else None,
            "failure_reason": None,
            "final_agent_variables": final_vars,
            "webhook_config": {"url": url, "metadata": {"customer_id": customer_id, "simulated": True}},
            "interaction_transcript": [{"role": r, "en_text": t} for r, t in transcript] if transcript else None,
        }
        self.http.post(url, json=payload).raise_for_status()
        print(f"   webhook: call {status}" + (f", {duration:.0f}s" if duration else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--pay", action="store_true", help="also 'pay' the mock link so the dashboard shows a recovery")
    args = ap.parse_args()

    s = get_settings()
    sim = Sim(args.base, s.tool_secret)
    state = sim.http.get(f"{sim.base}/api/state").json()
    customers = {r["customer_id"]: Customer.from_dict(r) for r in state["customers"]}
    today = datetime.now(ZoneInfo(s.timezone)).date()

    # 1. Ananya: amount above UPI AutoPay limit -> agrees to a one-time link
    print("\n[1] CUST-006 Ananya, amount above mandate limit (expect: payment link)")
    link = sim.tool("create_payment_link", {"customer_id": "CUST-006"})
    sim.webhook("CUST-006", "connected", [
        ("agent", "Hello, am I speaking with Ananya?"),
        ("user", "Yes, who is this?"),
        ("agent", "This is Asha, an automated assistant calling for Kaveri Fitness about your annual membership."),
        ("user", "Okay."),
        ("agent", "Your renewal of 18000 rupees didn't go through. It was above the limit set on your UPI AutoPay, so the bank "
                  "blocked it. I can set up a one-time payment link instead. Would that work?"),
        ("user", "Oh, I didn't know there was a limit. Yeah, send the link."),
        ("agent", "One moment. Done, the team will send it to your registered number. It's valid for 3 days."),
        ("user", "Fine, thanks."),
    ], {"customer_id": "CUST-006", "call_outcome": "link_sent", "promise_date": "", "call_summary": "Agreed to pay by one-time link."}, 58.0)
    if args.pay and link.get("mode") == "mock":
        sim.http.post(f"{sim.base}/mock/pay/{link['link_id']}").raise_for_status()
        print("   customer paid the (mock) link -> recovered")

    # 2. Aarav: low balance -> first asks for a date that's too late, then picks payday
    print("\n[2] CUST-001 Aarav, insufficient funds (expect: retry booked on payday)")
    aarav = customers["CUST-001"]
    too_late = (date.fromisoformat(aarav.failed_at) + timedelta(days=s.grace_period_days + 5)).isoformat()
    sim.tool("schedule_retry", {"customer_id": "CUST-001", "retry_date": too_late})
    payday = suggested_retry_date(aarav, today, s) or (today + timedelta(days=1))
    grace = date.fromisoformat(aarav.failed_at) + timedelta(days=s.grace_period_days)
    sim.tool("schedule_retry", {"customer_id": "CUST-001", "retry_date": payday.isoformat()})
    sim.webhook("CUST-001", "connected", [
        ("agent", "Hello, am I speaking with Aarav?"),
        ("user", "Haan, bolo."),
        ("agent", "This is Asha calling for Kaveri Fitness. Your gym membership payment of 1499 rupees didn't go through "
                  "because the balance was low. Should we try it again on another date, or would you like a link to pay now?"),
        ("user", "Try again at the end of the month."),
        ("agent", f"Sorry, the latest we can retry is {grace:%d %B}. Your salary usually comes on the {payday.day}th. "
                  f"Can we try on {payday:%d %B}?"),
        ("user", "Okay, that's fine."),
        ("agent", f"Done, we'll retry on {payday:%d %B}. Please keep 1499 rupees in the account that morning."),
    ], {"customer_id": "CUST-001", "call_outcome": "retry_scheduled", "promise_date": payday.isoformat(),
        "call_summary": "Retry booked for payday."}, 74.0)

    # 3. Rohan: cancelled the mandate on purpose -> agent records it and doesn't push
    print("\n[3] CUST-003 Rohan, mandate revoked (expect: respects cancellation, no pressure)")
    sim.tool("record_outcome", {"customer_id": "CUST-003", "outcome": "wants_to_cancel", "notes": "Moving to another city."})
    sim.webhook("CUST-003", "connected", [
        ("agent", "Hello, am I speaking with Rohan?"),
        ("user", "Yes."),
        ("agent", "This is Asha calling for Kaveri Fitness. The UPI AutoPay for your gym membership was cancelled. "
                  "Did you mean to cancel it?"),
        ("user", "Yes, I'm moving to Pune next week."),
        ("agent", "Got it, thanks for letting us know. The team will close the membership and confirm by message."),
    ], {"customer_id": "CUST-003", "call_outcome": "wants_to_cancel", "promise_date": "", "call_summary": "Moving cities, wants to cancel."}, 41.0)

    # 4. Meera: no answer
    print("\n[4] CUST-010 Meera, bank decline after 2 silent retries (expect: no answer logged)")
    sim.webhook("CUST-010", "no_answer", None, None, None)

    print(f"\nDone. Open {sim.base}/ to see the dashboard.\n")


if __name__ == "__main__":
    main()
