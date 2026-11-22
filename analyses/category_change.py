"""How each category's spending changed compared with the previous equal-length period."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import analytics, money, ui

TITLE = "Category change vs previous period"
ORDER = 50


def build(df_tx, df_shares, filters):
    """Category, spend now, spend before and change (all cents; change > 0 means spent more).

    Returns None when there is no previous period (e.g. "all time").
    """
    previous = filters.period.previous()
    if previous is None:
        return None

    def spend(period):
        result = analytics.aggregate(df_shares, "Expense", ["Category"], None, filters.view, period)
        return (-result.set_index("Category")["value"]).rename("spend")

    now, before = spend(filters.period), spend(previous)
    table = now.to_frame("now").join(before.to_frame("before"), how="outer").fillna(0).astype("int64")
    table["change"] = table["now"] - table["before"]
    table = table[table["change"] != 0].sort_values("change", ascending=False)
    return table.reset_index().rename(columns={"index": "Category"})


def render(df_tx, df_shares, filters):
    data = build(df_tx, df_shares, filters)
    if data is None:
        st.caption("Pick a year, months or a date range to compare with the period before it.")
        return
    if data.empty:
        st.caption("No change: nothing was spent in this period or the one before it.")
        return
    colors = ui.palette()
    more, less = colors["series"][7], colors["series"][0]          # red = spent more, blue = spent less
    bar = ui.bar_trace("Change", ui.dollars(data["change"]), data["Category"], more, horizontal=True)
    bar.update(marker_color=[more if v > 0 else less for v in data["change"]],
               text=[money.fmt_money(v, plus=True) for v in data["change"]], textposition="outside", cliponaxis=False)
    fig = ui.style_figure(go.Figure(bar), legend=False, height=max(220, 44 * len(data) + 60))
    fig.update_xaxes(**ui.money_axis(ui.dollars(data["change"].abs()).max()))
    fig.update_yaxes(autorange="reversed", showgrid=False, type="category")
    fig.update_layout(margin=dict(l=70, r=70))
    st.caption(f"Compared with {filters.period.previous().label()}. Red = spent more, blue = spent less.")
    ui.show(fig, "chart_category_change")
