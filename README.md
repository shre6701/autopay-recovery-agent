# Autopay recovery voice agent

Calls customers whose autopay failed (UPI AutoPay, card or e-mandate) and tries to get the payment fixed. Voice runs on Sarvam Voice Agents. Payments go through Razorpay payment links in test mode.

Demo video: [add link]

All 10 customer records are made up. Live calls only ever go to one number I control (`DEMO_PHONE_NUMBER`).

## Approach

The reason a payment failed decides what to do about it, and for a few of them a call is the wrong move. So I split the work in two:

1. A decision engine in plain Python (`app/decision.py`). It decides who gets a call, when, and what the agent should try. Consent, DND, calling hours and attempt limits live here, not in the prompt, so the LLM can't skip them.
2. The Sarvam agent, which handles the conversation and calls four endpoints on my backend to actually do things.

How each failure reason is handled:

| Failure reason | What happens | Reasoning |
|---|---|---|
| Bank technical decline | Silent retry, up to 2 times. Call only if both fail | These usually clear on retry. Calling costs money and annoys people. |
| Not enough balance | Call. Offer a retry on payday, or a link to pay now | They want to pay, just not today. |
| Card expired | Call. Send a payment link | The old card won't work again. |
| Amount above the UPI AutoPay limit | Call. Send a one-time link | The mandate blocked it, not the customer. |
| UPI approval not completed | Call. Send a payment link | Usually a missed notification. |
| Mandate cancelled | Call, but ask why before offering anything. If they meant to cancel, record it and stop | Could be a mistake, could be a deliberate exit. |
| Charge disputed | No bot. Goes to a person | Chasing a disputed charge with a bot is a bad idea. |

On top of that, nobody gets called without consent, if they're on DND, outside 9 AM to 7 PM IST, more than 3 times in 7 days, or within 4 hours of the last call. If someone already agreed to a retry date or got a link, the engine waits instead of calling again.

## What happens to the 10 records at 11 AM IST

| Customer | Failure | Due | Decision |
|---|---|---:|---|
| CUST-006 Ananya | above UPI limit | 18,000 | call (first, highest expected value) |
| CUST-010 Meera | bank decline, 2 retries already failed | 2,499 | call |
| CUST-001 Aarav | low balance | 1,499 | call, offer payday retry |
| CUST-003 Rohan | mandate cancelled | 999 | call, ask why first |
| CUST-002 Priya | card expired | 4,999 | scheduled for 5 PM (her preferred window) |
| CUST-004 Sneha | bank decline | 1,499 | silent retry, no call |
| CUST-007 Karthik | disputed | 1,499 | handed to a person |
| CUST-005 Vikram | low balance | 2,499 | skipped, no consent |
| CUST-008 Fatima | low balance | 999 | skipped, on DND |
| CUST-009 Arjun | approval not completed | 1,499 | skipped, already called 3 times this week |

Call order is amount due x an assumed recovery rate for that failure reason. The rates are guesses (see Assumptions).

## Flow

```
customers.json -> decision engine -> call / schedule / silent retry / human / skip / wait
                                        |
                                        v  (call)
                  scripts/dial.py -> Sarvam outbound API -> phone
                                        |
                  during the call, the agent hits my backend:
                    POST /tools/create_payment_link   Razorpay payment link (test mode)
                    POST /tools/schedule_retry        rejects dates after the grace period
                    POST /tools/record_outcome        cancel, DND, dispute, callback, etc.
                    POST /tools/escalate_to_human     hands over to the merchant's team
                                        |
                  after the call:
                    Sarvam webhook   -> status, duration, transcript, extracted outcome
                    Razorpay webhook -> payment_link.paid marks the customer recovered
```

## What the agent can do

- Check it's speaking to the right person before mentioning any amount.
- Explain the failure in the customer's language. Sarvam supports 11 Indian languages plus English, and the agent follows if the customer switches.
- Send a payment link, book a retry date, record how the call ended, or hand over to a person.

## What it can't do

These are checked in the backend, so a bad prompt or a confused agent can't get around them:

- Send a link to someone on DND.
- Create a second payment link for the same failed payment. A repeat request returns the first one.
- Book a retry outside tomorrow to the end of the grace period.
- Mark a payment as paid. Only Razorpay can do that, via webhook or status check.

These rely on the prompt, so they're covered by the Sarvam test cases in `agent/test_cases.md` instead:

- Take card numbers, PINs or OTPs over the phone.
- Offer discounts or deadlines it wasn't given.
- Argue with someone who wants to cancel.

## Running it locally (no keys needed)

Python 3.11 or newer.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # leave empty for mock mode

