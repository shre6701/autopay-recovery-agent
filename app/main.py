"""HTTP API.

/tools/*             called by the Sarvam agent during a call
/webhooks/sarvam/*   Sarvam posts here after each call attempt
/webhooks/razorpay   payment_link.paid marks the customer as recovered
/                    dashboard
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .campaign import current_plan, parse_now
from .config import Settings, get_settings
from .db import Store
from .decision import grace_end
from .models import Outcome
from .razorpay_client import (RazorpayError, create_payment_link, fetch_payment_link,
                              reference_id_for, verify_webhook_signature)

STATIC = Path(__file__).parent / "static"

class LinkReq(BaseModel):
    customer_id: str
    interaction_id: str | None = None


class RetryReq(BaseModel):
    customer_id: str
    retry_date: str = Field(description="YYYY-MM-DD")


class OutcomeReq(BaseModel):
    customer_id: str
    outcome: str
    promise_date: str | None = None
    notes: str | None = None


class EscalateReq(BaseModel):
    customer_id: str
    reason: str


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    store = Store(s)
    store.load_seed()
    app = FastAPI(title="Autopay Recovery Voice Agent", version="1.0.0")
    app.state.settings, app.state.store = s, store

    def require_tool_secret(x_tool_secret: str | None = Header(default=None),
                            authorization: str | None = Header(default=None)) -> None:
        # Sarvam sends it as a bearer token. X-Tool-Secret is accepted for curl testing.
        if not s.tool_secret:
            return
        bearer = authorization.removeprefix("Bearer ").strip() if authorization else None
        if s.tool_secret not in {x_tool_secret, bearer}:
            raise HTTPException(status_code=401, detail="invalid tool secret")

    def today_local() -> date:
        return datetime.now(ZoneInfo(s.timezone)).date()

    def load_open(customer_id: str, tool: str, req: dict[str, Any]):
        c = store.customer(customer_id)
        if c is None:
            msg = {"ok": False, "message": "I could not find this customer record. Apologise and say a team member will call back."}
            store.log_tool(tool, customer_id, req, msg, False)
            return None, msg
        return c, None

    def link_delivery() -> str:
        if s.razorpay_notify_sms and s.razorpay_mode == "test":
            return "Tell the customer it has been sent by SMS to their registered number"
        return "Tell the customer the team will send it to their registered number"

    def reply(tool: str, customer_id: str, req: dict[str, Any], resp: dict[str, Any]) -> dict[str, Any]:
        store.log_tool(tool, customer_id, req, resp, resp.get("ok", False))
        return resp

    @app.post("/tools/create_payment_link", dependencies=[Depends(require_tool_secret)])
    def tool_create_payment_link(body: LinkReq) -> dict[str, Any]:
        req = body.model_dump()
        c, err = load_open(body.customer_id, "create_payment_link", req)
        if err:
            return err
        if c.dnd:
            return reply("create_payment_link", c.customer_id, req,
                         {"ok": False, "message": "This customer asked not to be contacted. Do not send anything. End the call politely."})
        existing = store.link_by_reference(reference_id_for(c))
        if existing:
            return reply("create_payment_link", c.customer_id, req, {
                "ok": True, "link_id": existing["link_id"], "amount_inr": existing["amount_inr"], "reused": True,
                "message": f"A payment link for {existing['amount_inr']} rupees already exists for this payment. "
                           f"{link_delivery()}. It is valid for 3 days."})
        try:
            link, mode = create_payment_link(c, store.merchant()["name"], s)
        except RazorpayError as e:
            return reply("create_payment_link", c.customer_id, {**req, "error": str(e)},
                         {"ok": False, "message": "The payment link could not be created right now. Apologise and offer to "
                                                  "schedule a retry instead, or say the team will send it shortly."})
        store.save_link(link, c.customer_id, mode)
        store.add_outcome(c.customer_id, Outcome.LINK_SENT.value, "tool")
        return reply("create_payment_link", c.customer_id, req, {
            "ok": True, "link_id": link["id"], "amount_inr": c.amount_due_inr, "mode": mode, "reused": False,
            "message": f"Payment link for {c.amount_due_inr} rupees created. {link_delivery()}. "
                       "It is valid for 3 days and works with UPI, card or netbanking."})

    @app.post("/tools/schedule_retry", dependencies=[Depends(require_tool_secret)])
    def tool_schedule_retry(body: RetryReq) -> dict[str, Any]:
        req = body.model_dump()
        c, err = load_open(body.customer_id, "schedule_retry", req)
        if err:
            return err
        earliest, latest = today_local() + timedelta(days=1), grace_end(c, s)
        try:
            d = date.fromisoformat(body.retry_date.strip()[:10])
        except ValueError:
            return reply("schedule_retry", c.customer_id, req, {
                "ok": False, "message": f"The date was not understood. Ask for a date between {earliest:%d %B} and {latest:%d %B}."})
        if not earliest <= d <= latest:
            return reply("schedule_retry", c.customer_id, req, {
                "ok": False, "earliest": earliest.isoformat(), "latest": latest.isoformat(),
                "message": f"We can only retry between {earliest:%d %B} and {latest:%d %B}. Offer a date in that range, "
                           "or offer the payment link to pay now."})
        store.add_retry(c.customer_id, d.isoformat())
        store.add_outcome(c.customer_id, Outcome.RETRY_SCHEDULED.value, "tool", promise_date=d.isoformat())
        return reply("schedule_retry", c.customer_id, req, {
            "ok": True, "retry_date": d.isoformat(),
            "message": f"Retry scheduled for {d:%A, %d %B}. Ask the customer to keep {c.amount_due_inr} rupees in the account that morning."})

    agent_outcomes = {o.value for o in Outcome} - {Outcome.PAID_VIA_LINK.value}

    @app.post("/tools/record_outcome", dependencies=[Depends(require_tool_secret)])
    def tool_record_outcome(body: OutcomeReq) -> dict[str, Any]:
        req = body.model_dump()
        c, err = load_open(body.customer_id, "record_outcome", req)
        if err:
            return err
        outcome = body.outcome.strip().lower()
        if outcome not in agent_outcomes:
            return reply("record_outcome", c.customer_id, req,
                         {"ok": False, "message": f"Unknown outcome. Use one of: {', '.join(sorted(agent_outcomes))}."})
        store.add_outcome(c.customer_id, outcome, "tool", body.promise_date, body.notes)
        msg = "Outcome saved."
        if outcome == Outcome.DO_NOT_CALL.value:
            c.dnd = True
            store.update_customer(c)
            msg = "Customer added to do-not-call list. Confirm they will not be called again, then end the call."
        elif outcome == Outcome.WANTS_TO_CANCEL.value:
            store.set_status(c.customer_id, "cancelled")
            store.add_escalation(c.customer_id, "Customer wants to cancel. Merchant to process cancellation.")
            msg = "Saved. Tell the customer the team will process the cancellation and confirm by message. Do not push back."
        elif outcome in {Outcome.ALREADY_PAID.value, Outcome.DISPUTES_CHARGE.value, Outcome.WRONG_PERSON.value}:
            store.set_status(c.customer_id, "handed_off")
            store.add_escalation(c.customer_id, f"Agent recorded '{outcome}'. {body.notes or ''}".strip())
            msg = "Saved and passed to the team. Tell the customer someone from the team will look into it and get back to them."
        return reply("record_outcome", c.customer_id, req, {"ok": True, "message": msg})

    @app.post("/tools/escalate_to_human", dependencies=[Depends(require_tool_secret)])
    def tool_escalate(body: EscalateReq) -> dict[str, Any]:
        req = body.model_dump()
        c, err = load_open(body.customer_id, "escalate_to_human", req)
        if err:
            return err
        store.add_escalation(c.customer_id, body.reason)
        store.add_outcome(c.customer_id, Outcome.ESCALATED.value, "tool", notes=body.reason)
        store.set_status(c.customer_id, "handed_off")
        support = store.merchant().get("support_hours", "working hours")
        return reply("escalate_to_human", c.customer_id, req, {
            "ok": True, "message": f"Escalated. Tell the customer a team member will call back during {support}."})

    @app.post("/webhooks/sarvam/outbound")
    async def sarvam_webhook(request: Request, token: str | None = Query(default=None)) -> dict[str, Any]:
        if s.tool_secret and token != s.tool_secret:
            raise HTTPException(status_code=401, detail="invalid token")
        p = await request.json()
        fav = p.get("final_agent_variables") or {}
        meta = (p.get("webhook_config") or {}).get("metadata") or {}
        customer_id = meta.get("customer_id") or fav.get("customer_id")
        if not customer_id or not p.get("attempt_id"):
            raise HTTPException(status_code=422, detail="missing attempt_id or customer_id")
        store.update_attempt(
            p["attempt_id"], customer_id,
            status=p.get("status", "unknown"), duration_s=p.get("duration"),
            interaction_id=p.get("interaction_id"), failure_reason=p.get("failure_reason"),
            final_variables=json.dumps(fav), transcript=json.dumps(p.get("interaction_transcript") or []),
            simulated=bool(meta.get("simulated")),
        )
        # Sarvam extracts call_outcome after the call. Store it too, in case the agent never called record_outcome.
        call_outcome = (fav.get("call_outcome") or "").strip().lower()
        if p.get("status") == "connected" and call_outcome in agent_outcomes:
            store.add_outcome(customer_id, call_outcome, "webhook", fav.get("promise_date") or None, fav.get("call_summary"))
        if p.get("status") == "failed" and "NDNC" in (p.get("failure_reason") or ""):
            c = store.customer(customer_id)
            if c:
                c.dnd = True
                store.update_customer(c)
        return {"ok": True}

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(request: Request, x_razorpay_signature: str | None = Header(default=None)) -> dict[str, Any]:
        raw = await request.body()
        if not verify_webhook_signature(raw, x_razorpay_signature or "", s.razorpay_webhook_secret):
            raise HTTPException(status_code=400, detail="bad signature")
        event = json.loads(raw)
        if event.get("event") == "payment_link.paid":
            link_id = event["payload"]["payment_link"]["entity"]["id"]
            _mark_recovered(link_id)
        return {"ok": True}

    def _mark_recovered(link_id: str) -> str | None:
        customer_id = store.mark_link_paid(link_id)
        if customer_id:
            store.set_status(customer_id, "recovered")
            store.add_outcome(customer_id, Outcome.PAID_VIA_LINK.value, "razorpay")
        return customer_id

    @app.post("/api/refresh-payments")
    def refresh_payments() -> dict[str, Any]:
        """Polls Razorpay for link status, for when its webhook can't reach this machine."""
        if s.razorpay_mode != "test":
            return {"ok": False, "message": "Only available with Razorpay test keys."}
        updated = []
        for link in store.links():
            if link["status"] != "paid" and link["mode"] == "test":
                try:
                    if fetch_payment_link(link["link_id"], s).get("status") == "paid":
                        _mark_recovered(link["link_id"])
                        updated.append(link["link_id"])
                except RazorpayError:
                    continue
        return {"ok": True, "paid": updated}

    @app.get("/mock/pay/{link_id}", response_class=HTMLResponse)
    def mock_pay_page(link_id: str) -> str:
        link = next((l for l in store.links() if l["link_id"] == link_id), None)
        if not link or link["mode"] != "mock":
            raise HTTPException(status_code=404)
        state = "Paid" if link["status"] == "paid" else f"Pay Rs {link['amount_inr']}"
        return f"""<!doctype html><meta name=viewport content="width=device-width"><title>Mock payment</title>
<body style="font-family:system-ui;max-width:420px;margin:40px auto;padding:0 16px">
<p style="color:#b45309">Mock payment page. No Razorpay keys are set, so no money moves.</p>
<h2>{link['customer_id']}: Rs {link['amount_inr']}</h2>
<form method=post><button {'disabled' if link['status']=='paid' else ''} style="font-size:18px;padding:10px 18px">{state}</button></form>"""

    @app.post("/mock/pay/{link_id}", response_class=HTMLResponse)
    def mock_pay(link_id: str) -> str:
        link = next((l for l in store.links() if l["link_id"] == link_id), None)
        if not link or link["mode"] != "mock":
            raise HTTPException(status_code=404)
        _mark_recovered(link_id)
        return mock_pay_page(link_id)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "razorpay_mode": s.razorpay_mode, "sarvam_ready": s.sarvam_ready}

    @app.get("/api/state")
    def state(now: str | None = None) -> dict[str, Any]:
        n = parse_now(now, s)
        customers = {c.customer_id: c for c in store.customers()}
        statuses = store.statuses()
        decisions = {d.customer_id: d.to_dict() for d in current_plan(store, s, n)}
        attempts, links, outcomes = store.attempts(), store.links(), store.outcomes()

        rows = []
        for cid, c in customers.items():
            last = next((o for o in outcomes if o["customer_id"] == cid), None)
            link = next((l for l in links if l["customer_id"] == cid), None)
            rows.append({**c.to_dict(), "status": statuses.get(cid, "open"), "decision": decisions.get(cid),
                         "last_outcome": last, "link": link,
                         "calls": [a for a in attempts if a["customer_id"] == cid]})

        connected = [a for a in attempts if a["status"] == "connected"]
        minutes = sum((a["duration_s"] or 0) for a in connected) / 60
        cost = minutes * (s.agent_cost_per_min_inr + s.telephony_cost_per_min_inr)
        recovered = sum(l["amount_inr"] for l in links if l["status"] == "paid")
        called_ids = {a["customer_id"] for a in attempts}
        targeted = sum(customers[cid].amount_due_inr for cid in called_ids if cid in customers)
        promised_ids = {o["customer_id"] for o in outcomes if o["outcome"] in {"retry_scheduled", "promised_to_pay"}}
        promised = sum(customers[cid].amount_due_inr for cid in promised_ids
                       if cid in customers and statuses.get(cid) != "recovered")
        metrics = {
            "total_due_inr": sum(c.amount_due_inr for c in customers.values()),
            "calls_placed": len(attempts),
            "calls_connected": len(connected),
            "connect_rate": round(len(connected) / len(attempts), 2) if attempts else None,
            "targeted_inr": targeted,
            "recovered_inr": recovered,
            "recovery_rate": round(recovered / targeted, 2) if targeted else None,
            "promised_inr": promised,
            "call_minutes": round(minutes, 2),
            "call_cost_inr": round(cost, 2),
            "cost_per_100_inr_recovered": round(cost / recovered * 100, 2) if recovered else None,
            "skipped_or_human": sum(1 for d in decisions.values() if d["action"] in {"skip", "human_handoff"}),
        }
        return {
            "now": n.isoformat(), "merchant": store.merchant(), "metrics": metrics, "customers": rows,
            "retries": store.retries(), "escalations": store.escalations(), "tool_log": store.tool_log(25),
            "config": {"razorpay_mode": s.razorpay_mode, "sarvam_ready": s.sarvam_ready,
                       "demo_phone_set": bool(s.demo_phone_number),
                       "call_window": f"{s.call_window_start}-{s.call_window_end} {s.timezone}"},
        }

    @app.get("/", response_class=FileResponse)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC / "dashboard.html")

    return app


app = create_app()
