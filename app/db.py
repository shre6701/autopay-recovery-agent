"""SQLite storage. Fine for a demo; production would use Postgres."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from .config import Settings
from .models import Customer

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'          -- open | recovered | cancelled | handed_off
);
CREATE TABLE IF NOT EXISTS call_attempts (
    attempt_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'dialling',    -- dialling | connected | no_answer | busy | failed
    duration_s REAL,
    interaction_id TEXT,
    failure_reason TEXT,
    final_variables TEXT,
    transcript TEXT,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payment_links (
    link_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    reference_id TEXT UNIQUE NOT NULL,
    short_url TEXT NOT NULL,
    amount_inr INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- created | paid | expired | cancelled
    mode TEXT NOT NULL,                         -- test | mock
    created_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    promise_date TEXT,
    notes TEXT,
    source TEXT NOT NULL,                       -- tool | webhook | razorpay
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    retry_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    customer_id TEXT,
    request TEXT,
    response TEXT,
    ok INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.settings.db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def load_seed(self, reset: bool = False) -> dict[str, Any]:
        """Load the fictional customers. Relative dates are resolved against today so the demo works any day."""
        seed = json.loads(Path(self.settings.customers_file).read_text())
        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        with self.conn() as c:
            if reset:
                for t in ("customers", "call_attempts", "payment_links", "outcomes", "retries", "escalations", "tool_log"):
                    c.execute(f"DELETE FROM {t}")
            for raw in seed["customers"]:
                cust = dict(raw)
                if "failed_days_ago" in cust:
                    cust["failed_at"] = (today - timedelta(days=cust.pop("failed_days_ago"))).isoformat()
                if "payday_in_days" in cust:
                    cust["payday_day"] = (today + timedelta(days=cust.pop("payday_in_days"))).day
                cust.setdefault("payday_day", None)
                c.execute("INSERT OR IGNORE INTO customers(customer_id, data) VALUES (?, ?)",
                          (cust["customer_id"], json.dumps(cust)))
        return seed["merchant"]

    def merchant(self) -> dict[str, Any]:
        return json.loads(Path(self.settings.customers_file).read_text())["merchant"]

    def customers(self) -> list[Customer]:
        with self.conn() as c:
            rows = c.execute("SELECT data FROM customers ORDER BY customer_id").fetchall()
        return [Customer.from_dict(json.loads(r["data"])) for r in rows]

    def customer(self, customer_id: str) -> Customer | None:
        with self.conn() as c:
            row = c.execute("SELECT data FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
        return Customer.from_dict(json.loads(row["data"])) if row else None

    def update_customer(self, customer: Customer) -> None:
        with self.conn() as c:
            c.execute("UPDATE customers SET data = ? WHERE customer_id = ?",
                      (json.dumps(customer.to_dict()), customer.customer_id))

    def set_status(self, customer_id: str, status: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE customers SET status = ? WHERE customer_id = ?", (status, customer_id))

    def statuses(self) -> dict[str, str]:
        with self.conn() as c:
            return {r["customer_id"]: r["status"] for r in c.execute("SELECT customer_id, status FROM customers")}

    def add_attempt(self, attempt_id: str, customer_id: str, simulated: bool = False) -> None:
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO call_attempts(attempt_id, customer_id, created_at, simulated) VALUES (?,?,?,?)",
                      (attempt_id, customer_id, now_iso(), int(simulated)))

    def update_attempt(self, attempt_id: str, customer_id: str, **fields: Any) -> None:
        self.add_attempt(attempt_id, customer_id, simulated=bool(fields.pop("simulated", False)))
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE call_attempts SET {cols} WHERE attempt_id = ?", (*fields.values(), attempt_id))

    def attempts(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM call_attempts ORDER BY created_at DESC")]

    def link_by_reference(self, reference_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM payment_links WHERE reference_id = ?", (reference_id,)).fetchone()
        return dict(row) if row else None

    def save_link(self, link: dict[str, Any], customer_id: str, mode: str) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO payment_links(link_id, customer_id, reference_id, short_url, amount_inr, status, mode, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (link["id"], customer_id, link["reference_id"], link["short_url"], link["amount"] // 100,
                 link.get("status", "created"), mode, now_iso()))

    def mark_link_paid(self, link_id: str) -> str | None:
        with self.conn() as c:
            row = c.execute("SELECT customer_id, status FROM payment_links WHERE link_id = ?", (link_id,)).fetchone()
            if not row:
                return None
            if row["status"] != "paid":
                c.execute("UPDATE payment_links SET status = 'paid', paid_at = ? WHERE link_id = ?", (now_iso(), link_id))
            return row["customer_id"]

    def links(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM payment_links ORDER BY created_at DESC")]

    def add_outcome(self, customer_id: str, outcome: str, source: str, promise_date: str | None = None, notes: str | None = None) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO outcomes(customer_id, outcome, promise_date, notes, source, created_at) VALUES (?,?,?,?,?,?)",
                      (customer_id, outcome, promise_date, notes, source, now_iso()))

    def outcomes(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outcomes ORDER BY id DESC")]

    def add_retry(self, customer_id: str, retry_date: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO retries(customer_id, retry_date, created_at) VALUES (?,?,?)", (customer_id, retry_date, now_iso()))

    def retries(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM retries ORDER BY id DESC")]

    def add_escalation(self, customer_id: str, reason: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO escalations(customer_id, reason, created_at) VALUES (?,?,?)", (customer_id, reason, now_iso()))

    def escalations(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM escalations ORDER BY id DESC")]

    def log_tool(self, tool: str, customer_id: str | None, request: Any, response: Any, ok: bool) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO tool_log(tool, customer_id, request, response, ok, created_at) VALUES (?,?,?,?,?,?)",
                      (tool, customer_id, json.dumps(request, default=str), json.dumps(response, default=str), int(ok), now_iso()))

    def tool_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tool_log ORDER BY id DESC LIMIT ?", (limit,))]
