# API tools

Set up in Sarvam under Build, Tools, Create tool, API tool. All four call my backend. Replace `https://YOUR-PUBLIC-URL` with the ngrok URL.

Settings that are the same for every tool:
- Auth: Bearer token, with `TOOL_SECRET` pasted into the masked input. Sarvam keeps it in Settings, Secrets, so it isn't stored in the agent config.
- Max wait: 8 seconds.
- If it fails: "Sorry, I couldn't do that right now. Someone from the team will follow up with you."
- Response template: `{{message}}`, inserted with `@`.

Every reply has `ok` (true or false) and `message`, which is what the agent should tell the customer.

In each body, `customer_id` is mapped to the `@customer_id` variable. Fields marked "agent fills" are left for the agent to fill from the conversation. The cURL commands can be pasted into the tool builder to fill in method, URL, headers and body.

## create_payment_link

Description: Creates a Razorpay payment link for the amount due. Use it when the customer agrees to pay now. Only once per call.

| Field | Source |
|---|---|
| `customer_id` | `@customer_id` |
| `interaction_id` | Interaction ID call-context variable (optional, for logs) |

```bash
curl -X POST https://YOUR-PUBLIC-URL/tools/create_payment_link \
  -H "Authorization: Bearer $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"customer_id": "CUST-006"}'
```

## schedule_retry

Description: Schedules the automatic payment to be retried on a date the customer picks. Date format YYYY-MM-DD, later than today and no later than the latest retry date. If the date is rejected, offer one inside the range in the reply.

| Field | Source |
|---|---|
| `customer_id` | `@customer_id` |
| `retry_date` | agent fills, YYYY-MM-DD |

```bash
curl -X POST https://YOUR-PUBLIC-URL/tools/schedule_retry \
  -H "Authorization: Bearer $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"customer_id": "CUST-001", "retry_date": "2026-10-15"}'
```

## record_outcome

Description: Saves how the call ended when no link or retry was set up: wrong person, wants to cancel, already paid, disputes the charge, wants a callback, asked not to be called, promised to pay, or nothing agreed.

| Field | Source |
|---|---|
| `customer_id` | `@customer_id` |
| `outcome` | agent fills, one of `wrong_person`, `wants_to_cancel`, `already_paid`, `disputes_charge`, `callback_requested`, `do_not_call`, `promised_to_pay`, `no_resolution` |
| `promise_date` | agent fills, optional, YYYY-MM-DD |
| `notes` | agent fills, optional, short reason in English |

```bash
curl -X POST https://YOUR-PUBLIC-URL/tools/record_outcome \
  -H "Authorization: Bearer $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"customer_id": "CUST-003", "outcome": "wants_to_cancel", "notes": "Moving cities"}'
```

## escalate_to_human

Description: Hands the customer over to someone on the team. Use it when they're upset, ask for something you can't do (refund, discount, plan change), or the situation isn't clear.

| Field | Source |
|---|---|
| `customer_id` | `@customer_id` |
| `reason` | agent fills, one sentence |

```bash
curl -X POST https://YOUR-PUBLIC-URL/tools/escalate_to_human \
  -H "Authorization: Bearer $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"customer_id": "CUST-010", "reason": "Customer asked for a refund"}'
```

## Checks the backend does regardless of what the agent sends

- Unknown customer ID: safe reply telling the agent to say someone will call back.
- Customer on DND: no link is created.
- Second link request for the same failed payment: returns the first link.
- Retry date outside tomorrow to end of grace period: rejected, with the allowed range.
- `paid_via_link` can't be recorded by the agent. Only Razorpay sets it.
- `do_not_call` applies to all future calls straight away.
