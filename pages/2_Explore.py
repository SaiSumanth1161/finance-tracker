"""Explore: build a new analysis without code, then save it or pin it to Insights (SPEC section 7.3).

Everything on this page is one call to analytics.aggregate(). The choices are kept
as a small dict ("config"); saving a view stores that dict as JSON in saved_views.
"""
from __future__ import annotations

import json

import streamlit as st

from core import analytics, db, export, ui

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NONE = "(none)"
GLOBAL = "Global"

d, filters = ui.page("Explore")
ss = st.session_state

views = [GLOBAL, analytics.ME_VIEW] + [p["name"] for p in d["people"] if p["active"] and not p["is_me"]] + [analytics.HOUSEHOLD_VIEW]
periods = [GLOBAL] + analytics.PERIOD_PRESETS
categories = [c["name"] for c in d["categories"]]
modes = [m["name"] for m in d["modes"]]
groups = list(analytics.GROUPS)

# Defaults for the controls (only set the first time the page is opened).
DEFAULTS = {"ex_measure": "Expense", "ex_group1": "Month", "ex_group2": "Category", "ex_view": GLOBAL,
            "ex_period": "Last 6 months", "ex_categories": [], "ex_modes": [], "ex_text": "", "ex_chart": "Bar",
            "ex_name": ""}
for key, value in DEFAULTS.items():
    ss.setdefault(key, value)
if ss["ex_view"] not in views:               # e.g. a roommate was deactivated
    ss["ex_view"] = GLOBAL


def load_view(config_json: str, name: str) -> None:
    """Put a saved view back into the controls (runs before the page is redrawn)."""
    config = json.loads(config_json)
    group_by = config.get("group_by") or ["Month"]
    saved_filters = config.get("filters") or {}
    ss["ex_measure"] = config.get("measure", "Expense")
    ss["ex_group1"] = group_by[0]
    ss["ex_group2"] = group_by[1] if len(group_by) > 1 else NONE
    ss["ex_view"] = config.get("view") if config.get("view") in views else GLOBAL
    ss["ex_period"] = config.get("period") if config.get("period") in periods else GLOBAL
    ss["ex_categories"] = [c for c in saved_filters.get("categories", []) if c in categories]
    ss["ex_modes"] = [m for m in saved_filters.get("payment_modes", []) if m in modes]
    ss["ex_text"] = saved_filters.get("description_contains", "")
    ss["ex_chart"] = config.get("chart", "Bar")
    ss["ex_name"] = name


def save_view(pinned: bool) -> None:
    """Save the current choices under the typed name (an existing name is overwritten)."""
    try:
        with db.session() as conn:
            db.save_view(conn, ss["ex_name"], json.dumps(current_config()), pinned)
    except ValueError as problem:
        ui.flash(str(problem), "error")
        return
    ui.invalidate()
    ui.flash(f"Saved “{ss['ex_name'].strip()}”" + (" and pinned it to Insights." if pinned else "."))


def set_pinned(view_id: str, pinned: bool) -> None:
    with db.session() as conn:
        db.set_view_pinned(conn, view_id, pinned)
    ui.invalidate()


def delete_view(view_id: str) -> None:
    with db.session() as conn:
        db.delete_view(conn, view_id)
    ui.invalidate()
    ui.flash("View deleted.")


def current_config() -> dict:
    """The choices on screen as the dict that gets saved."""
    second = ss["ex_group2"]
    return {
        "measure": ss["ex_measure"],
        "group_by": [ss["ex_group1"]] + ([second] if second not in (NONE, ss["ex_group1"]) else []),
        "view": ss["ex_view"], "period": ss["ex_period"],
        "filters": {"categories": ss["ex_categories"], "payment_modes": ss["ex_modes"],
                    "description_contains": ss["ex_text"]},
        "chart": ss["ex_chart"],
    }


# -------------------------------------------------------------------- controls

c1, c2, c3, c4, c5 = st.columns(5)
c1.selectbox("Measure", analytics.MEASURES, key="ex_measure",
             help="Expense, Income, My share and Net are signed (expense is negative). Split = what others covered. "
                  "Saved = money moved into savings. Count = number of transactions. Avg = average expense per transaction.")
c2.selectbox("Group by", groups, key="ex_group1")
if ss["ex_group2"] == ss["ex_group1"]:
    ss["ex_group2"] = NONE
