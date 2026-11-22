"""Tests for core/analytics.py, driven by the "ledger" in tests/cases.json (SPEC section 9)."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import CASES, PERSON_NAMES

from core import analytics
from core.analytics import Filters, Period

LEDGER = CASES["ledger"]
YEAR_2026 = Period(years=(2026,))


def _view(key: str) -> str:
    """cases.json view key -> the app's view name."""
    return {"me": analytics.ME_VIEW, "household": analytics.HOUSEHOLD_VIEW}.get(key, PERSON_NAMES.get(key, key))


@pytest.fixture
def frames(ledger):
    conn, ids = ledger
    tx, shares = analytics.load_frames(conn)
    return tx, shares, ids


@pytest.mark.parametrize("view", ["me", "household", "roommate1"])
def test_kpis(frames, view):
    _, shares, _ = frames
    result = analytics.kpis(shares, _view(view), YEAR_2026)
    for key, value in LEDGER["kpis"][view].items():
        assert result[key] == value, key
    # Net = income + expense, and Not Reported / Savings rows are not part of either.
    assert result["net"] == result["income"] + result["expense"]


def test_savings_rate(frames):
    _, shares, _ = frames
    result = analytics.kpis(shares, analytics.ME_VIEW, YEAR_2026)
    assert result["savings_rate"] == pytest.approx(390800 / 502000)
    assert analytics.kpis(shares, "Roommate 1", YEAR_2026)["savings_rate"] is None  # no income


def test_refund_lowers_expense(frames):
    _, shares, _ = frames
    january = analytics.kpis(shares, analytics.HOUSEHOLD_VIEW, Period(years=(2026,), months=(1,)))
    assert january["expense"] == -9000 + 900


def test_year_month_filter_excludes_february(frames):
    tx, _, ids = frames
    case = LEDGER["period_filter"]
    period = Period(years=tuple(case["years"]), months=tuple(case["months"]))
    assert set(period.apply(tx)["id"]) == {ids[key] for key in case["expected_ids"]}


def test_date_range_period(frames):
    tx, _, ids = frames
    period = Period(start=date(2026, 1, 10), end=date(2026, 1, 15))
    assert set(period.apply(tx)["id"]) == {ids["t2"], ids["t3"], ids["t4"]}  # both ends included


@pytest.mark.parametrize("case", LEDGER["aggregate"],
                         ids=lambda c: f"{c['measure']} by {'+'.join(c['group_by']) or 'nothing'} ({c['view']})")
def test_aggregate(frames, case):
    _, shares, _ = frames
    result = analytics.aggregate(shares, case["measure"], case["group_by"], None, _view(case["view"]), YEAR_2026)
    rows = [[PERSON_NAMES.get(v, v) if isinstance(v, str) else v for v in row] for row in case["expected"]]
    assert result.values.tolist() == rows
    assert list(result.columns) == case["group_by"] + ["value"]


def test_aggregate_filters(frames):
    _, shares, _ = frames
    household = analytics.HOUSEHOLD_VIEW
    by_mode = analytics.aggregate(shares, "Expense", [], {"payment_modes": ["visa"]}, household, YEAR_2026)
    assert by_mode["value"].iloc[0] == -14500
    by_text = analytics.aggregate(shares, "Expense", [], {"description_contains": "cost"}, household, YEAR_2026)
    assert by_text["value"].iloc[0] == -8100
    by_category = analytics.aggregate(shares, "Expense", [], {"categories": ["Personal"]}, household, None)
    assert by_category["value"].iloc[0] == -4500
    with pytest.raises(ValueError):
        analytics.aggregate(shares, "Profit")


def test_aggregate_on_empty_data(conn):
    _, shares = analytics.load_frames(conn)
    assert analytics.aggregate(shares, "Expense", ["Month"]).empty
    assert analytics.aggregate(shares, "Expense")["value"].iloc[0] == 0
    assert analytics.kpis(shares)["net"] == 0
    assert analytics.monthly_table(shares).empty
    assert analytics.person_table(shares, ["category"], ["Roommate 1"]).empty


def test_previous_period():
    # A whole year -> the year before.
    assert Period(years=(2026,)).previous() == Period(start=date(2025, 1, 1), end=date(2025, 12, 31))
    # An unbroken run of months -> the same number of months right before it (may cross a year).
    assert Period(years=(2026,), months=(2, 3, 4)).previous() == Period(start=date(2025, 11, 1), end=date(2026, 1, 31))
    assert Period(years=(2026,), months=(1,)).previous() == Period(start=date(2025, 12, 1), end=date(2025, 12, 31))
    # Months with a gap -> the same months one year earlier.
    assert Period(years=(2026,), months=(1, 3)).previous() == Period(years=(2025,), months=(1, 3))
    # A date range -> the same number of days before it.
    assert Period(start=date(2026, 3, 1), end=date(2026, 3, 10)).previous() == Period(start=date(2026, 2, 19), end=date(2026, 2, 28))
    assert Period().previous() is None


