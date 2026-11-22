"""Tests for recurring bills (phase 4) and monthly budgets (phase 5)."""
from __future__ import annotations

from datetime import date

from conftest import load_ledger

from core import analytics, db
from core.analytics import Period


def test_recurring_bills_are_added_once_per_month(ledger):
    conn, ids = ledger
    template_id = db.save_template_from_transaction(conn, ids["t6"])        # the $3,000 rent split 3 ways, on the 3rd
    template = db.list_templates(conn)[0]
    assert (template["description"], template["day_of_month"], template["total_cents"], template["split_method"]) == (
        "Rent", 3, 300000, "equal")

    assert db.add_recurring(conn, 2026, 2) == (0, 1)                        # February already has this rent
    assert db.add_recurring(conn, 2026, 4) == (1, 0)
    assert db.add_recurring(conn, 2026, 4) == (0, 1)                        # pressing twice adds nothing
    april = db.last_transaction(conn, "Rent")
    assert april["date"] == "2026-04-03" and april["total_cents"] == 300000 and len(april["shares"]) == 3

    db.delete_template(conn, template_id)
    assert db.add_recurring(conn, 2026, 5) == (0, 0)


def test_recurring_day_is_moved_back_in_short_months(conn):
    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    tx_id = db.save_transaction(conn, {
        "date": "2026-01-31", "description": "Month-end bill", "direction": "debit",
        "subcategory_id": db.list_subcategories(conn)[2]["id"], "payment_mode_id": db.list_payment_modes(conn)[0]["id"],
        "paid_by_person_id": people["Mine"], "split_method": "only_me", "notes": ""}, {people["Mine"]: 5000})
    db.save_template_from_transaction(conn, tx_id)
    db.add_recurring(conn, 2026, 2)
    assert db.last_transaction(conn, "Month-end bill")["date"] == "2026-02-28"


def test_budget_table_flags_overspending(ledger):
    conn, _ = ledger
    categories = {c["name"]: c["id"] for c in db.list_categories(conn)}
    db.set_budget(conn, categories["Home"], 30000)           # $300 a month
    db.set_budget(conn, categories["Personal"], 10000)       # $100 a month
    db.set_budget(conn, categories["Auto"], 5000)
    db.set_budget(conn, categories["Auto"], None)            # removed again
    budgets = {b["category"]: b["monthly_cents"] for b in db.list_budgets(conn)}
    assert budgets == {"Home": 30000, "Personal": 10000}

    _, shares = analytics.load_frames(conn)
    period = Period(years=(2026,), months=(1, 2, 3))
    table = analytics.budget_table(period.apply(shares), budgets, period.month_count(today=date(2026, 12, 1)),
                                   analytics.ME_VIEW).set_index("category")
    # Home: budget 3 x $300 = $900, I spent $1,067.00 -> over. Personal: 3 x $100, spent $45 -> fine.
    assert table.loc["Home", ["budget", "spent", "remaining"]].tolist() == [90000, 106700, -16700] and table.loc["Home", "over"]
    assert table.loc["Personal", ["budget", "spent", "remaining"]].tolist() == [30000, 4500, 25500] and not table.loc["Personal", "over"]
    assert analytics.budget_table(shares, {}, 3).empty


def test_month_count():
    later = date(2027, 6, 1)
    assert Period(years=(2026,)).month_count(today=later) == 12
    assert Period(years=(2025, 2026), months=(1, 3)).month_count(today=later) == 4
    assert Period(start=date(2025, 11, 15), end=date(2026, 1, 10)).month_count(today=later) == 3
    # Months that have not started yet do not count (their budget is not available to spend).
    assert Period(years=(2026,)).month_count(today=date(2026, 11, 16)) == 11
    assert Period(start=date(2026, 10, 1), end=date(2026, 12, 31)).month_count(today=date(2026, 11, 16)) == 2
    assert Period(years=(2027,)).month_count(today=date(2026, 11, 16)) == 0


def test_old_json_exports_without_the_new_tables_still_import(conn):
    from core import export
    load_ledger(conn)
    dump = export.export_json(conn)
    for table in db.OPTIONAL_TABLES:
        del dump["tables"][table]                             # what a schema-1 export looked like
    dump["schema_version"] = 1
    assert export.json_preview(dump)["counts"]["budgets"] == 0
    export.import_json(conn, dump)
    assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 11


def test_recurring_plan_and_edited_items(ledger):
    conn, ids = ledger
    template_id = db.save_template_from_transaction(conn, ids["t6"])        # $3,000 rent split 3 ways (equal)
    plan = db.recurring_plan(conn, 2026, 2)
    assert [(p["template_id"], p["already_entered"]) for p in plan] == [(template_id, True)]
    plan = db.recurring_plan(conn, 2026, 4)
    assert plan[0]["date"].isoformat() == "2026-04-03" and not plan[0]["already_entered"]
    # This month the rent is $3,100 and on the 5th; the equal split is redone among the same people.
    added = db.add_recurring_items(conn, [{"template_id": template_id, "date": plan[0]["date"].replace(day=5),
                                           "total_cents": 310000, "payment_mode_id": plan[0]["payment_mode_id"],
                                           "description": "Rent April", "notes": ""}])
    assert added == 1
    tx = db.last_transaction(conn)
    assert (tx["date"], tx["description"], tx["total_cents"]) == ("2026-04-05", "Rent April", 310000)
    assert sorted(tx["shares"].values()) == [103333, 103333, 103334]
    assert db.list_templates(conn)[0]["total_cents"] == 300000                 # the default is unchanged
    db.add_recurring_items(conn, [{"template_id": template_id, "date": "2026-05-07", "total_cents": 320000,
                                   "payment_mode_id": plan[0]["payment_mode_id"]}], save_as_default=True)
    template = db.list_templates(conn)[0]
    assert (template["total_cents"], template["day_of_month"]) == (320000, 7)


def test_scale_shares_keeps_proportions():
    from core import money
    assert money.scale_shares({"a": 5000, "b": 5000}, 10001, "equal", payer="b", sort_order=["a", "b"]) == {"a": 5000, "b": 5001}
    assert money.scale_shares({"a": 7500, "b": 2500}, 20000, "percent") == {"a": 15000, "b": 5000}
    scaled = money.scale_shares({"a": 3000, "b": 1000, "c": 1}, 8003, "exact", payer="a", sort_order=["a", "b", "c"])
    assert sum(scaled.values()) == 8003 and scaled["a"] > scaled["b"] > scaled["c"]
    assert money.scale_shares({"me": 4500}, 4800, "only_me") == {"me": 4800}
