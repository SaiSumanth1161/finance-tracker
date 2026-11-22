"""Tests for core/importer.py, using a made-up workbook with the same headers as the old spreadsheet.

No real data is used anywhere: every row below is invented.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest

from core import analytics, db, importer

TX_COLUMNS = ["Bill", "Date", "Credit Total", "Debit Total", "Mine", "Roommate 1", "Roommate 2", "Jordan", "Taylor", "Casey",
              "Others", "Total again", "Sub Category", "Category", "Category Type", "Income/Expense Total",
              "Income/Expense Mine", "Mode"]
SAVINGS_COLUMNS = ["Item", "Date", "Amount", "Sub Category Type2", "Category Type2", "Category Type"]


def tx_row(bill, day, credit, debit, shares, sub, category, kind, mode):
    row = dict.fromkeys(TX_COLUMNS)
    row.update({"Bill": bill, "Date": day, "Credit Total": credit, "Debit Total": debit, "Sub Category": sub,
                "Category": category, "Category Type": kind, "Mode": mode, "Total again": credit or debit,
                "Income/Expense Total": (credit or 0) - (debit or 0), **shares})
    return row


@pytest.fixture
def workbook() -> bytes:
    transactions = pd.DataFrame([
        tx_row("Fake paycheck", date(2026, 1, 5), 4000, None, {"Mine": 4000}, "salary", "Income", "Income", "checking"),
        tx_row("Fake rent", date(2026, 1, 6), None, 2700, {"Mine": 900, "Roommate 1": 900, "Roommate 2": 900}, "rent", "Home", "Expense", "Zelle"),
        tx_row("Fake dinner", date(2026, 1, 9), None, 100.01, {"Mine": 33.34, "Jordan": 33.34, "Taylor": 33.33}, "outside food", "Personal", "Expense", "visa"),
        tx_row("Fake gym", date(2026, 1, 12), None, 45, {}, "gym", "Health", "Expense", "discover"),          # new sub, category, mode; no shares
        tx_row("Fake refund", date(2026, 1, 15), 20, None, {"Mine": 10, "Roommate 1": 10}, "groceries", "Home", "expense", "mastercard"),
        tx_row("Broken date", "not a date", None, 10, {"Mine": 10}, "rent", "Home", "Expense", "visa"),
        tx_row("No subcategory", date(2026, 1, 20), None, 10, {"Mine": 10}, None, "Home", "Expense", "visa"),
    ], columns=TX_COLUMNS)
    savings = pd.DataFrame([
        ["Fake stocks buy", date(2026, 1, 16), 500, "stocks", "Stocks", "Savings"],
        ["Fake HYSA out", date(2026, 2, 10), -1000, "high-yield savings", "HYSA", "Savings"],
        ["Fake crypto", date(2026, 2, 12), 50, "bitcoin", "Crypto", "Savings"],
    ], columns=SAVINGS_COLUMNS)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"Category": ["Home"], "subcategory": ["rent"]}).to_excel(writer, sheet_name="Category", index=False)
        transactions.to_excel(writer, sheet_name="Transaction", index=False)
        savings.to_excel(writer, sheet_name="Savings", index=False)
    return buffer.getvalue()


def _tx_rows(conn, workbook):
    frame = importer.read_sheet(workbook, "Transaction")
    mapping = importer.guess_mapping(list(frame.columns), importer.TRANSACTIONS, [p["name"] for p in db.list_people(conn)])
    return importer.build_rows(frame, mapping, importer.TRANSACTIONS, "Mine"), mapping


def test_mapping_is_guessed_from_the_headers(conn, workbook):
    assert importer.sheet_names(workbook) == ["Category", "Transaction", "Savings"]
    (_, _), mapping = _tx_rows(conn, workbook)
    assert (mapping["description"], mapping["date"], mapping["credit"], mapping["debit"]) == ("Bill", "Date", "Credit Total", "Debit Total")
    assert (mapping["subcategory"], mapping["category"], mapping["category_type"], mapping["mode"]) == (
        "Sub Category", "Category", "Category Type", "Mode")
    assert list(mapping["people"]) == ["Mine", "Roommate 1", "Roommate 2", "Jordan", "Taylor", "Casey", "Others"]   # no total columns
    savings = importer.guess_mapping(SAVINGS_COLUMNS, importer.SAVINGS, [])
    assert savings == {"description": "Item", "date": "Date", "amount": "Amount",
                       "subcategory": "Sub Category Type2", "category": "Category Type2"}


def test_rows_and_problems(conn, workbook):
    (rows, problems), _ = _tx_rows(conn, workbook)
    assert [r["description"] for r in rows] == ["Fake paycheck", "Fake rent", "Fake dinner", "Fake gym", "Fake refund"]
    paycheck, rent, dinner, gym, refund = rows
    assert (paycheck["direction"], paycheck["total_cents"], paycheck["split_method"]) == ("credit", 400000, "only_me")
    assert rent["shares"] == {"Mine": 90000, "Roommate 1": 90000, "Roommate 2": 90000} and rent["split_method"] == "exact"
    assert dinner["total_cents"] == 10001 and dinner["shares"]["Taylor"] == 3333
    assert gym["shares"] == {"Mine": 4500}                         # no shares in the sheet -> only me
    assert (refund["direction"], refund["category_type"]) == ("credit", "Expense")   # "expense" is normalised
    # Problems name the Excel row and the column, never the cell content.
    assert problems == ["Row 7: column 'Date' is not a date. Row skipped.",
                        "Row 8: column 'Sub Category' is empty. Row skipped."]
    assert not any("Broken date" in p or "not a date." not in p and "No subcategory" in p for p in problems)


def test_review_and_apply(conn, workbook):
    (rows, _), _ = _tx_rows(conn, workbook)
    summary = importer.review(conn, rows)
    assert summary["new_people"] == ["Jordan", "Taylor"] and summary["new_modes"] == ["discover"]
    assert [u["subcategory"] for u in summary["unknown_subcategories"]] == ["gym"]
    assert summary["duplicates"] == 0 and summary["bad_types"] == []
    assert summary["signed_total_by_type"] == {"Income": 400000, "Expense": -270000 - 10001 - 4500 + 2000}

    result = importer.apply(conn, rows)
    assert result == {"imported": 5, "skipped_duplicates": 0,
                      "created": {"people": 2, "modes": 1, "categories": 1, "subcategories": 1}}
    _, shares = analytics.load_frames(conn)
    numbers = analytics.kpis(shares, analytics.HOUSEHOLD_VIEW)
    assert (numbers["income"], numbers["expense"], numbers["my_share"]) == (400000, -282501, -90000 - 3334 - 4500 + 1000)
    gym = conn.execute("SELECT category, category_type FROM v_transactions WHERE subcategory = 'gym'").fetchone()
    assert (gym["category"], gym["category_type"]) == ("Health", "Expense")

    # Importing the same sheet again adds nothing.
    again = importer.apply(conn, rows)
    assert (again["imported"], again["skipped_duplicates"]) == (0, 5)


def test_unknown_subcategory_can_be_mapped_to_an_existing_one(conn, workbook):
    (rows, _), _ = _tx_rows(conn, workbook)
    target = next(s["id"] for s in db.list_subcategories(conn) if s["name"] == "misc-expense")
    result = importer.apply(conn, rows, subcategory_map={"GYM": target})
    assert result["created"]["subcategories"] == 0 and result["created"]["categories"] == 0
    assert conn.execute("SELECT subcategory FROM v_transactions WHERE description = 'Fake gym'").fetchone()["subcategory"] == "misc-expense"


def test_savings_sheet_and_undo(conn, workbook):
    frame = importer.read_sheet(workbook, "Savings")
    mapping = importer.guess_mapping(list(frame.columns), importer.SAVINGS, [])
    rows, problems = importer.build_rows(frame, mapping, importer.SAVINGS, "Mine")
    assert problems == []
    assert [(r["direction"], r["total_cents"], r["category_type"]) for r in rows] == [
        ("debit", 50000, "Savings"), ("credit", 100000, "Savings"), ("debit", 5000, "Savings")]
    with pytest.raises(ValueError):
        importer.apply(conn, rows)                                 # the sheet has no Mode: a default is needed
    conn.rollback()
    zelle = next(m["id"] for m in db.list_payment_modes(conn) if m["name"] == "Zelle")
    result = importer.apply(conn, rows, default_mode_id=zelle)
    assert result["imported"] == 3 and result["created"]["subcategories"] == 1      # bitcoin under a new Crypto category
    _, shares = analytics.load_frames(conn)
    numbers = analytics.kpis(shares)
    assert (numbers["saved"], numbers["withdrawn"], numbers["net_saved"]) == (55000, 100000, -45000)
    flipped, _ = importer.build_rows(frame, mapping, importer.SAVINGS, "Mine", positive_is_deposit=False)
    assert flipped[0]["direction"] == "credit"

    assert importer.last_import_count(conn) == 3
    assert importer.undo_last(conn) == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 0


# ------------------------------------------------------------- bank statement

BANK_CSV = b"""Transaction Date,Description,Amount
01/03/2026,FAKE COSTCO WHSE #123,-84.20
01/04/2026,FAKE EMPLOYER PAYROLL,2500.00
01/05/2026,FAKE COFFEE BAR,-4.75
not-a-date,FAKE BROKEN,-1.00
"""


def _bank_rows(conn, **options):
    frame = importer.read_csv(BANK_CSV)
    mapping = importer.guess_mapping(list(frame.columns), importer.BANK, [])
    assert mapping == {"description": "Description", "date": "Transaction Date", "amount": "Amount", "debit": None, "credit": None}
    rules = [{"pattern": "costco", "subcategory": "groceries"}, {"pattern": "PAYROLL", "subcategory": "salary"}]
    return importer.build_rows(frame, mapping, importer.BANK, "Mine", rules=rules,
                               subcategories=db.list_subcategories(conn), default_subcategory="misc-expense", **options)


def test_bank_csv_uses_rules_and_skips_duplicates(conn):
    rows, problems = _bank_rows(conn)
    assert problems == ["Row 5: column 'Transaction Date' is not a date. Row skipped."]
    assert [(r["subcategory"], r["direction"], r["total_cents"], r["matched"]) for r in rows] == [
        ("groceries", "debit", 8420, True), ("salary", "credit", 250000, True), ("misc-expense", "debit", 475, False)]
    assert all(r["split_method"] == "only_me" and r["shares"] == {"Mine": r["total_cents"]} for r in rows)
    assert importer.review(conn, rows)["unmatched"] == 1

    checking = next(m["id"] for m in db.list_payment_modes(conn) if m["name"] == "checking")
    first = importer.apply(conn, rows, default_mode_id=checking)
    second = importer.apply(conn, rows, default_mode_id=checking)              # the same statement again
    assert (first["imported"], second["imported"], second["skipped_duplicates"]) == (3, 0, 3)
    assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 3
    assert first["created"] == {"people": 0, "modes": 0, "categories": 0, "subcategories": 0}

    flipped, _ = _bank_rows(conn, negative_is_out=False)
    assert flipped[0]["direction"] == "credit"


def test_bank_file_with_debit_and_credit_columns(conn):
    frame = pd.DataFrame({"Date": ["2026-01-03", "2026-01-04"], "Details": ["FAKE SHOP", "FAKE REFUND"],
                          "Debit": ["12.50", ""], "Credit": ["", "3.00"]})
    mapping = importer.guess_mapping(list(frame.columns), importer.BANK, [])
    rows, problems = importer.build_rows(frame, mapping, importer.BANK, "Mine", subcategories=db.list_subcategories(conn),
                                         default_subcategory="misc-expense")
    assert problems == [] and [(r["direction"], r["total_cents"]) for r in rows] == [("debit", 1250), ("credit", 300)]


def test_rules_are_stored_in_order(conn):
    subs = {s["name"]: s["id"] for s in db.list_subcategories(conn)}
    db.replace_rules(conn, [("costco", subs["groceries"]), ("  ", subs["rent"]), ("netflix", subs["subscriptions"])])
    assert [(r["pattern"], r["subcategory"]) for r in db.list_rules(conn)] == [("costco", "groceries"), ("netflix", "subscriptions")]
    assert importer.match_rule("FAKE NETFLIX.COM", db.list_rules(conn)) == "subscriptions"
    assert importer.match_rule("something else", db.list_rules(conn)) is None


# ------------------------------------------------- header detection and categories

def _offset_workbook() -> bytes:
    """A made-up workbook whose tables do NOT start on row 1 or in column A (like a hand-made sheet)."""
    from openpyxl import Workbook

    book = Workbook()
    savings = book.active
    savings.title = "Savings"
    savings["B1"] = "My savings log"                               # a title row, then an empty row
    for column, name in enumerate(SAVINGS_COLUMNS, start=2):       # header on row 3, starting in column B
        savings.cell(row=3, column=column, value=name)
    savings.append([None, "Fake stocks buy", date(2026, 1, 16), 500, "stocks", "Stocks", "Savings"])
    savings.append([None, "Fake broken", "nope", 20, "stocks", "Stocks", "Savings"])

    category = book.create_sheet("Category")
    category.append([])                                            # empty first row
    category.append(["Category", "subcategory", "Category Type", None, "Category Type 2", "subcategory type 2", "Category Type 3"])
    category.append(["Home", "rent", "Expense", None, "Stocks", "stocks", "Savings"])
    category.append([None, "pet food", None, None, "Crypto", "bitcoin", "Savings"])      # blank category = same as above
    category.append(["Health", "gym", "expense", None, "Crypto", "ethereum", None])
    category.append(["Auto", "rent", "Expense", None, None, None, None])                  # "rent" already lives under Home
    category.append(["Income", "bonus", "Expense", None, None, None, None])               # Income is not an Expense category
    category.append(["Gifts", None, "Charity", None, None, None, None])                   # unknown type
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_header_row_is_found_when_the_table_starts_lower(conn):
    data = _offset_workbook()
    raw = importer.read_raw(data, "Savings")
    assert importer.detect_header_row(raw) == 3
    frame = importer.read_sheet(data, "Savings")                    # finds the header by itself
    assert list(frame.columns) == SAVINGS_COLUMNS                   # the empty column A is dropped
    assert importer.frame_from_raw(raw, 1).columns.tolist() == ["My savings log"]   # row 1 is only a title
    mapping = importer.guess_mapping(list(frame.columns), importer.SAVINGS, [])
    rows, problems = importer.build_rows(frame, mapping, importer.SAVINGS, "Mine")
    assert [(r["row"], r["total_cents"]) for r in rows] == [(4, 50000)]             # Excel row numbers stay right
    assert problems == ["Row 5: column 'Date' is not a date. Row skipped."]
    assert importer.detect_header_row(importer.read_raw(data, "Category")) == 2
    assert importer.detect_header_row(pd.DataFrame([[1, 2], [3, 4]], dtype=object)) is None


def test_header_detection_on_the_plain_workbook(workbook):
    assert importer.detect_header_row(importer.read_raw(workbook, "Transaction")) == 1
    assert importer.detect_header_row(importer.read_raw(workbook, "Savings")) == 1


def test_categories_sheet_with_two_tables(conn):
    frame = importer.read_sheet(_offset_workbook(), "Category")
    mapping = importer.guess_mapping(list(frame.columns), importer.CATEGORIES, [])
    assert mapping == {"category": "Category", "subcategory": "subcategory", "category_type": "Category Type",
                       "category2": "Category Type 2", "subcategory2": "subcategory type 2", "category_type2": "Category Type 3"}
    items, problems = importer.build_categories(frame, mapping)
    assert problems == []
    summary = importer.review_categories(conn, items)
    status = {(i["category"], i["subcategory"]): i["status"] for i in summary["items"]}
    assert status == {
        ("Home", "rent"): "already there",
        ("Home", "pet food"): "new",                       # blank category cell = the one above
        ("Health", "gym"): "new",                          # "expense" is read as Expense
        ("Auto", "rent"): "exists under Home",
        ("Income", "bonus"): "category is Income",
        ("Gifts", ""): "unknown type",
        ("Stocks", "stocks"): "already there",
        ("Crypto", "bitcoin"): "new",
        ("Crypto", "ethereum"): "new",                     # no type in the sheet: table 2 defaults to Savings
    }
    assert (summary["new_categories"], summary["new_subcategories"], summary["already_there"], summary["conflicts"]) == (2, 4, 2, 3)

    before = len(db.list_subcategories(conn))
    assert importer.apply_categories(conn, items) == {"categories": 2, "subcategories": 4}
    assert importer.apply_categories(conn, items) == {"categories": 0, "subcategories": 0}    # safe to repeat
    subs = {s["name"]: (s["category"], s["category_type"]) for s in db.list_subcategories(conn)}
    assert len(subs) == before + 4
    assert subs["pet food"] == ("Home", "Expense") and subs["gym"] == ("Health", "Expense")
    assert subs["bitcoin"] == ("Crypto", "Savings") and subs["ethereum"] == ("Crypto", "Savings")
    assert subs["rent"] == ("Home", "Expense")             # untouched

    strict, strict_problems = importer.build_categories(frame, mapping, fill_down=False)
    assert strict_problems == ["Row 4: column 'Category' is empty. Row skipped."]
    assert ("Home", "pet food") not in {(i["category"], i["subcategory"]) for i in strict}


def test_transaction_rows_without_a_mode_use_the_default(conn, workbook):
    (rows, _), _ = _tx_rows(conn, workbook)
    rows[0]["mode"] = ""                                         # an empty Mode cell
    assert importer.review(conn, rows)["no_mode"] == 1
    try:
        importer.apply(conn, rows)
        raise AssertionError("expected an error without a default mode")
    except ValueError:
        conn.rollback()
    cash = next(m["id"] for m in db.list_payment_modes(conn) if m["name"] == "Cash")
    assert importer.apply(conn, rows, default_mode_id=cash)["imported"] == 5
    assert conn.execute("SELECT payment_mode FROM v_transactions WHERE description = 'Fake paycheck'").fetchone()[0] == "Cash"


def test_coverage_accounts_for_every_excel_row(conn, workbook):
    frame = importer.read_sheet(workbook, "Transaction")
    (rows, problems), _ = _tx_rows(conn, workbook)
    cover = importer.coverage(frame, rows, problems)
    assert (cover["first"], cover["last"]) == (2, 8)                # 7 data lines: 5 ready, 2 problems
    assert [cover["status"][n] for n in range(2, 9)] == ["ready"] * 5 + ["skipped"] * 2
    found = importer.rows_in_range(cover, 2, 10)
    assert found["counts"] == {"ready": 5, "skipped": 2, "blank": 0, "outside": 2}
    assert found["not_ready"] == [(7, "skipped (problem)"), (8, "skipped (problem)"),
                                  (9, "outside the table"), (10, "outside the table")]
    # A completely empty line inside the table is reported as blank, not silently lost.
    gap = frame.copy()
    gap.loc[2, :] = None
    cover = importer.coverage(gap, [r for r in rows if r["row"] != 4], problems)
    assert cover["status"][4] == "blank"


def test_total_mismatch_is_reported_by_row(conn):
    rows = [
        {"row": 5, "direction": "debit", "total_cents": 1000, "stated_cents": 1000, "category_type": "Expense"},
        {"row": 6, "direction": "debit", "total_cents": 1009, "stated_cents": 1000, "category_type": "Expense"},
        {"row": 7, "direction": "debit", "total_cents": 500, "stated_cents": None, "category_type": "Expense"},
    ]
    found = importer.total_mismatches(rows)
    assert [(g["row"], g["gap_cents"]) for g in found] == [(6, 9)]
