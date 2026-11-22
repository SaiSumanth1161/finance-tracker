"""Tests for core/money.py, driven by tests/cases.json (SPEC section 9)."""
from __future__ import annotations

import pytest
from conftest import CASES

from core import money

ORDER = CASES["sort_order"]


def _split(case):
    return money.compute_split(
        case["method"], total_cents=case.get("total_cents", 0), people=case.get("people", []),
        payer=case.get("payer"), sort_order=ORDER, me="me",
        exact=case.get("exact"), percents=case.get("percents"),
    )


@pytest.mark.parametrize("case", CASES["splits"], ids=lambda c: c["name"])
def test_splits(case):
    total, shares = _split(case)
    assert shares == case["expected"]
    assert total == case.get("expected_total_cents", case.get("total_cents"))
    assert sum(shares.values()) == total  # shares always add up to the total


@pytest.mark.parametrize("case", CASES["split_errors"], ids=lambda c: c["name"])
def test_split_errors(case):
    with pytest.raises(money.MoneyError):
        _split(case)


@pytest.mark.parametrize("case", CASES["validate_errors"], ids=lambda c: c["name"])
def test_validate_errors(case):
    with pytest.raises(money.MoneyError):
        money.validate_shares(case["total_cents"], case["shares"])


@pytest.mark.parametrize("case", CASES["parse_amount"], ids=lambda c: c["text"])
def test_parse_amount(case):
    assert money.parse_amount(case["text"]) == case["expected_cents"]


@pytest.mark.parametrize("text", CASES["parse_amount_errors"])
def test_parse_amount_errors(text):
    with pytest.raises(money.MoneyError):
        money.parse_amount(text)


@pytest.mark.parametrize("case", CASES["format"], ids=lambda c: f"{c['cents']}/{c['plus']}")
def test_format(case):
    assert money.fmt_money(case["cents"], plus=case["plus"]) == case["expected"]


@pytest.mark.parametrize("case", CASES["signs"], ids=lambda c: c["name"])
def test_signs(case):
    assert money.signed_cents(case["total_cents"], case["direction"]) == case["expected_signed_cents"]


@pytest.mark.parametrize("case", CASES["savings"], ids=lambda c: c["name"])
def test_savings_sign(case):
    assert money.saved_cents(case["total_cents"], case["direction"]) == case["expected_saved_cents"]


def test_kpi_formulas():
    split, net, unallocated = CASES["kpi_formulas"]
    assert money.split_kpi_cents(split["household_expense_cents"], split["my_share_expense_cents"]) == split["expected_split_cents"]
    assert money.net_cents(net["income_cents"], net["expense_cents"]) == net["expected_net_cents"]
    assert money.unallocated_cents(unallocated["net_cents"], unallocated["net_saved_cents"]) == unallocated["expected_unallocated_cents"]
    assert money.savings_rate(250, 1000) == 0.25
    assert money.savings_rate(250, 0) is None


@pytest.mark.parametrize("case", CASES["balances"], ids=lambda c: c["name"])
def test_balances(case):
    nets = money.balances(case["transactions"], case["settlements"])
    assert nets == case["expected_nets"]
    assert sum(nets.values()) == 0  # money is only moved around, never created
    plan = money.settle_plan(nets, ORDER)
    assert [list(step) for step in plan] == case["expected_plan"]


def test_direction_labels():
    assert money.direction_label("debit") == "Money out" and money.direction_label("credit", "Expense") == "Money in"
    assert money.direction_label("debit", "Savings") == "Deposit" and money.direction_label("credit", "Savings") == "Withdraw"
