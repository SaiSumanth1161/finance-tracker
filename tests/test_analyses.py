"""Tests for the chart modules in analyses/ (SPEC section 8: a new analysis = one file plus a test).

Each module's build() is pure pandas, so it is checked against the cases.json ledger.
"""
from __future__ import annotations

import pytest

import analyses
from analyses import (category_change, expense_by_category, income_vs_expense, monthly_share_by_person,
                      savings_cumulative, top_descriptions)
from core import analytics
from core.analytics import Filters, Period

YEAR_2026 = Filters(period=Period(years=(2026,)), view=analytics.ME_VIEW)


@pytest.fixture
def frames(ledger):
    conn, _ = ledger
    return analytics.load_frames(conn)


def test_modules_are_discovered_in_order():
    main = analyses.discover()
    assert [m.__name__.split(".")[-1] for m in main] == [
        "income_vs_expense", "expense_by_category", "monthly_share_by_person", "top_descriptions", "category_change"]
    assert [m.__name__.split(".")[-1] for m in analyses.discover("Savings")] == ["savings_cumulative"]
    for module in main + analyses.discover("Savings"):
        assert isinstance(module.TITLE, str) and callable(module.render) and callable(module.build)


def test_income_vs_expense(frames):
    data = income_vs_expense.build(*frames, YEAR_2026)
    assert data["label"].tolist() == ["Jan 2026", "Feb 2026", "Mar 2026"]
    assert data["income"].tolist() == [500000, 0, 2000]
    assert data["spend"].tolist() == [2700, 100000, 8500]          # spending shown as positive
    assert data["net"].tolist() == [497300, -100000, -6500]


def test_expense_by_category(frames):
    data = expense_by_category.build(*frames, YEAR_2026)
    assert data.values.tolist() == [["Home", 106700], ["Personal", 4500]]


def test_monthly_share_by_person_ignores_the_view(frames):
    data = monthly_share_by_person.build(*frames, YEAR_2026)
    february = data[data["Month"] == "2026-02"].set_index("Person")["spend"].to_dict()
    assert february == {"Mine": 100000, "Roommate 1": 100000, "Roommate 2": 100000}
    assert data["spend"].sum() == 322600                            # the household total


def test_top_descriptions(frames):
    data = top_descriptions.build(*frames, YEAR_2026)
    assert data.values.tolist() == [["Rent", 100000], ["Chipotle", 4500], ["Family phone plan", 4000], ["Costco", 2700]]
    assert len(top_descriptions.build(*frames, YEAR_2026, top=2)) == 2


def test_category_change(frames):
    # March vs the month before it (February): Home 4000 vs 100000, Personal 4500 vs 0.
    march = Filters(period=Period(years=(2026,), months=(3,)), view=analytics.ME_VIEW)
    data = category_change.build(*frames, march).set_index("Category")
    assert data.loc["Personal", ["now", "before", "change"]].tolist() == [4500, 0, 4500]
    assert data.loc["Home", ["now", "before", "change"]].tolist() == [4000, 100000, -96000]
    assert category_change.build(*frames, Filters()) is None        # "all time" has no previous period


def test_savings_cumulative(frames):
    data = savings_cumulative.build(*frames, YEAR_2026)
    stocks = data[data["Category"] == "Stocks"].set_index("Month")["cumulative"].to_dict()
    hysa = data[data["Category"] == "HYSA"].set_index("Month")["cumulative"].to_dict()
    assert stocks == {"2026-01": 50000, "2026-02": 50000}           # carried forward after January
    assert hysa == {"2026-01": 0, "2026-02": -100000}               # a withdrawal counts as negative
    empty = Filters(period=Period(years=(1999,)))
    assert savings_cumulative.build(*frames, empty).empty
