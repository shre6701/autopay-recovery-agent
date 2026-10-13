# Agent variables

Set up in Sarvam under Build, Variables.

## Input variables
These are filled by `scripts/dial.py` through `agent_variables` when the call is created.
Create each one with the default shown. The defaults only matter for Playground tests.

| Variable | Default (for Playground tests) | Sent to LLM? |
|---|---|---|
| `customer_id` | `CUST-006` | No (tools only) |
| `customer_name` | `Ananya` | Yes |
| `merchant_name` | `Kaveri Fitness` | Yes |
| `plan_name` | `Annual Premium Membership` | Yes |
| `amount_due` | `18000 rupees` | Yes |
| `failure_reason` | `amount_above_mandate_limit` | No |
| `failure_explained` | `the amount was higher than the limit set on the UPI AutoPay mandate, so the bank did not allow it automatically` | Yes |
| `playbook` | `one_time_link` | Yes |
| `latest_retry_date` | about 7 days from today, `YYYY-MM-DD` | Yes |
| `suggested_retry_date` | empty | Yes |
| `preferred_language` | `English` | Yes |

`customer_id` and `failure_reason` have "send to LLM" turned off. The agent doesn't need them to talk, and tools still get them.

## Output variables (extracted by Sarvam after the call)

| Variable | Type | Extraction prompt |
|---|---|---|
| `call_outcome` | Enum: `link_sent`, `retry_scheduled`, `promised_to_pay`, `wants_to_cancel`, `already_paid`, `disputes_charge`, `callback_requested`, `wrong_person`, `do_not_call`, `no_resolution`, `escalated` | The single best description of how the call ended. |
| `promise_date` | String | The date (YYYY-MM-DD) the customer agreed to pay or retry. Empty if none. |
| `call_summary` | String | One sentence: what the customer said and what was agreed. |
| `recovery_secured` | Enum: `yes`, `no` | `yes` only if a payment link was sent and accepted, or a retry date was agreed. |

Call goal ("Successful when"): `recovery_secured` = `yes`.

These come back in the post-call webhook as `final_agent_variables`. The backend stores `call_outcome` alongside whatever the tools recorded during the call, so a call where the agent forgot to call `record_outcome` still has an outcome.
