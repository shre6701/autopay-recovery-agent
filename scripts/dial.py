"""Places calls through Sarvam for everyone the decision engine marks as "call".

Without --live it only prints the request it would send. Live calls always go to
DEMO_PHONE_NUMBER, never to the numbers in the fictional records.

    python scripts/dial.py --now 11:00                # dry run, as if it were 11 AM
    python scripts/dial.py --only CUST-006 --live     # one real call
    python scripts/dial.py --live --limit 2           # the 2 highest-value customers
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.campaign import dial, parse_now  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Store  # noqa: E402
from app.sarvam_client import SarvamError  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="really place the call(s)")
    ap.add_argument("--only", nargs="*", help="customer IDs to call, e.g. CUST-006")
    ap.add_argument("--limit", type=int, help="max number of calls")
    ap.add_argument("--now", help="override current time for the eligibility check (dry run only)")
    args = ap.parse_args()

    s = get_settings()
    if args.live and args.now:
        sys.exit("--now is only for dry runs. Live calls always use the real time so calling-hour rules hold.")
    store = Store(s)
    store.load_seed()
    try:
        results = dial(store, s, parse_now(args.now, s), only=args.only, live=args.live, limit=args.limit)
    except SarvamError as e:
        sys.exit(f"Stopped: {e}")
    if not results:
        print("Nobody is eligible for a call right now (check calling hours with scripts/plan.py).")
    for r in results:
        if r["mode"] == "dry_run":
            print(f"\n--- DRY RUN {r['customer_id']} (would POST to Sarvam) ---")
            print(json.dumps(r["request"], indent=2, ensure_ascii=False))
        else:
            print(f"Calling for {r['customer_id']}: attempt_id={r['attempt_id']}")
    if results and not args.live:
        print("\nNothing was dialled. Add --live to place real calls.")


if __name__ == "__main__":
    main()
