"""Who carried how much of the expenses each month (stacked, always the whole household)."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import analytics, ui

TITLE = "Monthly expense share by person"
ORDER = 30


def build(df_tx, df_shares, filters):
    """Month, Person and spend (expense as a positive number, cents). The view is ignored on purpose."""
    result = analytics.aggregate(df_shares, "Expense", ["Month", "Person"], None,
                                 analytics.HOUSEHOLD_VIEW, filters.period)
    return result.assign(spend=-result["value"])[["Month", "Person", "spend"]]


def render(df_tx, df_shares, filters):
    data = build(df_tx, df_shares, filters)
    if data.empty:
        st.caption("No expenses for these filters.")
        return
    # People keep the same colour everywhere: it follows their order in Setup.
    everyone = list(df_shares.sort_values("person_sort")["person"].unique())
    colors = ui.series_colors(list(data["Person"].unique()), everyone)
    months = sorted(data["Month"].unique())
    fig = go.Figure()
    for person in [name for name in everyone if name in set(data["Person"])]:
        values = data[data["Person"] == person].set_index("Month")["spend"].reindex(months).fillna(0)
        fig.add_trace(ui.bar_trace(person, [ui.month_label(m) for m in months], ui.dollars(values), colors[person]))
    ui.style_figure(fig, unified_hover=True)
    fig.update_layout(barmode="relative")
    fig.update_yaxes(**ui.money_axis(ui.dollars(data.groupby("Month")["spend"].sum()).max()))
    fig.update_xaxes(showgrid=False, type="category")
    ui.show(fig, "chart_share_by_person")
