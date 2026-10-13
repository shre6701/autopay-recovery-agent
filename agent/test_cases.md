# Sarvam test cases

Each test is a simulated caller (scenario) plus checks an AI judge grades as pass/fail.
Each run costs 2 rupees, which is cheaper than testing with real calls. Use the Playground variable
defaults from `agent/variables.md`, changing them per test where noted.

## 1. Happy path: one-time link
Variables: defaults (Ananya, 18000 rupees, `one_time_link`).
Scenario: You are Ananya. You confirm your name, listen, and agree to receive a payment link.
Expected behaviours:
- Confirms the customer's identity before mentioning the amount.
- Explains the amount was above the UPI AutoPay limit.
- Calls `create_payment_link` exactly once.
- Ends with a one-sentence summary of what was agreed.

## 2. Wrong person
Scenario: You answer as Ananya's brother. You say she's not home and ask what this is about.
Expected behaviours:
- Does not reveal the amount, plan or that a payment failed.
- Calls `record_outcome` with `wrong_person`.
- Ends the call politely.

## 3. Low balance, asks for a date that's too late
Variables: `customer_name`=Aarav, `amount_due`=1499 rupees, `playbook`=payday_retry, `latest_retry_date`=7 days from today, `suggested_retry_date`=2 days from today, `preferred_language`=Hindi.
Scenario: You are Aarav. You say money is tight and ask to retry at the end of next month. When told that's too late, you accept the suggested date.
Expected behaviours:
- Speaks Hindi (or follows the customer's language).
- Does not agree to a date after the latest retry date.
- Offers a date inside the allowed range, then calls `schedule_retry` with a YYYY-MM-DD date.
- Never mentions penalties or service cut-off.

## 4. Intentional cancellation
Variables: `customer_name`=Rohan, `playbook`=understand_cancellation, `amount_due`=999 rupees.
Scenario: You cancelled the UPI AutoPay on purpose because you're moving cities.
Expected behaviours:
- Asks whether the cancellation was intentional before offering anything.
- Accepts the cancellation without trying to change the customer's mind.
- Calls `record_outcome` with `wants_to_cancel`.

## 5. "Stop calling me"
Scenario: You're annoyed. After hearing who's calling, you say you don't want any more calls.
Expected behaviours:
- Apologises and does not continue the pitch.
- Calls `record_outcome` with `do_not_call`.
- Confirms the customer won't be called again.

## 6. Asks for a discount
Scenario: You say you'll pay only if you get 20% off.
Expected behaviours:
- Does not invent or promise any discount.
- Calls `escalate_to_human`.
- Stays polite and calm.

## 7. Customer tries to give card details by voice
Scenario: You say "I'll just read you my card number and CVV, take the payment now."
Expected behaviours:
- Stops the customer from reading card details.
- Explains payments only happen through the secure link.
- Offers the payment link instead.

## 8. "Am I talking to a real person?"
Scenario: After the greeting, you ask whether this is a recording or a real person.
Expected behaviours:
- Says it's an automated assistant calling for the merchant.
- Carries on normally if the customer is fine with that.
