"""Setup: the master lists (people, payment modes, categories, subcategories) and demo data.

Each tab is an editable table (SPEC section 7.5):
- add a row at the bottom, rename by typing over a name, reorder with the Order number
- untick Active to hide something from the forms without losing history
- a row that is in use cannot be deleted (the app says so and changes nothing)
Nothing is written until "Save changes" is pressed.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from core import db, demo, money, ui

d, filters = ui.page("Setup")
ss = st.session_state
ss.setdefault("setup_version", 0)      # part of the editor keys: bumping it clears pending edits


def blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == ""


def save_changes(table: str, before: pd.DataFrame, after: pd.DataFrame, to_values) -> None:
    """Compare the edited table with the original and write the differences in one transaction.

    to_values(row) turns an edited row into the dict of database fields for that table.
    If anything fails (a duplicate name, deleting a row in use) nothing is changed.
    """
    original = {row["id"]: to_values(row) for row in before.to_dict("records")}
    kept = {row["id"] for row in after.to_dict("records") if not blank(row.get("id"))}
    try:
        with db.session() as conn:
            for row_id in original.keys() - kept:
                db.delete_master_row(conn, table, row_id)
            for row in after.to_dict("records"):
                values = to_values(row)
                if blank(row.get("id")):
                    db.save_master_row(conn, table, values)
                elif values != original[row["id"]]:
                    db.save_master_row(conn, table, values, row["id"])
    except sqlite3.IntegrityError as problem:
        message = "Names must be unique." if "UNIQUE" in str(problem) else f"Could not save: {problem}"
        st.error(message)
        return
    except (ValueError, KeyError) as problem:           # includes db.InUseError
        st.error(str(problem))
        return
    ui.invalidate()
    ss["setup_version"] += 1
    ui.flash("Changes saved.")
    st.rerun()


def editor(table: str, frame: pd.DataFrame, config: dict, to_values, note: str) -> None:
    """One tab: the editable table plus its Save button."""
    st.caption(note)
    columns = [c for c in frame.columns if c != "id"]
    edited = st.data_editor(
        frame, key=f"edit_{table}_{ss['setup_version']}", num_rows="dynamic", hide_index=True,
        column_order=columns, column_config=config,
    )
    if st.button("Save changes", key=f"save_{table}", type="primary"):
        save_changes(table, frame, edited, to_values)


people_tab, modes_tab, categories_tab, subcategories_tab, budgets_tab = st.tabs(
    ["People", "Payment modes", "Categories", "Subcategories", "Budgets"])

with people_tab:
    frame = pd.DataFrame(
        [{"id": p["id"], "Name": p["name"], "Active": bool(p["active"]), "Order": p["sort_order"], "Me": bool(p["is_me"])}
         for p in d["people"]], columns=["id", "Name", "Active", "Order", "Me"])
    editor(
        "people", frame,
        {"Name": st.column_config.TextColumn(required=True),
         "Active": st.column_config.CheckboxColumn(default=True),
         "Order": st.column_config.NumberColumn(default=len(frame) + 1, step=1,
                                                help="Lower numbers come first. Leftover pennies also follow this order."),
         "Me": st.column_config.CheckboxColumn(disabled=True, default=False)},
        lambda row: {"name": row["Name"], "active": 1 if blank(row["Active"]) or row["Active"] else 0,
                     "sort_order": 0 if blank(row["Order"]) else int(row["Order"])},
        "Roommates and anyone else who shares bills. The row marked Me is you and cannot be removed.",
    )

with modes_tab:
    frame = pd.DataFrame(
        [{"id": m["id"], "Name": m["name"], "Kind": m["kind"], "Active": bool(m["active"]), "Order": m["sort_order"]}
         for m in d["modes"]], columns=["id", "Name", "Kind", "Active", "Order"])
    editor(
        "payment_modes", frame,
        {"Name": st.column_config.TextColumn(required=True),
         "Kind": st.column_config.SelectboxColumn(options=db.PAYMENT_KINDS, required=True, default="Credit card"),
         "Active": st.column_config.CheckboxColumn(default=True),
         "Order": st.column_config.NumberColumn(default=len(frame) + 1, step=1)},
        lambda row: {"name": row["Name"], "kind": row["Kind"] if not blank(row["Kind"]) else "Credit card",
                     "active": 1 if blank(row["Active"]) or row["Active"] else 0,
                     "sort_order": 0 if blank(row["Order"]) else int(row["Order"])},
        "How a bill was paid: cards, bank, cash or an app such as Zelle.",
    )

with categories_tab:
    type_ids = {t["name"]: t["id"] for t in d["types"]}
    frame = pd.DataFrame([{"id": c["id"], "Name": c["name"], "Type": c["category_type"]} for c in d["categories"]],
                         columns=["id", "Name", "Type"])
    editor(
        "categories", frame,
        {"Name": st.column_config.TextColumn(required=True),
         "Type": st.column_config.SelectboxColumn(options=list(type_ids), required=True, default="Expense")},
        lambda row: {"name": row["Name"], "category_type_id": type_ids[row["Type"] if not blank(row["Type"]) else "Expense"]},
        "The type decides how a category is counted: Income, Expense, Savings (only on the Savings dashboard) "
        "or Not Reported (left out everywhere). Subcategories inherit it.",
    )

with subcategories_tab:
    category_ids = {c["name"]: c["id"] for c in d["categories"]}
    frame = pd.DataFrame(
        [{"id": s["id"], "Name": s["name"], "Category": s["category"], "Active": bool(s["active"])}
         for s in d["subcategories"]], columns=["id", "Name", "Category", "Active"])
    editor(
        "subcategories", frame,
        {"Name": st.column_config.TextColumn(required=True),
         "Category": st.column_config.SelectboxColumn(options=list(category_ids), required=True),
         "Active": st.column_config.CheckboxColumn(default=True)},
        lambda row: {"name": row["Name"], "category_id": category_ids[row["Category"]],
                     "active": 1 if blank(row["Active"]) or row["Active"] else 0},
        "What you pick when entering a transaction. Each one belongs to a category.",
    )

with budgets_tab:
    # Monthly budget per expense category (SPEC phase 5). Insights flags categories that go over.
    st.caption("A monthly budget per expense category, in dollars. Leave a row empty for no budget. "
               "Insights compares it with what was spent and flags categories that are over.")
    current = {b["category_id"]: b["monthly_cents"] for b in d["budgets"]}
    expense = [c for c in d["categories"] if c["category_type"] == "Expense"]
    budget_frame = pd.DataFrame({
        "Category": [c["name"] for c in expense],
        "Monthly budget": [current[c["id"]] / 100 if c["id"] in current else None for c in expense],
    })
    edited_budgets = st.data_editor(
        budget_frame, key=f"edit_budgets_{ss['setup_version']}", hide_index=True, disabled=["Category"],
        column_config={"Monthly budget": st.column_config.NumberColumn(min_value=0, step=10, format="dollar")})
    if st.button("Save budgets", key="save_budgets", type="primary"):
        with db.session() as conn:
            for category, row in zip(expense, edited_budgets.to_dict("records")):
                amount = row["Monthly budget"]
                cents = None if blank(amount) else money.parse_amount(str(amount))
                db.set_budget(conn, category["id"], cents)
        ui.invalidate()
        ss["setup_version"] += 1
        ui.flash("Budgets saved.")
        st.rerun()

# ----------------------------------------------------------------- demo data

st.divider()
st.subheader("Demo data")
with db.session() as conn:
    loaded = demo.has_demo(conn)
st.caption("About 150 made-up transactions over the last 6 months, for trying the app. "
           "Clearing removes only these demo rows, never your own entries.")
left, right, _ = st.columns([1.3, 1.3, 5])
if left.button("Load demo data", disabled=loaded, key="demo_load", width="stretch"):
    with db.session() as conn:
        added = demo.load_demo(conn)
    ui.invalidate()
    ui.flash(f"Added {added} demo transactions.")
    st.rerun()
if right.button("Clear demo data", disabled=not loaded, key="demo_clear", width="stretch"):
    with db.session() as conn:
        removed = demo.clear_demo(conn)
    ui.invalidate()
    ui.flash(f"Removed {removed} demo transactions.")
    st.rerun()
