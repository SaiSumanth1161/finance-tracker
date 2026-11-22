"""Shared test helpers.

- `cases` loads tests/cases.json (the golden cases a future JavaScript port can reuse).
- `conn` is a fresh in-memory database with the real schema and seed data.
- `ledger_conn` is that database filled with the small "ledger" from cases.json.

Tests never touch ./data/finance.db.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `import core` works when pytest is run from anywhere

from core import db  # noqa: E402

CASES = json.loads((ROOT / "tests" / "cases.json").read_text(encoding="utf-8"))

# cases.json uses short keys for people; the seed data uses these names.
PERSON_NAMES = {"me": "Mine", "roommate1": "Roommate 1", "roommate2": "Roommate 2", "others": "Others"}


@pytest.fixture(scope="session")
def cases() -> dict:
    return CASES


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def load_ledger(connection) -> dict:
    """Insert the cases.json ledger. Returns {case id ("t1"...): database id}."""
    people = {p["name"]: p["id"] for p in db.list_people(connection)}
    modes = {m["name"]: m["id"] for m in db.list_payment_modes(connection)}
    subs = {s["name"]: s["id"] for s in db.list_subcategories(connection)}
    ids = {}
    for tx in CASES["ledger"]["transactions"]:
        row = {
            "date": tx["date"], "description": tx["description"], "direction": tx["direction"],
            "subcategory_id": subs[tx["subcategory"]], "payment_mode_id": modes[tx["payment_mode"]],
            "paid_by_person_id": people[PERSON_NAMES[tx["payer"]]], "split_method": tx["split_method"], "notes": "",
        }
        shares = {people[PERSON_NAMES[key]]: cents for key, cents in tx["shares"].items()}
        ids[tx["id"]] = db.save_transaction(connection, row, shares)
    connection.commit()
    return ids


@pytest.fixture
def ledger(conn):
    """(connection, {case id: database id}) with the ledger loaded."""
    return conn, load_ledger(conn)
