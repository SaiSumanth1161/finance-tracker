"""Where the money went: expense per category, biggest first."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import analytics, money, ui

TITLE = "Expense by category"
ORDER = 20


def build(df_tx, df_shares, filters):
    """Category and spend (expense as a positive number, cents), biggest first."""
    result = analytics.aggregate(df_shares, "Expense", ["Category"], None, filters.view, filters.period)
    return result.assign(spend=-result["value"])[["Category", "spend"]]


def render(df_tx, df_shares, filters):
    data = build(df_tx, df_shares, filters)
    if data.empty:
        st.caption("No expenses for these filters.")
        return
    bar = ui.bar_trace("Expense", ui.dollars(data["spend"]), data["Category"], ui.palette()["series"][0], horizontal=True)
    bar.update(text=[money.fmt_money(v) for v in data["spend"]], textposition="outside", cliponaxis=False)
    fig = ui.style_figure(go.Figure(bar), legend=False, height=max(220, 44 * len(data) + 60))
    fig.update_xaxes(**ui.money_axis(ui.dollars(data["spend"]).max()), rangemode="tozero")
    fig.update_yaxes(autorange="reversed", showgrid=False, type="category")
    fig.update_layout(margin=dict(r=70))
    ui.show(fig, "chart_expense_by_category")