python scripts/plan.py --reset --now 11:00            # decisions as if it were 11 AM IST today
python -m pytest -q

uvicorn app.main:app --port 8000                       # terminal 1
python scripts/simulate_call.py --pay                  # terminal 2
# dashboard at http://localhost:8000
```

`simulate_call.py` sends the same tool requests and the same webhook payload Sarvam would, so the backend and dashboard can be tested without placing real calls. With no Razorpay keys, payment links are mocked and the "pay" page is local.

## Running real calls

Razorpay (test mode):
1. Generate test keys in the Razorpay dashboard and put them in `.env`. Keys starting with `rzp_live_` are refused.
2. Optionally add a webhook to `<PUBLIC_BASE_URL>/webhooks/razorpay` for `payment_link.paid` and set `RAZORPAY_WEBHOOK_SECRET`. Without it, `POST /api/refresh-payments` polls link status.

Public URL: run `ngrok http 8000` and set `PUBLIC_BASE_URL`. I avoided free hosts that sleep, because a cold start in the middle of a call leaves the customer in silence.

Sarvam:
1. Create the agent from `agent/instruction.md`, `agent/variables.md` and `agent/tools.md`.
2. Run the cases in `agent/test_cases.md`.
3. Add a phone number under Deploy, Phone Numbers.
4. Commit a version and fill the `SARVAM_*` values, `TOOL_SECRET` and `DEMO_PHONE_NUMBER` in `.env`.

Then:

```bash
python scripts/dial.py --now 11:00             # dry run, prints the exact request to Sarvam
python scripts/dial.py --only CUST-006 --live  # real call to DEMO_PHONE_NUMBER
```

Live calls ignore `--now` and use the real clock, so they only go out inside calling hours.

## Measuring it

The dashboard shows recovered amount, recovery rate (of the amount we called about), amount promised or booked for retry, connect rate, call cost, and cost per 100 rupees recovered. Cost is call minutes x the per-minute rates in `.env`.

Results from my test calls: [add after live run]

These numbers come from test mode and one phone, so they don't say much about real recovery rates. To find out whether the agent beats a merchant's current process (retries plus SMS), I'd split failed payments into two groups and compare recovered amount minus cost.

## Compliance choices

- Consent and DND are checked before every call. If the telephony provider rejects a call because the number is on the TRAI NDNC list, the customer is added to DND automatically.
- Calling hours default to 9 AM to 7 PM IST, narrowed to the customer's preferred window if they have one. RBI restricts loan recovery calls to 8 AM to 7 PM. Subscriptions aren't loans, so that rule doesn't strictly apply, but I used it as the outer limit and made the window a setting.
- The agent never threatens, invents offers, or pushes someone who wants to cancel.
- Secrets only live in `.env`, which is git-ignored. On Sarvam's side the tool secret goes in their Secrets store.

## Assumptions

- One merchant: a gym with monthly, quarterly and annual plans.
- 7-day grace period after a failed payment.
- The recovery rate per failure reason (used only to order calls) is made up. With real data it would come from the merchant's history.
- Payday is known for some customers.
- Agent cost is 3.50 rupees a minute (Sarvam pay-as-you-go). Telephony cost goes in `.env`.

## Limitations and what I'd change for production

| Now | In production |
|---|---|
| The payment link covers this month only. The broken mandate or card stays broken | For expired cards and cancelled mandates, send a Razorpay registration link so next month's debit works |
| The link shows on the dashboard. SMS only if `RAZORPAY_NOTIFY_SMS=true` | Send it on WhatsApp or SMS from a sender the customer recognises |
| Silent retries and scheduled calls are decided but not executed | A scheduler or queue that runs retries and places scheduled calls |
| The Sarvam webhook is authenticated with a token in the URL | Signed webhooks if Sarvam offers them, plus IP allowlisting |
| SQLite, one process | Postgres and a job queue |
| Voice only | Choose the channel per customer, for example WhatsApp first for small amounts |

## Cost to run

Roughly 300 to 600 rupees for the whole demo. That covers Sarvam call minutes at 3.50 a minute, a rented number at 59 rupees for 30 days, and test runs at 2 rupees each. Razorpay test mode and ngrok's free plan cost nothing.

## Files

```
app/decision.py         who to call, when, and what to try
app/main.py             tool endpoints, webhooks, dashboard API
app/razorpay_client.py  payment links (test or mock), webhook signature check
app/sarvam_client.py    outbound call request
app/campaign.py         builds the plan, places calls
app/db.py               SQLite
app/static/             dashboard
agent/                  Sarvam prompt, variables, tools, test cases
data/customers.json     the 10 fictional customers
scripts/                plan, dial, simulate_call, reset
tests/                  decision rules and API tests
```
