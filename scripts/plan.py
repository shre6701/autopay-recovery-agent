"""Prints what the decision engine would do for each customer. No calls are made.

    python scripts/plan.py
    python scripts/plan.py --now 11:00                 # pretend it is 11 AM IST today
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.campaign import current_plan, parse_now  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", help="HH:MM today, or an ISO datetime, in the calling timezone")
    ap.add_argument("--reset", action="store_true", help="reload the fictional customers and clear all history")
    args = ap.parse_args()

    s = get_settings()
    store = Store(s)
    store.load_seed(reset=args.reset)
    now = parse_now(args.now, s)
    customers = {c.customer_id: c for c in store.customers()}

    print(f"\nPlan as of {now:%Y-%m-%d %H:%M} ({s.timezone}), calling window {s.call_window_start}-{s.call_window_end}\n")
    print(f"{'ID':<9} {'Name':<15} {'Due':>8}  {'Failure':<27} {'Action':<14} {'Score':>8}  Why")
    print("-" * 125)
    for d in current_plan(store, s, now):
        c = customers[d.customer_id]
        why = d.reason + (f" Next: {d.next_contact_at[:16]}" if d.next_contact_at else "")
        print(f"{c.customer_id:<9} {c.name:<15} {c.amount_due_inr:>8}  {c.failure_reason:<27} {d.action:<14} "
              f"{d.priority_score:>8}  {why}")
    print("\nScore = amount due x assumed recovery likelihood. Calls are placed highest score first.\n")


if __name__ == "__main__":
    main()
