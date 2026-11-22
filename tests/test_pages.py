"""Smoke tests: every page runs headless without an exception, on empty data and on demo data.

Uses Streamlit's AppTest, so no browser and no server are needed.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import db, demo

ROOT = Path(__file__).resolve().parent.parent
PAGES = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "pages").glob("*.py"))


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """Point the app at a throw-away database (and backup folder) for one test."""
    path = tmp_path / "finance.db"
    monkeypatch.setenv("FINANCE_DB", str(path))
    monkeypatch.setenv("FINANCE_BACKUPS", str(tmp_path / "backups"))
    monkeypatch.chdir(ROOT)
    db.init_db(path)
    return path


def _run(page: str | None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    if page:
        at = at.switch_page(page).run()
    return at


@pytest.mark.parametrize("page", [None] + PAGES, ids=lambda p: p or "app.py")
@pytest.mark.parametrize("with_demo", [False, True], ids=["empty", "demo"])
def test_page_runs_without_errors(app_db, page, with_demo):
    if with_demo:
        with db.session(app_db) as conn:
            demo.load_demo(conn)
    at = _run(page)
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_quick_add_three_way_split_and_smart_defaults(app_db):
    """Phase 2 check: add a $90 bill split 3 ways, then see the next "Costco" pre-fill the same split."""
    with db.session(app_db) as conn:
        people = {p["name"]: p["id"] for p in db.list_people(conn)}
        groceries = next(s["id"] for s in db.list_subcategories(conn) if s["name"] == "groceries")
    at = _run(None)
    at.text_input(key="qa_desc").set_value("Costco").run()
    at.text_input(key="qa_amount").set_value("90").run()
    at.selectbox(key="qa_subcat").set_value(groceries).run()
    at.selectbox(key="qa_split").set_value("equal").run()
    at.checkbox(key=f"qa_in_{people['Others']}").uncheck().run()
    assert any("My share −$30.00" in m.value.replace("\\", "") for m in at.markdown)   # the preview line
    at.button(key="btn_save").click().run()
    assert not at.exception, [e.value for e in at.exception]

    with db.session(app_db) as conn:
        saved = db.last_transaction(conn)
    assert saved["description"] == "Costco" and saved["total_cents"] == 9000 and saved["split_method"] == "equal"
    assert saved["shares"] == {people["Mine"]: 3000, people["Roommate 1"]: 3000, people["Roommate 2"]: 3000}
    assert at.text_input(key="qa_desc").value == ""                                    # the form was reset

    # Typing the same description again pre-fills subcategory and split from last time.
    at.text_input(key="qa_desc").set_value("costco").run()
    assert at.selectbox(key="qa_split").value == "equal"
    assert at.selectbox(key="qa_subcat").value == groceries
    assert at.checkbox(key=f"qa_in_{people['Others']}").value is False

    # A bad amount is reported and nothing is saved.
    at.text_input(key="qa_amount").set_value("abc").run()
    at.button(key="btn_save").click().run()
    assert any("not a valid amount" in e.value for e in at.error)
    with db.session(app_db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 1


def test_duplicate_last_keeps_the_original_date(app_db):
    """Duplicate last copies the date too, so entering a past bill twice does not jump to today."""
    with db.session(app_db) as conn:
        me = db.me_id(conn)
        sub = next(s["id"] for s in db.list_subcategories(conn) if s["name"] == "groceries")
        mode = db.list_payment_modes(conn)[0]["id"]
        db.save_transaction(conn, {"date": date(2026, 3, 14), "description": "Old bill", "direction": "debit",
                                   "subcategory_id": sub, "payment_mode_id": mode, "paid_by_person_id": me,
                                   "split_method": "only_me", "notes": ""}, {me: 1234})
    at = _run(None)
    at.button(key="btn_duplicate").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.date_input(key="qa_date").value == date(2026, 3, 14)
    assert (at.text_input(key="qa_desc").value, at.text_input(key="qa_amount").value) == ("Old bill", "12.34")


def test_saved_view_is_pinned_and_survives_a_restart(app_db):
    """Phase 3 check: a view saved in Explore shows up on Insights, also in a brand-new session."""
    with db.session(app_db) as conn:
        demo.load_demo(conn)
    at = _run("pages/2_Explore.py")
    at.selectbox(key="ex_group1").set_value("Category").run()
    at.selectbox(key="ex_chart").set_value("Stacked bar").run()
    at.text_input(key="ex_name").set_value("Spend by category").run()
    at.button(key="ex_pin").click().run()
    assert not at.exception, [e.value for e in at.exception]
    with db.session(app_db) as conn:
        saved = db.list_saved_views(conn)
    assert [(v["name"], v["pinned"]) for v in saved] == [("Spend by category", 1)]

    fresh = _run("pages/1_Insights.py")                                   # a new session = after a restart
    assert not fresh.exception, [e.value for e in fresh.exception]
    assert any("Spend by category" in m.value for m in fresh.markdown)


@pytest.mark.parametrize("view", ["Me", "Roommate 1", "Household"])
def test_insights_for_every_view(app_db, view):
    """Charts and tables work for Me, a roommate and the Household."""
    with db.session(app_db) as conn:
        demo.load_demo(conn)
    at = _run("pages/1_Insights.py")
    at.selectbox(key="w_view").set_value(view).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(f"View: {view}." in c.value for c in at.caption)
    at.button(key="refresh").click().run()                                # the Refresh button reloads cleanly
    assert not at.exception, [e.value for e in at.exception]


def test_global_filters_persist_across_pages(app_db):
    with db.session(app_db) as conn:
        demo.load_demo(conn)
    at = _run(None)
    at.selectbox(key="w_view").set_value("Household").run()
    at.pills(key="w_months").set_value([1, 3]).run()                     # the Month slicer: Jan + Mar
    at = at.switch_page("pages/2_Explore.py").run()
    assert at.selectbox(key="w_view").value == "Household"
    assert at.session_state["f_months"] == [1, 3]
    assert any("Jan, Mar" in c.value and "Household" in c.value for c in at.sidebar.caption)

    # A quick preset sets the Year / Month pills; Clear filters removes everything.
    at.pills(key="w_preset").set_value("This month").run()
    today = date.today()
    assert (at.session_state["f_years"], at.session_state["f_months"]) == ([today.year], [today.month])
    at.pills(key="w_preset").set_value("Custom").run()
    assert not at.exception, [e.value for e in at.exception]
    clear = next(b for b in at.sidebar.button if b.label == "Clear filters")
    clear.click().run()
    assert (at.session_state["f_years"], at.session_state["f_months"], at.session_state["f_view"]) == ([], [], "Me")
    assert not at.exception, [e.value for e in at.exception]


def test_record_settlement_and_backup_buttons(app_db):
    with db.session(app_db) as conn:
        demo.load_demo(conn)
        before = len(db.list_settlements(conn))
        people = {p["name"]: p["id"] for p in db.list_people(conn)}
    at = _run("pages/3_Settle_Up.py")
    amount_key = f"su_amount_{people['Roommate 1']}_{people['Mine']}"
    assert float(at.text_input(key=amount_key).value) > 0                 # pre-filled with what Roommate 1 owes
    at.text_input(key=amount_key).set_value("25.50").run()
    at.button(key="su_save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    with db.session(app_db) as conn:
        settlements = db.list_settlements(conn)
    assert len(settlements) == before + 1
    assert max(s["created_at"] for s in settlements) and any(s["amount_cents"] == 2550 for s in settlements)

    at = at.switch_page("pages/5_Backup.py").run()
    at.button(key="backup_now").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Last backup" in s.value for s in at.success)


def test_setup_demo_buttons(app_db):
    at = _run("pages/4_Setup.py")
    at.button(key="demo_load").click().run()
    assert not at.exception, [e.value for e in at.exception]
    with db.session(app_db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] > 100
    at.button(key="demo_clear").click().run()
    with db.session(app_db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 0


def test_saving_tab_deposit_and_withdraw(app_db):
    """SPEC 7.1: the Saving tab stores Deposit as debit and Withdraw as credit, always only_me and paid by me."""
    with db.session(app_db) as conn:
        me = db.me_id(conn)
        subs = {s["name"]: s for s in db.list_subcategories(conn)}
    at = _run(None)
    where = at.selectbox(key="sv_where")
    assert set(where.options) == {"gold", "401k", "stocks", "high-yield savings"}          # Savings subcategories only
    where.set_value(subs["stocks"]["id"]).run()
    at.text_input(key="sv_amount").set_value("500").run()
    assert any("Saved +$500.00" in m.value.replace("\\", "") for m in at.markdown)
    at.button(key="btn_sv_save").click().run()
    assert not at.exception, [e.value for e in at.exception]

    at.selectbox(key="sv_where").set_value(subs["high-yield savings"]["id"]).run()
    at.radio(key="sv_action").set_value("credit").run()
    at.text_input(key="sv_amount").set_value("1,000").run()
    assert any("Saved −$1,000.00" in m.value.replace("\\", "") for m in at.markdown)
    at.button(key="btn_sv_another").click().run()

    with db.session(app_db) as conn:
        rows = db.rows(conn, "SELECT t.*, s.name AS sub FROM transactions t JOIN subcategories s ON s.id = t.subcategory_id ORDER BY t.created_at")
        shares = db.rows(conn, "SELECT person_id FROM transaction_shares")
    assert [(r["sub"], r["direction"], r["total_cents"], r["split_method"], r["paid_by_person_id"]) for r in rows] == [
        ("stocks", "debit", 50000, "only_me", me), ("high-yield savings", "credit", 100000, "only_me", me)]
    assert {s["person_id"] for s in shares} == {me}
    # The table shows Deposit / Withdraw for Savings rows.
    table = at.dataframe[0].value
    assert set(table["Dir"]) == {"Deposit", "Withdraw"}
    # Picking a Savings subcategory on the Bill / Income tab switches the direction labels too.
    at.selectbox(key="qa_subcat").set_value(subs["gold"]["id"]).run()
    assert at.radio(key="qa_direction").options == ["Deposit", "Withdraw"]


def test_import_page_with_a_made_up_workbook(app_db):
    """Phase 4: upload an invented workbook (same headers as the old sheet), import it, then undo."""
    import io

    import pandas as pd

    columns = ["Bill", "Date", "Credit Total", "Debit Total", "Mine", "Roommate 1", "Roommate 2", "Total again", "Sub Category",
               "Category", "Category Type", "Mode"]
    fake = pd.DataFrame([
        ["Fake rent", date(2026, 1, 6), None, 2700, 900, 900, 900, 2700, "rent", "Home", "Expense", "Zelle"],
        ["Fake paycheck", date(2026, 1, 5), 4000, None, 4000, None, None, 4000, "salary", "Income", "Income", "checking"],
    ], columns=columns)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        fake.to_excel(writer, sheet_name="Transaction", index=False)

    at = _run("pages/6_Import.py")
    assert not at.exception, [e.value for e in at.exception]
    at.file_uploader(key="imp_file").upload("fake.xlsx", buffer.getvalue()).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.metric[0].value == "2"                                       # rows ready
    sure = next(c for c in at.checkbox if c.key and c.key.startswith("imp_sure_"))
    sure.check().run()
    at.button(key="imp_go").click().run()
    assert not at.exception, [e.value for e in at.exception]
    with db.session(app_db) as conn:
        rows = db.rows(conn, "SELECT description, total_cents, direction FROM transactions ORDER BY date")
    assert rows == [{"description": "Fake paycheck", "total_cents": 400000, "direction": "credit"},
                    {"description": "Fake rent", "total_cents": 270000, "direction": "debit"}]
    at.button(key="imp_undo").click().run()
    with db.session(app_db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 0


def test_bank_csv_recurring_and_budget_flows(app_db):
    """Phases 4 and 5 through the pages: bank CSV with a rule, a recurring bill, and an over-budget flag."""
    with db.session(app_db) as conn:
        subs = {s["name"]: s["id"] for s in db.list_subcategories(conn)}
        categories = {c["name"]: c["id"] for c in db.list_categories(conn)}
        db.replace_rules(conn, [("costco", subs["groceries"])])
        db.set_budget(conn, categories["Home"], 5000)                        # $50 a month for Home
    today = date.today()
    csv = f"Date,Description,Amount\n{today:%m/%d/%Y},FAKE COSTCO #1,-84.20\n{today:%m/%d/%Y},FAKE CAFE,-4.75\n".encode()

    at = _run("pages/6_Import.py")
    at.file_uploader(key="imp_file").upload("statement.csv", csv).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [m.value for m in at.metric] == ["2", "0", "0", "1"]              # ready, problems, duplicates, unmatched
    next(c for c in at.checkbox if c.key and c.key.startswith("imp_sure_")).check().run()
    at.button(key="imp_go").click().run()
    with db.session(app_db) as conn:
        found = {r["description"]: r["subcategory"] for r in db.rows(conn, "SELECT description, subcategory FROM v_transactions")}
        costco = db.last_transaction(conn, "FAKE COSTCO #1")["id"]
        db.save_template_from_transaction(conn, costco)
    assert found == {"FAKE COSTCO #1": "groceries", "FAKE CAFE": "misc-expense"}

    # Recurring: the bill is already there this month, so it starts unticked and nothing can be added.
    at = _run(None)
    at.button(key="refresh").click().run()               # the template was written behind the app's back
    assert not at.exception, [e.value for e in at.exception]
    assert at.button(key="btn_add_recurring").disabled
    # Next month it is ticked; adding it creates exactly one new transaction, dated next month.
    options = at.segmented_control(key="rec_month").options
    at.segmented_control(key="rec_month").set_value(options[2]).run()
    assert not at.button(key="btn_add_recurring").disabled
    at.button(key="btn_add_recurring").click().run()
    assert not at.exception, [e.value for e in at.exception]
    with db.session(app_db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 3
        newest = db.last_transaction(conn)
    assert (newest["description"], newest["total_cents"], newest["notes"]) == ("FAKE COSTCO #1", 8420, "recurring")
    assert date.fromisoformat(newest["date"]).month == (today.month % 12) + 1

    # Budget: $84.20 of groceries against a $50 Home budget this month is flagged.
    at = at.switch_page("pages/1_Insights.py").run()
    at.pills(key="w_preset").set_value("This month").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("over budget" in w.value for w in at.warning)
    at = at.switch_page("pages/4_Setup.py").run()
    assert not at.exception, [e.value for e in at.exception]


def test_import_page_finds_the_header_and_imports_categories(app_db):
    """A made-up workbook whose tables start below row 1: the header is found, and the Category sheet imports."""
    from test_importer import _offset_workbook

    at = _run("pages/6_Import.py")
    at.file_uploader(key="imp_file").upload("offset.xlsx", _offset_workbook()).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.warning                                                   # no "no column names" message
    header = next(n for n in at.number_input if n.key and n.key.startswith("imp_header_"))
    assert header.value == 3                                                # the Savings table starts on row 3
    assert at.metric[0].value == "1"                                        # one readable savings row

    at.selectbox(key="imp_sheet").set_value("Category").run()
    assert not at.exception, [e.value for e in at.exception]
    kind = next(s for s in at.segmented_control if s.key and s.key.startswith("imp_kind_"))
    assert kind.value == "Categories"                                       # guessed from the sheet name
    assert [m.value for m in at.metric] == ["2", "4", "2", "3"]             # new categories, new subs, existing, conflicts
    at.button(key="imp_cat_go").click().run()
    assert not at.exception, [e.value for e in at.exception]
    with db.session(app_db) as conn:
        subs = {s["name"]: s["category"] for s in db.list_subcategories(conn)}
        assert conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"] == 0   # categories only
    assert subs["gym"] == "Health" and subs["bitcoin"] == "Crypto" and subs["pet food"] == "Home"
