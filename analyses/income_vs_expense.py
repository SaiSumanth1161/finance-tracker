"""Income and expense bars per month, with a net line. All three share one dollar axis."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import analytics, ui

TITLE = "Income vs expense by month"
ORDER = 10


def build(df_tx, df_shares, filters):
    """One row per month: label, income, spend (expense as a positive number) and net, in cents."""
    table = analytics.monthly_table(filters.period.apply(df_shares), filters.view)
    table["label"] = [f"{name} {year}" for name, year in zip(table["month_name"], table["year"])]
    table["spend"] = -table["expense"]
    return table[["label", "income", "spend", "net"]]


def render(df_tx, df_shares, filters):
    data = build(df_tx, df_shares, filters)
    if data.empty:
        st.caption("No income or expenses for these filters.")
        return
    colors = ui.palette()
    fig = go.Figure([
        ui.bar_trace("Income", data["label"], ui.dollars(data["income"]), colors["series"][0]),
        ui.bar_trace("Expense", data["label"], ui.dollars(data["spend"]), colors["series"][1]),
        ui.line_trace("Net", data["label"], ui.dollars(data["net"]), colors["muted"]),
    ])
    ui.style_figure(fig, unified_hover=True)
    fig.update_layout(barmode="group")
    fig.update_yaxes(**ui.money_axis(ui.dollars(data[["income", "spend"]].max().max())))
    fig.update_xaxes(showgrid=False, type="category")
    ui.show(fig, "chart_income_vs_expense")