def test_named_periods():
    today = date(2026, 1, 20)
    assert analytics.named_period("This month", today) == Period(years=(2026,), months=(1,))
    assert analytics.named_period("Last month", today) == Period(years=(2025,), months=(12,))
    assert analytics.named_period("Last 3 months", today) == Period(start=date(2025, 11, 1), end=date(2026, 1, 31))
    assert analytics.named_period("YTD", date(2026, 3, 5)) == Period(years=(2026,), months=(1, 2, 3))
    assert analytics.named_period("All time", today) == Period()
    assert Period(years=(2026,), months=(3, 1)).label() == "2026 · Jan, Mar"
    assert Period().label() == "All time"


def test_person_table_and_hierarchy(frames):
    _, shares, _ = frames
    data = YEAR_2026.apply(shares)
    table = analytics.person_table(data, ["category", "subcategory"], ["Roommate 1", "Roommate 2", "Others"], analytics.HOUSEHOLD_VIEW)
    rent = table[table["subcategory"] == "rent"].iloc[0]
    assert (rent["amount"], rent["my_share"], rent["Roommate 1"], rent["Roommate 2"], rent["Others"]) == (-300000, -100000, -100000, -100000, 0)
    # Household amount is the sum of everyone's columns.
    assert (table["amount"] == table["my_share"] + table["Roommate 1"] + table["Roommate 2"] + table["Others"]).all()

    tree = analytics.hierarchy(table, "category", "subcategory", ["amount", "my_share", "Roommate 1"],
                               pct_of="amount", sort_by="amount")
    assert tree["label"].tolist() == ["Home", "rent", "phone", "groceries", "Personal", "outside food", "Grand Total"]
    assert tree["level"].tolist() == ["parent", "child", "child", "child", "parent", "child", "total"]
    home, total = tree.iloc[0], tree.iloc[-1]
    assert home["amount"] == -318100 and total["amount"] == -322600 and total["my_share"] == -111200
    assert total["pct"] == 100.0 and home["pct"] == pytest.approx(98.6)
    # In the Me view the Amount column equals the My share column.
    mine = analytics.person_table(data, ["category"], ["Roommate 1"], analytics.ME_VIEW)
    assert (mine["amount"] == mine["my_share"]).all()


def test_monthly_and_savings_tables(frames):
    _, shares, _ = frames
    data = YEAR_2026.apply(shares)
    monthly = analytics.monthly_table(data, analytics.ME_VIEW)
    assert monthly["month_name"].tolist() == ["Jan", "Feb", "Mar"]
    assert monthly["income"].tolist() == [500000, 0, 2000]
    assert monthly["expense"].tolist() == [-2700, -100000, -8500]
    assert monthly["net"].tolist() == [497300, -100000, -6500]
    assert monthly["saved"].tolist() == [50000, -100000, 0]

    savings = analytics.savings_table(data, ["category", "subcategory"], analytics.ME_VIEW)
    by_sub = savings.set_index("subcategory")
    assert by_sub.loc["stocks", ["saved", "withdrawn", "net_saved"]].tolist() == [50000, 0, 50000]
    assert by_sub.loc["high-yield savings", ["saved", "withdrawn", "net_saved"]].tolist() == [0, 100000, -100000]


def test_wide_transactions(frames):
    tx, shares, ids = frames
    wide = analytics.wide_transactions(tx, shares, ["Mine", "Roommate 1", "Roommate 2", "Others"]).set_index("id")
    phone = wide.loc[ids["t9"]]
    assert (phone["Mine"], phone["Roommate 1"], phone["Roommate 2"], phone["Others"]) == (4000, 3000, 0, 3000)
    assert (wide[["Mine", "Roommate 1", "Roommate 2", "Others"]].sum(axis=1) == wide["total_cents"]).all()


def test_saved_view_follows_or_fixes_filters(frames):
    _, shares, _ = frames
    global_filters = Filters(period=YEAR_2026, view=analytics.HOUSEHOLD_VIEW)
    follows = {"measure": "Expense", "group_by": [], "view": "Global", "period": "Global"}
    assert analytics.run_view(follows, shares, global_filters)["value"].iloc[0] == -322600
    fixed = {"measure": "Expense", "group_by": [], "view": "Me", "period": "Last month"}
    # "Last month" seen from 2026-03-20 is February 2026: only my third of the rent.
    assert analytics.run_view(fixed, shares, global_filters, today=date(2026, 3, 20))["value"].iloc[0] == -100000