c3.selectbox("then by (optional)", [NONE] + [g for g in groups if g != ss["ex_group1"]], key="ex_group2")
c4.selectbox("View", views, key="ex_view",
             format_func=lambda v: f"Sidebar filter ({filters.view})" if v == GLOBAL else v)
c5.selectbox("Period", periods, key="ex_period",
             format_func=lambda p: f"Sidebar filter ({filters.period.label()})" if p == GLOBAL else p)

c1, c2, c3, c4 = st.columns([2, 2, 1.5, 1])
c1.multiselect("Category", categories, key="ex_categories", placeholder="any")
c2.multiselect("Payment mode", modes, key="ex_modes", placeholder="any")
c3.text_input("Description has", key="ex_text", placeholder="e.g. coffee")
c4.selectbox("Chart", ui.CHART_TYPES, key="ex_chart")

# ---------------------------------------------------------------------- result

config = current_config()
measure, group_by = config["measure"], config["group_by"]
used_view, used_period = analytics.resolve_view(config, filters)
result = analytics.run_view(config, d["shares"], filters)
st.caption(f"{measure} by {' and '.join(group_by)} · view: {ui.plain(used_view)} · period: {used_period.label()}"
           + (" · grouped by Person, so the view is not applied" if "Person" in group_by and measure not in ("My share", "Split") else ""))

with st.container(border=True):
    if result.empty:
        st.info("No data for these choices. Try a wider period or fewer filters.")
    else:
        figure = ui.result_figure(result, measure, group_by, config["chart"], d)
        if figure is not None:
            ui.show(figure, "explore_chart")
            if measure in ("Expense", "My share", "Avg"):
                st.caption("The chart shows spending as positive amounts; the table below keeps the signs.")
        elif config["chart"] == "Pie":
            st.caption("A pie needs positive amounts. Use Bar for this measure.")

display = ui.result_table(result, measure)
money_columns = [] if measure == "Count" else [measure]
left, right = st.columns([4, 1.5])
left.dataframe(ui.money_styler(display, money_columns), hide_index=True, height=min(420, 38 * (len(display) + 1) + 4))
right.download_button("CSV", export.csv_bytes(display), file_name="explore.csv", mime="text/csv",
                      icon=":material/download:", key="explore_csv", width="stretch")
right.download_button("Excel", lambda: export.workbook_bytes({"Explore": (result.rename(columns={"value": measure}), money_columns)}),
                      file_name="explore.xlsx", mime=XLSX_MIME, icon=":material/download:", key="explore_xlsx", width="stretch")

# ----------------------------------------------------------------- saved views

st.subheader("Saved views")
n1, n2, n3, _ = st.columns([2.5, 1.2, 1.6, 2])
n1.text_input("Name", key="ex_name", placeholder="e.g. Coffee trend", label_visibility="collapsed")
no_name = not ss["ex_name"].strip()
n2.button("Save view", on_click=save_view, args=(False,), disabled=no_name, icon=":material/save:", key="ex_save", width="stretch")
n3.button("Pin to Insights", on_click=save_view, args=(True,), disabled=no_name, icon=":material/keep:", key="ex_pin", width="stretch",
          help="Saves the view and shows it as a card at the bottom of Insights.")

if not d["views"]:
    st.caption("Nothing saved yet. Build a chart above, give it a name and press Save view.")
for saved in d["views"]:
    summary = json.loads(saved["config_json"])
    row = st.columns([3, 3.5, 1, 1.3, 1], vertical_alignment="center")
    row[0].markdown(f"**{ui.plain(saved['name'])}**" + (" · pinned" if saved["pinned"] else ""))
    row[1].caption(f"{summary.get('measure')} by {' and '.join(summary.get('group_by') or [])} · "
                   f"{summary.get('view')} · {summary.get('period')} · {summary.get('chart')}")
    row[2].button("Load", on_click=load_view, args=(saved["config_json"], saved["name"]), key=f"load_{saved['id']}", width="stretch")
    row[3].button("Unpin" if saved["pinned"] else "Pin", on_click=set_pinned, args=(saved["id"], not saved["pinned"]),
                  key=f"pin_{saved['id']}", width="stretch")
    row[4].button("Delete", on_click=delete_view, args=(saved["id"],), key=f"delete_{saved['id']}", width="stretch")
