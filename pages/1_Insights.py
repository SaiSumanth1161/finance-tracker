"""Insights: the two dashboards, "Income & Expense" and "Savings" (SPEC section 7.2).

The tables reproduce the spreadsheet reports (parity checklist in section 7.2.3):
pivot-style Category -> Subcategory tables with data bars, drawn by ui.breakdown_table().
The charts below them are the modules in the analyses/ folder, found automatically.
Everything follows the global Year / Month / View filters in the sidebar.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

import analyses
from core import analytics, export, ui
from core.analytics import EXPENSE, INCOME
from core.ui import Col

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

d, filters = ui.page("Insights")
shares, period, view = d["shares"], filters.period, filters.view
current = period.apply(shares)                 # share rows inside the chosen period
previous_period = period.previous()
now = analytics.kpis(shares, view, period)
before = analytics.kpis(shares, view, previous_period) if previous_period else {}
roommates = ui.roommates(d)
sheets: dict[str, tuple[pd.DataFrame, list[str]]] = {}     # everything shown, for the Dashboard workbook


def table(title: str, tree: pd.DataFrame, cols: list[Col], key: str, first_label: str = "Category",
          bars: str = "all") -> None:
    """Draw a breakdown table and remember it as a sheet of the Dashboard workbook."""
    ui.breakdown_table(title, tree, cols, key, first_label, bars)
    sheets[title[:31]] = ui.tree_for_download(tree, cols, first_label)


def charts(tab: str) -> None:
    """Draw every analysis module of a tab, two per row."""
    modules = analyses.discover(tab)
    for start in range(0, len(modules), 2):
        row = modules[start:start + 2]
        for column, module in zip(st.columns(len(row)), row):      # a lone chart gets the full width
            with column.container(border=True):
                st.markdown(f"**{module.TITLE}**")
                module.render(d["tx"], shares, filters)


income_tab, savings_tab = st.tabs(["Income & Expense", "Savings"])

# ------------------------------------------------------------ Income & Expense

with income_tab:
    head, download_slot = st.columns([5, 1.4], vertical_alignment="center")
    head.subheader(f"Income & Expense · {period.label()}")
    head.caption(f"View: {ui.plain(view)}. Amount columns follow the view; My share and the per-person columns are always shown."
                 + (f" Arrows compare with {previous_period.label()}." if previous_period else ""))

    k = st.columns(6)
    ui.kpi_card(k[0], "Income", now["income"], before.get("income"), good="up")
    ui.kpi_card(k[1], "Expense", now["expense"], before.get("expense"), good="down", magnitude=True)
    ui.kpi_card(k[2], "Net", now["net"], before.get("net"), good="up", help="Income + expense (expense is negative).")
    ui.kpi_card(k[3], "My share", now["my_share"], before.get("my_share"), good="down", magnitude=True,
                help="My shares of all expenses, whatever the view.")
    ui.kpi_card(k[4], "Split", now["split"], before.get("split"), good=None,
                help="The part of the bills that others covered: household expense minus my share.")
    ui.kpi_card(k[5], "Savings rate", now["savings_rate"], before.get("savings_rate"), kind="rate", good="up",
                help="Net ÷ income.")

    left, right = st.columns(2)
    with left.container(border=True):
        income = analytics.person_table(current, ["category", "subcategory"], [], view, INCOME)
        table("Income by category", analytics.hierarchy(income, "category", "subcategory", ["amount"], sort_by="amount"),
              [Col("amount", "Income", bar="green")], "income_by_category")
    with right.container(border=True):
        by_category = analytics.person_table(current, ["category"], [], view)
        table("Expense by category",
              analytics.hierarchy(by_category, "category", None, ["amount", "my_share"], pct_of="amount", sort_by="amount"),
              [Col("amount", "Expense", bar="red"), Col("pct", "% of total", kind="pct"), Col("my_share", "My share")],
              "expense_by_category")

    with st.container(border=True):
        detail = analytics.person_table(current, ["category", "subcategory"], roommates, view)
        table("Expense by category and subcategory",
              analytics.hierarchy(detail, "category", "subcategory", ["amount", "my_share"] + roommates,
                                  pct_of="amount", sort_by="amount"),
              [Col("amount", "Expense", bar="red"), Col("pct", "% of total", kind="pct"), Col("my_share", "My share")]
              + [Col(name, name) for name in roommates],
              "expense_breakdown")

    with st.container(border=True):
        by_mode = analytics.person_table(current, ["payment_mode"], [], view)
        table("Expense by payment mode",
              analytics.hierarchy(by_mode, "payment_mode", None, ["amount", "my_share"], pct_of="amount", sort_by="amount"),
              [Col("amount", "Expense", bar="red"), Col("pct", "% of total", kind="pct"), Col("my_share", "My share")],
              "expense_by_payment_mode", first_label="Payment mode")

    # Budget against actual (SPEC phase 5). Shown only when budgets are set in Setup.
    budgets = {b["category"]: b["monthly_cents"] for b in d["budgets"]}
    if budgets:
        months = period.month_count(shares)
        budget = analytics.budget_table(current, budgets, months, view)
        over = int(budget["over"].sum())
        tree = pd.DataFrame({
            "level": "parent", "group": budget["category"],
            "label": [f"{name}  ⚠ over budget" if flag else name for name, flag in zip(budget["category"], budget["over"])],
            "budget": budget["budget"], "spent": budget["spent"], "remaining": budget["remaining"]})
        total = {"level": "total", "group": "", "label": "Grand Total", "budget": int(budget["budget"].sum()),
                 "spent": int(budget["spent"].sum()), "remaining": int(budget["remaining"].sum())}
        tree = pd.concat([tree, pd.DataFrame([total])], ignore_index=True)
        with st.container(border=True):
            table(f"Budget vs spent ({months} month{'s' if months != 1 else ''})", tree,
                  [Col("budget", "Budget"), Col("spent", "Spent", bar="red"), Col("remaining", "Remaining", bar="auto")],
                  "budget_vs_spent")
            if over:
                st.warning(f"{over} categor{'y is' if over == 1 else 'ies are'} over budget for {period.label()}.",
                           icon=":material/warning:")
            else:
                st.caption("Every budgeted category is within budget.")

    monthly = analytics.monthly_table(current, view)
    monthly["year"] = monthly["year"].astype(str)
    m1, m2, m3 = st.columns(3)
    with m1.container(border=True):
        table("Monthly income", analytics.hierarchy(monthly, "year", "month_name", ["income"]),
              [Col("income", "Income", bar="green")], "monthly_income", first_label="Year / month", bars="child")
    with m2.container(border=True):
        table("Monthly expense", analytics.hierarchy(monthly, "year", "month_name", ["expense", "my_share"]),
              [Col("expense", "Expense", bar="red"), Col("my_share", "My share")], "monthly_expense",
              first_label="Year / month", bars="child")
    with m3.container(border=True):
        table("Monthly net savings", analytics.hierarchy(monthly, "year", "month_name", ["net"]),
              [Col("net", "Net", bar="auto")], "monthly_net", first_label="Year / month", bars="child")

    st.subheader("Charts")
    st.caption("Charts show spending as positive amounts. The tables above carry the exact numbers.")
    charts("Income & Expense")

    pinned = [v for v in d["views"] if v["pinned"]]
    if pinned:
        st.subheader("Pinned custom views")
        for start in range(0, len(pinned), 2):
            for column, saved in zip(st.columns(2), pinned[start:start + 2]):
                with column.container(border=True):
                    config = json.loads(saved["config_json"])
                    used_view, used_period = analytics.resolve_view(config, filters)
                    st.markdown(f"**{ui.plain(saved['name'])}**")
                    st.caption(f"{config['measure']} by {' and '.join(config.get('group_by') or ['total'])} · "
                               f"{used_view} · {used_period.label()}")
                    result = analytics.run_view(config, shares, filters)
                    figure = ui.result_figure(result, config["measure"], config.get("group_by") or [],
                                              config.get("chart", "Bar"), d)
                    if figure is not None:
                        ui.show(figure, f"pinned_{saved['id']}")
                    elif result.empty:
                        st.caption("No data for these filters.")
                    else:
                        st.dataframe(ui.money_styler(ui.result_table(result, config["measure"]),
                                                     [] if config["measure"] == "Count" else [config["measure"]]),
                                     hide_index=True)

# --------------------------------------------------------------------- Savings

with savings_tab:
    st.subheader(f"Savings · {period.label()}")
    st.caption(f"View: {ui.plain(view)}. Money moved into savings counts as saved; money taken back out counts as withdrawn.")
    k = st.columns(5)
    ui.kpi_card(k[0], "Saved this period", now["saved"], before.get("saved"), good="up")
    ui.kpi_card(k[1], "Withdrawn", now["withdrawn"], before.get("withdrawn"), good="down")
    ui.kpi_card(k[2], "Net saved", now["net_saved"], before.get("net_saved"), good="up")
    ui.kpi_card(k[3], "Net (income − expense)", now["net"], before.get("net"), good="up")
    ui.kpi_card(k[4], "Unallocated", now["unallocated"], before.get("unallocated"), good=None,
                help="Net − Net saved: cash left in checking that was not moved anywhere.")

    saving_cols = [Col("saved", "Saved"), Col("withdrawn", "Withdrawn"), Col("net_saved", "Net saved", bar="auto")]
    left, right = st.columns(2)
    with left.container(border=True):
        by_month = analytics.savings_table(current, ["year", "month_num"], view).sort_values(["year", "month_num"])
        by_month["year"] = by_month["year"].astype(str)
        by_month["month_name"] = [analytics.MONTH_NAMES[m - 1] for m in by_month["month_num"]]
        table("Monthly net saved",
              analytics.hierarchy(by_month, "year", "month_name", ["saved", "withdrawn", "net_saved"]),
              saving_cols, "savings_monthly", first_label="Year / month", bars="child")
    with right.container(border=True):
        by_category = analytics.savings_table(current, ["category", "subcategory"], view)
        table("Saved by category",
              analytics.hierarchy(by_category, "category", "subcategory", ["saved", "withdrawn", "net_saved"],
                                  sort_by="net_saved"),
              saving_cols, "savings_by_category")
    charts("Savings")

# ---------------------------------------------------- Dashboard Excel download


def build_dashboard() -> bytes:
    """One sheet per table above, for the current filters. Runs when the button is clicked."""
    labels = [("Income", "income"), ("Expense", "expense"), ("Net", "net"), ("My share", "my_share"), ("Split", "split"),
              ("Saved", "saved"), ("Withdrawn", "withdrawn"), ("Net saved", "net_saved"), ("Unallocated", "unallocated")]
    summary = pd.DataFrame({"KPI": [label for label, _ in labels], "Amount": [now[key] for _, key in labels]})
    rate = now["savings_rate"]
    notes = pd.DataFrame({"Setting": ["Period", "View", "Savings rate", "Exported"],
                          "Value": [period.label(), view, "" if rate is None else f"{rate * 100:.1f}%", date.today().isoformat()]})
    return export.workbook_bytes({"KPIs": (summary, ["Amount"]), "Filters": (notes, []), **sheets})


download_slot.download_button("Dashboard Excel", build_dashboard, file_name=f"dashboard_{date.today().isoformat()}.xlsx",
                              mime=XLSX_MIME, icon=":material/download:", key="dashboard_excel", width="stretch")
