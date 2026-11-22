"""Tests for core/db.py and core/demo.py: schema, seed data, SQL views, writes and demo data."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import CASES, PERSON_NAMES

from core import db, demo, money


def test_seed_matches_spec(conn):
    people = db.list_people(conn)
    assert [p["name"] for p in people] == ["Mine", "Roommate 1", "Roommate 2", "Others"]
    assert [p["is_me"] for p in people] == [1, 0, 0, 0]
    assert [m["name"] for m in db.list_payment_modes(conn)] == ["visa", "mastercard", "debit card", "checking", "Zelle", "Cash"]
    assert [t["name"] for t in db.list_category_types(conn)] == ["Income", "Expense", "Savings", "Not Reported"]
    types = {c["name"]: c["category_type"] for c in db.list_categories(conn)}
    assert types["Home"] == "Expense" and types["Stocks"] == "Savings" and types["Transfer"] == "Not Reported"
    assert len(db.list_subcategories(conn)) == 26
    assert db.get_meta(conn, "schema_version") == str(db.SCHEMA_VERSION)


def test_init_is_safe_to_repeat(conn):
    db.init_schema(conn)
    db.init_schema(conn)
    assert len(db.list_people(conn)) == 4  # not seeded twice


def test_every_table_has_timestamps_and_text_keys(conn):
    for table in db.TABLES:
        columns = {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "created_at" in columns and "updated_at" in columns, table
        if "id" in columns:
            assert columns["id"]["type"] == "TEXT", table


def test_views_match_golden_rows(ledger):
    conn, ids = ledger
    for expected in CASES["ledger"]["view_rows"]:
        row = dict(conn.execute("SELECT * FROM v_transactions WHERE id = ?", (ids[expected["id"]],)).fetchone())
        for key, value in expected.items():
            if key != "id":
                assert row[key] == value, (expected["id"], key)


def test_v_shares_has_one_row_per_person(ledger):
    conn, ids = ledger
    shares = db.rows(conn, "SELECT person, share_cents, share_signed_cents, category_type FROM v_shares "
                           "WHERE transaction_id = ? ORDER BY person_sort", (ids["t9"],))
    assert [(s["person"], s["share_cents"], s["share_signed_cents"]) for s in shares] == [
        ("Mine", 4000, -4000), ("Roommate 1", 3000, -3000), ("Others", 3000, -3000)]
    total_rows = conn.execute("SELECT COUNT(*) AS n FROM v_shares").fetchone()["n"]
    assert total_rows == sum(len(tx["shares"]) for tx in CASES["ledger"]["transactions"])


def test_total_is_always_the_sum_of_shares(ledger):
    conn, _ = ledger
    broken = conn.execute("""
        SELECT COUNT(*) AS n FROM transactions t
        WHERE t.total_cents != (SELECT SUM(share_cents) FROM transaction_shares WHERE transaction_id = t.id)
    """).fetchone()["n"]
    assert broken == 0


def test_update_and_delete_transaction(ledger):
    conn, ids = ledger
    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    tx = db.get_transaction(conn, ids["t2"])
    assert tx["total_cents"] == 9000 and len(tx["shares"]) == 3
    db.save_transaction(conn, tx, {people["Mine"]: 5000, people["Roommate 1"]: 5000}, tx_id=ids["t2"])
    updated = db.get_transaction(conn, ids["t2"])
    assert updated["total_cents"] == 10000 and set(updated["shares"]) == {people["Mine"], people["Roommate 1"]}
    db.delete_transaction(conn, ids["t2"])
    assert db.get_transaction(conn, ids["t2"]) is None
    left = conn.execute("SELECT COUNT(*) AS n FROM transaction_shares WHERE transaction_id = ?", (ids["t2"],)).fetchone()["n"]
    assert left == 0  # shares are deleted with the transaction


def test_bad_transactions_are_rejected(conn):
    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    tx = {"date": "2026-01-01", "description": "x", "direction": "debit",
          "subcategory_id": db.list_subcategories(conn)[0]["id"], "payment_mode_id": db.list_payment_modes(conn)[0]["id"],
          "paid_by_person_id": people["Mine"], "split_method": "equal", "notes": ""}
    with pytest.raises(money.MoneyError):
        db.save_transaction(conn, tx, {})
    with pytest.raises(money.MoneyError):
        db.save_transaction(conn, {**tx, "direction": "sideways"}, {people["Mine"]: 100})
    with pytest.raises(ValueError):
        db.save_transaction(conn, {**tx, "date": "not a date"}, {people["Mine"]: 100})


def test_last_transaction_by_description(ledger):
    conn, ids = ledger
    assert db.last_transaction(conn, "costco")["id"] == ids["t3"]     # case-insensitive, most recent entry
    assert db.last_transaction(conn)["id"] == ids["t11"]
    assert db.last_transaction(conn, "never used") is None


def test_balances_from_database(ledger):
    conn, _ = ledger
    name_of = {p["id"]: p["name"] for p in db.list_people(conn)}
    nets = {name_of[pid]: cents for pid, cents in money.balances(*db.balance_inputs(conn)).items()}
    # Roommate 1: 3000 − 300 + 100000 + 3000 + 3000 (the 2025 row) = 108700
    assert nets == {"Mine": 214400, "Roommate 1": -108700, "Roommate 2": -102700, "Others": -3000}
    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    db.add_settlement(conn, "2026-03-31", people["Roommate 1"], people["Mine"], 108700)
    nets = {name_of[pid]: cents for pid, cents in money.balances(*db.balance_inputs(conn)).items()}
    assert nets["Roommate 1"] == 0 and nets["Mine"] == 105700


def test_master_rows_in_use_cannot_be_deleted(ledger):
    conn, _ = ledger
    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    with pytest.raises(db.InUseError):
        db.delete_master_row(conn, "people", people["Roommate 1"])          # has shares
    with pytest.raises(db.InUseError):
        db.delete_master_row(conn, "people", people["Mine"])          # is me
    with pytest.raises(ValueError):
        db.save_master_row(conn, "people", {"name": "Mine", "active": 0, "sort_order": 1}, people["Mine"])
    # A brand-new person is unused, so it can be deleted again.
    new_id = db.save_master_row(conn, "people", {"name": "Ravi", "active": 1, "sort_order": 9})
    db.delete_master_row(conn, "people", new_id)
    assert "Ravi" not in [p["name"] for p in db.list_people(conn)]
    # Renames flow everywhere because rows point at ids, not names.
    db.save_master_row(conn, "people", {"name": "Anthony", "active": 1, "sort_order": 2}, people["Roommate 1"])
    assert conn.execute("SELECT COUNT(*) AS n FROM v_shares WHERE person = 'Anthony'").fetchone()["n"] > 0


def test_demo_data_load_and_clear(conn):
    added = demo.load_demo(conn, today=date(2026, 11, 16))
    assert 130 <= added <= 170                                         # "about 150"
    assert demo.load_demo(conn, today=date(2026, 11, 16)) == 0          # never loaded twice
    span = conn.execute("SELECT MIN(date) AS first, MAX(date) AS last FROM transactions").fetchone()
    assert span["first"] >= "2026-06-01" and span["last"] <= "2026-11-16"
    types = {r["category_type"] for r in conn.execute("SELECT DISTINCT category_type FROM v_transactions")}
    assert types == {"Income", "Expense", "Savings", "Not Reported"}
    methods = {r["split_method"] for r in conn.execute("SELECT DISTINCT split_method FROM transactions")}
    assert methods == set(money.SPLIT_METHODS)
    # A real (non-demo) row must survive "Clear demo data".
    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    keep = db.save_transaction(conn, {
        "date": "2026-11-01", "description": "real", "direction": "debit",
        "subcategory_id": db.list_subcategories(conn)[0]["id"], "payment_mode_id": db.list_payment_modes(conn)[0]["id"],
        "paid_by_person_id": people["Mine"], "split_method": "only_me", "notes": ""}, {people["Mine"]: 1234})
    assert demo.clear_demo(conn) == added
    assert [r["id"] for r in conn.execute("SELECT id FROM transactions")] == [keep]
    assert conn.execute("SELECT COUNT(*) AS n FROM settlements").fetchone()["n"] == 0


def test_person_names_cover_cases():
    assert set(PERSON_NAMES) == set(CASES["sort_order"])
