"""Savings tab: how much has been saved per category, adding up month by month."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import analytics, ui

TITLE = "Cumulative saved per category"
ORDER = 10
TAB = "Savings"


def build(df_tx, df_shares, filters):
    """Month, Category and cumulative saved (cents), counted from the start of the period."""
    result = analytics.aggregate(df_shares, "Saved", ["Month", "Category"], None, filters.view, filters.period)
    if result.empty:
        return result.assign(cumulative=[])[["Month", "Category", "cumulative"]]
    months = sorted(result["Month"].unique())
    wide = result.pivot_table(index="Month", columns="Category", values="value", aggfunc="sum", fill_value=0)
    wide = wide.reindex(months).fillna(0).cumsum().astype("int64")
    long = wide.reset_index().melt(id_vars="Month", var_name="Category", value_name="cumulative")
    return long[["Month", "Category", "cumulative"]]


def render(df_tx, df_shares, filters):
    data = build(df_tx, df_shares, filters)
    if data.empty:
        st.caption("No savings for these filters.")
        return
    savings = df_shares[df_shares["category_type"] == analytics.SAVINGS]
    colors = ui.series_colors(list(data["Category"].unique()), sorted(savings["category"].unique()))
    fig = go.Figure()
    for category in [name for name in colors if name in set(data["Category"])]:
        rows = data[data["Category"] == category]
        fig.add_trace(ui.line_trace(category, rows["Month"].map(ui.month_label), ui.dollars(rows["cumulative"]), colors[category]))
    ui.style_figure(fig, unified_hover=True)
    fig.update_yaxes(**ui.money_axis(ui.dollars(data["cumulative"].abs()).max()))
    fig.update_xaxes(showgrid=False, type="category")
    ui.show(fig, "chart_savings_cumulative")
