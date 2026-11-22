"""Transactions (home page): quick add, the table of all transactions, edit / delete, Excel download.

Run with:  streamlit run app.py --server.address 127.0.0.1

How the form works (SPEC section 7.1):
- The same form function draws "Quick add" (key prefix "qa_") and "Edit" (key prefix "ed_").
- Every field is a widget whose value lives in st.session_state under prefix + field name.
- Buttons use on_click callbacks. A callback runs before the page is redrawn, so it can
  save to the database and then reset or pre-fill the fields.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import analytics, db, export, money, ui

ADD, EDIT = "qa_", "ed_"
SAVING_ADD, SAVING_EDIT = "sv_", "es_"          # the short Saving form: quick add and edit
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

d, filters = ui.page("Transactions")
ss = st.session_state


# ------------------------------------------------------------------ form: state

def form_people(d: dict, prefix: str) -> list[dict]:
    """People offered in a form: the active ones, plus anyone already on the transaction being edited."""
    extra = ss.get(prefix + "extra_people", [])
    return [p for p in d["people"] if p["active"] or p["id"] in extra]


def set_form(d: dict, prefix: str, tx: dict | None = None, *, on_date: date | None = None) -> None:
    """Fill the form fields: blank defaults (tx None) or the values of a transaction."""
    people = d["people"]
    me = next(p["id"] for p in people if p["is_me"])
    active_subs = [s for s in d["subcategories"] if s["active"]]
    active_modes = [m for m in d["modes"] if m["active"]]
    recent = d["tx"].sort_values("created_at") if len(d["tx"]) else d["tx"]
    ss[prefix + "extra_people"] = list(tx["shares"]) + [tx["paid_by_person_id"]] if tx else []
    ss[prefix + "extra_sub"] = tx["subcategory_id"] if tx else None
    ss[prefix + "extra_mode"] = tx["payment_mode_id"] if tx else None
    for key in ("error", "hint"):
        ss.pop(prefix + key, None)

    if tx is None:
        # Defaults: today, Money out, only me, paid by me, and the payment mode used last time.
        last_mode = recent.iloc[-1]["payment_mode_id"] if len(recent) else None
        expense_subs = [s for s in active_subs if s["category_type"] == analytics.EXPENSE] or active_subs
        equal = recent[recent["split_method"] == "equal"] if len(recent) else recent
        ticked = set(d["shares"].loc[d["shares"]["transaction_id"] == equal.iloc[-1]["id"], "person_id"]) if len(equal) else None
        ss[prefix + "date"] = date.today()
        ss[prefix + "desc"], ss[prefix + "amount"], ss[prefix + "notes"] = "", "", ""
        ss[prefix + "direction"], ss[prefix + "split"], ss[prefix + "paid_by"] = "debit", "only_me", me
        ss[prefix + "subcat"] = expense_subs[0]["id"] if expense_subs else None
        ss[prefix + "mode"] = last_mode if last_mode in [m["id"] for m in active_modes] else (active_modes[0]["id"] if active_modes else None)
        for p in people:
            ss[prefix + f"in_{p['id']}"] = bool(p["active"]) if ticked is None else p["id"] in ticked
            ss[prefix + f"exact_{p['id']}"], ss[prefix + f"pct_{p['id']}"] = "", ""
        return

    total = tx["total_cents"]
    ss[prefix + "date"] = on_date or date.fromisoformat(tx["date"])
    ss[prefix + "desc"], ss[prefix + "notes"] = tx["description"] or "", tx["notes"] or ""
    ss[prefix + "amount"] = f"{total / 100:.2f}"
    ss[prefix + "direction"], ss[prefix + "split"] = tx["direction"], tx["split_method"]
    ss[prefix + "subcat"], ss[prefix + "mode"], ss[prefix + "paid_by"] = tx["subcategory_id"], tx["payment_mode_id"], tx["paid_by_person_id"]
    percents = {pid: round(cents * 100 / total, 2) for pid, cents in tx["shares"].items()}
    exact_percents = abs(sum(percents.values()) - 100) < 1e-9
    for p in people:
        cents = tx["shares"].get(p["id"])
        ss[prefix + f"in_{p['id']}"] = cents is not None
        ss[prefix + f"exact_{p['id']}"] = f"{cents / 100:.2f}" if cents else ""
        ss[prefix + f"pct_{p['id']}"] = f"{percents[p['id']]:g}" if cents and exact_percents else ""


def keep_alive(prefix: str) -> None:
    """Stop Streamlit from forgetting fields that are hidden right now (e.g. the people
    ticks while the split is "Only me"). Re-assigning a key keeps it in session state."""
    for key in list(ss.keys()):
        if isinstance(key, str) and key.startswith(prefix):
            ss[key] = ss[key]


def collect(d: dict, prefix: str) -> dict:
    """Read the form and compute the split. Returns {"error": text or None, "total": cents, "shares": {...}}."""
    people = form_people(d, prefix)
    order = [p["id"] for p in d["people"]]
    me = next(p["id"] for p in d["people"] if p["is_me"])
    method, payer = ss[prefix + "split"], ss[prefix + "paid_by"]
    try:
        if method == "exact":
            exact = {p["id"]: money.parse_amount(ss[prefix + f"exact_{p['id']}"])
                     for p in people if str(ss.get(prefix + f"exact_{p['id']}", "")).strip()}
            total, shares = money.compute_split("exact", exact=exact)
        else:
            amount = money.parse_amount(ss[prefix + "amount"])
            who = [p["id"] for p in people if ss.get(prefix + f"in_{p['id']}")]
            percents = {p["id"]: money.parse_percent(ss.get(prefix + f"pct_{p['id']}", "")) for p in people}
            total, shares = money.compute_split(method, total_cents=amount, people=who, payer=payer,
                                                sort_order=order, me=me, percents=percents)
    except money.MoneyError as problem:
        return {"error": str(problem), "total": 0, "shares": {}}
    return {"error": None, "total": total, "shares": shares}


# -------------------------------------------------------------- form: callbacks

def on_description() -> None:
    """Smart defaults: a description used before pre-fills subcategory, payment mode and split."""
    d = ui.data()
    text = ss[ADD + "desc"].strip().lower()
    if not text or d["tx"].empty:
        return
    same = d["tx"][d["tx"]["description"].fillna("").str.strip().str.lower() == text]
    if same.empty:
        return
    last = same.sort_values("created_at").iloc[-1]
    if last["subcategory_id"] in [s["id"] for s in d["subcategories"] if s["active"]]:
        ss[ADD + "subcat"] = last["subcategory_id"]
    if last["payment_mode_id"] in [m["id"] for m in d["modes"] if m["active"]]:
        ss[ADD + "mode"] = last["payment_mode_id"]
    ss[ADD + "split"] = last["split_method"]
    shares = d["shares"][d["shares"]["transaction_id"] == last["id"]]
    total = int(last["total_cents"])
    percents = {pid: round(cents * 100 / total, 2) for pid, cents in zip(shares["person_id"], shares["share_cents"])}
    for p in form_people(d, ADD):
        ss[ADD + f"in_{p['id']}"] = p["id"] in percents
        if abs(sum(percents.values()) - 100) < 1e-9:
            ss[ADD + f"pct_{p['id']}"] = f"{percents[p['id']]:g}" if p["id"] in percents else ""
    ss[ADD + "hint"] = f"Pre-filled from your last “{last['description']}” ({last['date']:%b %d, %Y}). Change anything you like."


def on_save(prefix: str, mode: str, tx_id: str | None = None) -> None:
    """Save the form. mode: "save" (then reset), "another" (keep date / mode / split), "update" (edit)."""
    d = ui.data()
    result = collect(d, prefix)
    if result["error"]:
        ss[prefix + "error"] = result["error"]
        return
    tx = {
        "date": ss[prefix + "date"], "description": ss[prefix + "desc"], "direction": ss[prefix + "direction"],
        "subcategory_id": ss[prefix + "subcat"], "payment_mode_id": ss[prefix + "mode"],
        "paid_by_person_id": ss[prefix + "paid_by"], "split_method": ss[prefix + "split"], "notes": ss[prefix + "notes"],
    }
    if not tx["subcategory_id"] or not tx["payment_mode_id"]:
        ss[prefix + "error"] = "Pick a subcategory and a payment mode (add them in Setup if the lists are empty)."
        return
    with db.session() as conn:
        db.save_transaction(conn, tx, result["shares"], tx_id)
    ui.invalidate()
    name = tx["description"].strip() or "transaction"
    ui.flash(f"{'Updated' if mode == 'update' else 'Saved'} {name} · {money.fmt_money(result['total'])}")
    if mode == "save":
        set_form(ui.data(), prefix)
    elif mode == "another":                              # ready for the next bill of the same kind
        ss[prefix + "desc"], ss[prefix + "amount"], ss[prefix + "notes"] = "", "", ""
        for p in d["people"]:
            ss[prefix + f"exact_{p['id']}"] = ""
        for key in ("error", "hint"):
            ss.pop(prefix + key, None)
    else:
        close_edit()


def on_duplicate_last() -> None:
    """Copy the most recently entered transaction into the quick-add form, keeping its date."""
    with db.session() as conn:
        last = db.last_transaction(conn)
    if last is None:
        ss[ADD + "error"] = "There is no transaction to duplicate yet."
        return
    set_form(ui.data(), ADD, last)
    ss[ADD + "hint"] = (f"Copied from “{last['description'] or 'last transaction'}” "
                        f"({date.fromisoformat(last['date']):%b %d, %Y}), date included. Change anything, then press Save.")


def on_delete(tx_id: str) -> None:
    with db.session() as conn:
        db.delete_transaction(conn, tx_id)
    ui.invalidate()
    ui.flash("Transaction deleted.")
    close_edit()


def on_make_recurring(tx_id: str) -> None:
    """Turn the selected transaction into a recurring bill (same split, same day of the month)."""
    with db.session() as conn:
        db.save_template_from_transaction(conn, tx_id)
    ui.invalidate()
    ui.flash("Added to recurring bills.")


def template_as_transaction(template: dict, on: date) -> dict:
    """A recurring bill in the shape set_form() expects, dated `on`."""
    return {"date": on.isoformat(), "description": template["description"], "notes": "recurring",
            "total_cents": template["total_cents"], "direction": template["direction"],
            "split_method": template["split_method"], "subcategory_id": template["subcategory_id"],
            "payment_mode_id": template["payment_mode_id"], "paid_by_person_id": template["paid_by_person_id"],
            "shares": template["shares"]}


def on_open_recurring(on: date) -> None:
    """Load one recurring bill into Quick add, for changes the table cannot make (split, people)."""
    d = ui.data()
    template = next((t for t in d["templates"] if t["id"] == ss.get("rec_open_pick")), None)
    if template is None:
        return
    set_form(d, ADD, template_as_transaction(template, on))
    ss[ADD + "hint"] = f"Loaded the recurring bill “{template['name']}” for {on:%b %d, %Y}. Change anything, then press Save."
    ui.flash("Loaded into Quick add at the top of the page.")


def on_delete_template(template_id: str) -> None:
    with db.session() as conn:
        db.delete_template(conn, template_id)
    ui.invalidate()
    ui.flash("Recurring bill removed.")


def close_edit() -> None:
    """Close the edit panel by clearing the table's row selection."""
    ss["table_reset"] = ss.get("table_reset", 0) + 1
    ss["ed_loaded"] = None
    ss.pop("confirm_delete", None)


# ------------------------------------------------------------------ form: draw

def transaction_form(d: dict, prefix: str, tx_id: str | None = None) -> None:
    """Draw the add / edit form. tx_id is set when editing."""
    keep_alive(prefix)
    people = form_people(d, prefix)
    names = {p["id"]: p["name"] for p in d["people"]}
    subs = [s for s in d["subcategories"] if s["active"] or s["id"] == ss.get(prefix + "extra_sub")]
    modes = [m for m in d["modes"] if m["active"] or m["id"] == ss.get(prefix + "extra_mode")]
    sub_by_id = {s["id"]: s for s in subs}
    mode_names = {m["id"]: m["name"] for m in modes}
    if not subs or not modes:
        st.warning("Add at least one active subcategory and payment mode in Setup first.")
        return
    result = collect(d, prefix)                          # uses the values typed so far
    method = ss[prefix + "split"]

    c1, c2, c3 = st.columns([1.2, 3, 1.3])
    c1.date_input("Date", key=prefix + "date", format="MM/DD/YYYY")
    c2.text_input("Description", key=prefix + "desc", placeholder="e.g. Costco",
                  on_change=on_description if prefix == ADD else None)
    if method == "exact":
        c3.text_input("Amount (sum of shares)", value=money.fmt_money(result["total"]), disabled=True)
    else:
        c3.text_input("Amount", key=prefix + "amount", placeholder="0.00")

    c1, c2, c3, c4 = st.columns([1.6, 1.7, 1.4, 1.3])
    chosen = sub_by_id.get(ss[prefix + "subcat"])
    kind = chosen["category_type"] if chosen else None   # a Savings subcategory shows Deposit / Withdraw
    c1.radio("Direction", list(money.DIRECTIONS), key=prefix + "direction", horizontal=True,
             format_func=lambda direction: money.direction_label(direction, kind))
    c2.selectbox("Subcategory", list(sub_by_id), key=prefix + "subcat", format_func=lambda i: sub_by_id[i]["name"])
    if chosen:
        c2.caption(ui.plain(f"→ {chosen['category']} · {chosen['category_type']}"))
    c3.selectbox("Payment mode", list(mode_names), key=prefix + "mode", format_func=mode_names.get)
    c4.selectbox("Split", list(money.SPLIT_METHODS), key=prefix + "split", format_func=money.SPLIT_LABELS.get)

    # People: who shares the bill, and how much each one carries.
    if method == "only_me":
        st.caption("People: the whole amount is my share.")
    else:
        columns = st.columns(len(people))
        for column, p in zip(columns, people):
            share = result["shares"].get(p["id"])
            if method == "equal":
                column.checkbox(ui.plain(p["name"]), key=prefix + f"in_{p['id']}")
            elif method == "exact":
                column.text_input(ui.plain(p["name"]), key=prefix + f"exact_{p['id']}", placeholder="0.00")
            else:
                column.text_input(f"{ui.plain(p['name'])} %", key=prefix + f"pct_{p['id']}", placeholder="0")
            if share is not None and method != "exact":
                column.caption(money.fmt_money(share))

    with st.expander(f"More options (Paid by: {ui.plain(names.get(ss[prefix + 'paid_by'], '?'))}, Notes)"):
        o1, o2 = st.columns([1, 3])
        o1.selectbox("Paid by", [p["id"] for p in people], key=prefix + "paid_by", format_func=names.get,
                     help="Who actually paid. When I pay, each roommate's share is money they owe me.")
        o2.text_input("Notes", key=prefix + "notes")

    # Preview, messages and buttons.
    if result["error"]:
        typed = str(ss.get(prefix + "amount", "")).strip() or method == "exact"
        st.caption(f"Preview: {result['error']}" if typed else "Preview: enter an amount to see the split.")
    else:
        me = next(p["id"] for p in d["people"] if p["is_me"])
        direction = ss[prefix + "direction"]
        signed = money.signed_cents(result["total"], direction)
        mine = money.signed_cents(result["shares"].get(me, 0), direction)
        st.markdown(ui.md(f"**Preview:** Total {money.fmt_money(result['total'])} · Signed "
                          f"{money.fmt_money(signed, plus=True)} · My share {money.fmt_money(mine, plus=True)}"))
    if ss.get(prefix + "hint"):
        st.caption(ui.plain(ss[prefix + "hint"]))
    if ss.get(prefix + "error"):
        st.error(ui.plain(ss.pop(prefix + "error")))

    if tx_id is None:
        with st.container(horizontal=True):
            st.button("Save", type="primary", on_click=on_save, args=(prefix, "save"), key="btn_save")
            st.button("Save & add another", on_click=on_save, args=(prefix, "another"), key="btn_another")
            st.button("Duplicate last", on_click=on_duplicate_last, key="btn_duplicate",
                      help="Copy the transaction you entered last into this form, date included.")
    elif ss.get("confirm_delete"):
        st.warning("Delete this transaction? This cannot be undone.")
        b1, b2, _ = st.columns([1.2, 1, 5])
        b1.button("Yes, delete", type="primary", on_click=on_delete, args=(tx_id,), key="btn_delete_yes", width="stretch")
        b2.button("Cancel", on_click=lambda: ss.pop("confirm_delete", None), key="btn_delete_no", width="stretch")
    else:
        b1, b2, b3, b4, _ = st.columns([1, 1, 1, 1.6, 2.9])
        b1.button("Update", type="primary", on_click=on_save, args=(prefix, "update", tx_id), key="btn_update", width="stretch")
        b2.button("Delete", on_click=lambda: ss.update(confirm_delete=True), key="btn_delete", width="stretch")
        b3.button("Close", on_click=close_edit, key="btn_close", width="stretch")
        b4.button("Make recurring", on_click=on_make_recurring, args=(tx_id,), key="btn_recurring", width="stretch",
                  help="Save this bill as a monthly recurring bill, with the same split and day of the month.")


# ----------------------------------------------------------------- Saving form
# A short form for money moved into or out of savings (SPEC section 7.1).
# It always saves split "only_me" and Paid by me; neither is shown.

def is_saving_row(d: dict, tx: dict) -> bool:
    """True when a transaction can be edited with the short Saving form."""
    me = next(p["id"] for p in d["people"] if p["is_me"])
    sub = next((s for s in d["subcategories"] if s["id"] == tx["subcategory_id"]), None)
    return bool(sub and sub["category_type"] == analytics.SAVINGS and tx["split_method"] == "only_me"
                and tx["paid_by_person_id"] == me and set(tx["shares"]) == {me})


def set_saving_form(d: dict, prefix: str, tx: dict | None = None) -> None:
    """Fill the Saving form: defaults (tx None) or the values of a transaction."""
    subs = [s for s in d["subcategories"] if s["active"] and s["category_type"] == analytics.SAVINGS]
    modes = [m["id"] for m in d["modes"] if m["active"]]
    ss[prefix + "extra_sub"] = tx["subcategory_id"] if tx else None
    ss[prefix + "extra_mode"] = tx["payment_mode_id"] if tx else None
    ss.pop(prefix + "error", None)
    if tx is None:
        earlier = d["tx"][d["tx"]["category_type"] == analytics.SAVINGS] if len(d["tx"]) else d["tx"]
        last_mode = earlier.sort_values("created_at").iloc[-1]["payment_mode_id"] if len(earlier) else None
        ss[prefix + "date"] = date.today()
        ss[prefix + "where"] = subs[0]["id"] if subs else None
        ss[prefix + "action"], ss[prefix + "amount"], ss[prefix + "notes"] = "debit", "", ""
        ss[prefix + "mode"] = last_mode if last_mode in modes else (modes[0] if modes else None)
        ss[prefix + "desc"] = None                       # None = make the description from the subcategory
        return
    ss[prefix + "date"] = date.fromisoformat(tx["date"])
    ss[prefix + "where"], ss[prefix + "action"], ss[prefix + "mode"] = tx["subcategory_id"], tx["direction"], tx["payment_mode_id"]
    ss[prefix + "amount"], ss[prefix + "notes"] = f"{tx['total_cents'] / 100:.2f}", tx["notes"] or ""
    ss[prefix + "desc"] = tx["description"]              # keep the description of an edited row


def on_save_saving(prefix: str, mode: str, tx_id: str | None = None) -> None:
    """Save the Saving form. Deposit is stored as 'debit', Withdraw as 'credit'."""
    d = ui.data()
    me = next(p["id"] for p in d["people"] if p["is_me"])
    sub = next((s for s in d["subcategories"] if s["id"] == ss[prefix + "where"]), None)
    try:
        cents = money.parse_amount(ss[prefix + "amount"])
        if cents <= 0:
            raise money.MoneyError("The amount must be more than zero.")
        if sub is None or not ss[prefix + "mode"]:
            raise money.MoneyError("Pick where the money goes and an account.")
    except money.MoneyError as problem:
        ss[prefix + "error"] = str(problem)
        return
    action = ss[prefix + "action"]
    description = ss.get(prefix + "desc")
    if description is None:                              # new row: "stocks" or "stocks withdrawal"
        description = sub["name"] + (" withdrawal" if action == "credit" else "")
    tx = {"date": ss[prefix + "date"], "description": description, "direction": action,
          "subcategory_id": sub["id"], "payment_mode_id": ss[prefix + "mode"], "paid_by_person_id": me,
          "split_method": "only_me", "notes": ss[prefix + "notes"]}
    with db.session() as conn:
        db.save_transaction(conn, tx, {me: cents}, tx_id)
    ui.invalidate()
    saved = money.saved_cents(cents, action)
    ui.flash(f"{'Updated' if mode == 'update' else 'Saved'} {sub['name']} · Saved {money.fmt_money(saved, plus=True)}")
    if mode == "save":
        set_saving_form(ui.data(), prefix)
    elif mode == "another":
        ss[prefix + "amount"], ss[prefix + "notes"] = "", ""
    else:
        close_edit()


def saving_form(d: dict, prefix: str, tx_id: str | None = None) -> None:
    """Draw the Saving form. tx_id is set when editing."""
    keep_alive(prefix)
    subs = {s["id"]: s for s in d["subcategories"] if s["category_type"] == analytics.SAVINGS
            and (s["active"] or s["id"] == ss.get(prefix + "extra_sub"))}
    modes = {m["id"]: m["name"] for m in d["modes"] if m["active"] or m["id"] == ss.get(prefix + "extra_mode")}
    if not subs or not modes:
        st.info("Add a category of type Savings with a subcategory (and a payment mode) in Setup first.")
        return
    c1, c2 = st.columns([1.2, 3])
    c1.date_input("Date", key=prefix + "date", format="MM/DD/YYYY")
    c2.selectbox("Where", list(subs), key=prefix + "where", format_func=lambda i: subs[i]["name"])
    chosen = subs.get(ss[prefix + "where"])
    if chosen:
        c2.caption(ui.plain(f"→ {chosen['category']} · {chosen['category_type']}"))
    c1, c2 = st.columns([1.2, 3])
    c1.radio("Action", list(money.DIRECTIONS), key=prefix + "action", horizontal=True,
             format_func=money.SAVINGS_DIRECTION_LABELS.get)
    c2.text_input("Amount", key=prefix + "amount", placeholder="0.00")
    c1, c2 = st.columns([1.2, 3])
    c1.selectbox("From/To account", list(modes), key=prefix + "mode", format_func=modes.get)
    c2.text_input("Notes", key=prefix + "notes")

    try:
        saved = money.saved_cents(money.parse_amount(ss[prefix + "amount"]), ss[prefix + "action"])
        st.markdown(ui.md(f"**Preview:** Saved {money.fmt_money(saved, plus=True)}"))
    except money.MoneyError as problem:
        typed = str(ss.get(prefix + "amount", "")).strip()
        st.caption(f"Preview: {problem}" if typed else "Preview: enter an amount.")
    if ss.get(prefix + "error"):
        st.error(ss.pop(prefix + "error"))

    if tx_id is None:
        b1, b2, _ = st.columns([1, 1.6, 5.4])
        b1.button("Save", type="primary", on_click=on_save_saving, args=(prefix, "save"), key="btn_sv_save", width="stretch")
        b2.button("Save & add another", on_click=on_save_saving, args=(prefix, "another"), key="btn_sv_another", width="stretch")
    elif ss.get("confirm_delete"):
        st.warning("Delete this transaction? This cannot be undone.")
        b1, b2, _ = st.columns([1.2, 1, 5])
        b1.button("Yes, delete", type="primary", on_click=on_delete, args=(tx_id,), key="btn_delete_yes", width="stretch")
        b2.button("Cancel", on_click=lambda: ss.pop("confirm_delete", None), key="btn_delete_no", width="stretch")
    else:
        b1, b2, b3, _ = st.columns([1, 1, 1, 4.5])
        b1.button("Update", type="primary", on_click=on_save_saving, args=(prefix, "update", tx_id), key="btn_update", width="stretch")
        b2.button("Delete", on_click=lambda: ss.update(confirm_delete=True), key="btn_delete", width="stretch")
        b3.button("Close", on_click=close_edit, key="btn_close", width="stretch")


# ------------------------------------------------------------------- quick add

if ADD + "date" not in ss:
    set_form(d, ADD)
if SAVING_ADD + "date" not in ss:
    set_saving_form(d, SAVING_ADD)
with st.container(border=True):
    st.markdown("**Quick add**")
    bill_tab, saving_tab = st.tabs(["Bill / Income", "Saving"])
    with bill_tab:
        transaction_form(d, ADD)
    with saving_tab:
        saving_form(d, SAVING_ADD)


# Recurring bills (SPEC phase 4): pick which monthly bills to add, change them if needed, add.
def month_choices(today: date) -> dict[str, tuple[int, int]]:
    """Last, this and next month, as {label: (year, month)}."""
    def shift(step: int) -> tuple[int, int]:
        index = today.year * 12 + today.month - 1 + step
        return index // 12, index % 12 + 1
    return {f"{date(y, m, 1):%B %Y}": (y, m) for y, m in (shift(-1), shift(0), shift(1))}


templates = d["templates"]
with st.expander(f"Recurring bills ({len(templates)})"):
    if not templates:
        st.caption("None yet. Click a transaction in the table below and press “Make recurring”.")
    else:
        months = month_choices(date.today())
        labels = list(months)
        picked = st.segmented_control("Add bills for", labels, default=labels[1], key="rec_month") or labels[1]
        year, month = months[picked]
        with db.session() as conn:
            plan = db.recurring_plan(conn, year, month)
        active_modes = {m["name"]: m["id"] for m in d["modes"] if m["active"]}
        mode_names = {m["id"]: m["name"] for m in d["modes"]}
        st.caption("Tick the bills to add. Change the date, description, amount, payment mode or notes in the table "
                   "if this month is different. Bills already entered this month start unticked.")
        frame = pd.DataFrame({
            "Add": [not item["already_entered"] for item in plan],
            "Date": [item["date"] for item in plan],
            "Description": [item["description"] for item in plan],
            "Amount": [item["total_cents"] / 100 for item in plan],
            "Payment mode": [mode_names.get(item["payment_mode_id"]) for item in plan],
            "Notes": ["recurring"] * len(plan),
            "Split": [money.SPLIT_LABELS.get(item["split_method"], item["split_method"]) for item in plan],
            "Subcategory": [item["subcategory"] for item in plan],
            "Status": ["Already entered" if item["already_entered"] else "" for item in plan],
        })
        edited = st.data_editor(
            frame, hide_index=True, key=f"rec_editor_{year}_{month}_{ss.get('rec_version', 0)}",
            disabled=["Split", "Subcategory", "Status"], alt="Recurring bills to add",
            column_config={
                "Add": st.column_config.CheckboxColumn(width="small"),
                "Date": st.column_config.DateColumn(format="MM/DD/YYYY", required=True),
                "Amount": st.column_config.NumberColumn(min_value=0.01, step=0.01, format="dollar", required=True,
                                                        help="A new amount is split the same way as the bill "
                                                             "(equal stays equal; other splits keep each person's part)."),
                "Payment mode": st.column_config.SelectboxColumn(
                    options=sorted(set(active_modes) | {n for n in frame["Payment mode"] if n}), required=True),
            })
        chosen = [(item, row) for item, row in zip(plan, edited.to_dict("records")) if row["Add"]]
        again = sum(1 for item, _ in chosen if item["already_entered"])
        if again:
            st.warning(f"{again} ticked bill(s) are already entered for {picked} and will be added again.")
        save_default = st.checkbox("Also save changed amounts, payment modes and days as the new default for these bills",
                                   key="rec_save_default")
        if st.button(f"Add {len(chosen)} selected", type="primary", disabled=not chosen, key="btn_add_recurring"):
            items, problem = [], None
            for item, row in chosen:
                try:
                    cents = money.parse_amount(f"{float(row['Amount']):.2f}")
                    if cents <= 0 or pd.isna(row["Date"]):
                        raise money.MoneyError("needs a date and an amount above zero")
                except (money.MoneyError, TypeError, ValueError):
                    problem = f"“{row['Description'] or item['name']}” needs a date and an amount above zero."
                    break
                mode_id = next((m["id"] for m in d["modes"] if m["name"] == row["Payment mode"]), item["payment_mode_id"])
                items.append({"template_id": item["template_id"], "date": pd.Timestamp(row["Date"]).date(),
                              "description": (row["Description"] or "").strip(), "total_cents": cents,
                              "payment_mode_id": mode_id, "notes": row["Notes"] or ""})
            if problem:
                st.error(problem)
            else:
                with db.session() as conn:
                    added = db.add_recurring_items(conn, items, save_as_default=save_default)
                ui.invalidate()
                ss["rec_version"] = ss.get("rec_version", 0) + 1
                ui.flash(f"Added {added} recurring bill(s) for {picked}.")
                st.rerun()
        template_names = {t["id"]: t["name"] for t in templates}
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.selectbox("Need to change the split or people? Pick the bill", list(template_names),
                         format_func=template_names.get, key="rec_open_pick", width=320,
                         help="Loads the bill into Quick add at the top of the page, where everything can be changed.")
            open_date = next((item["date"] for item in plan if item["template_id"] == ss.get("rec_open_pick")), date.today())
            st.button("Open in Quick add", on_click=on_open_recurring, args=(open_date,), key="btn_open_recurring")

        st.markdown("**Manage recurring bills**")
        for template in templates:
            row = st.columns([3, 1, 1.4, 2.6, 0.7], vertical_alignment="center")
            row[0].markdown(f"**{ui.plain(template['name'])}**")
            row[1].caption(f"day {template['day_of_month']}")
            row[2].markdown(ui.md(money.fmt_money(template["total_cents"])))
            row[3].caption(ui.plain(f"{template['subcategory']} · {template['payment_mode']} · "
                                    f"{money.SPLIT_LABELS[template['split_method']]}"))
            row[4].button(":material/delete:", type="tertiary", on_click=on_delete_template, args=(template["id"],),
                          key=f"btn_rm_{template['id']}", help="Remove this recurring bill")

# ------------------------------------------------------------- all transactions

st.subheader("All transactions")
names = ui.person_columns(d)
mine = ui.me_name(d)

f1, f2, f3, f4, f5 = st.columns([2.2, 1.2, 1.2, 1.2, 1.2])
search = f1.text_input("Search", key="t_search", placeholder="description or notes")
pick_type = f2.selectbox("Type", ["All"] + [t["name"] for t in d["types"]], key="t_type")
pick_category = f3.selectbox("Category", ["All"] + [c["name"] for c in d["categories"]], key="t_category")
pick_person = f4.selectbox("Person", ["All"] + names, key="t_person", help="Transactions this person has a share in.")
pick_mode = f5.selectbox("Mode", ["All"] + [m["name"] for m in d["modes"]], key="t_mode")

tx = filters.period.apply(d["tx"])
if search.strip():
    text = (tx["description"].fillna("") + " " + tx["notes"].fillna(""))
    tx = tx[text.str.contains(search.strip(), case=False, regex=False)]
if pick_type != "All":
    tx = tx[tx["category_type"] == pick_type]
if pick_category != "All":
    tx = tx[tx["category"] == pick_category]
if pick_mode != "All":
    tx = tx[tx["payment_mode"] == pick_mode]
if pick_person != "All":
    theirs = d["shares"][(d["shares"]["person"] == pick_person) & (d["shares"]["share_cents"] > 0)]
    tx = tx[tx["id"].isin(set(theirs["transaction_id"]))]

wide = analytics.wide_transactions(tx, d["shares"], names).reset_index(drop=True)
ids = list(wide["id"])
person_cols = {name: ui.dollars(wide[name]) for name in names}
table = pd.DataFrame({
    "Date": wide["date"].dt.date, "Description": wide["description"],
    # Savings rows read Deposit / Withdraw, everything else Out / In.
    "Dir": [money.SAVINGS_DIRECTION_LABELS[direction] if kind == analytics.SAVINGS else {"debit": "Out", "credit": "In"}[direction]
            for direction, kind in zip(wide["direction"], wide["category_type"])],
    "Subcat": wide["subcategory"], "Category": wide["category"], "Type": wide["category_type"],
    "Mode": wide["payment_mode"], "Paid by": wide["paid_by"],
    **person_cols,
    "Total": ui.dollars(wide["total_cents"]),
    "Income/Expense": ui.dollars(wide["signed_total_cents"]),
    "My share": ui.dollars(wide["my_share_signed_cents"]),
})
money_cols = names + ["Total", "Income/Expense", "My share"]
signed_cols = ["Income/Expense", "My share"]

st.caption(f"{len(table):,} of {len(d['tx']):,} transactions · period: {filters.period.label()} · "
           "click a row to edit or delete it")
small = {column: st.column_config.Column(width="small") for column in ["Dir", "Type", "Mode", "Paid by"] + money_cols}
event = st.dataframe(
    ui.money_styler(table, money_cols, signed_cols, blank_zero_cols=names),   # no share -> empty cell
    hide_index=True, height=420, on_select="rerun", selection_mode="single-row",
    key=f"tx_table_{ss.get('table_reset', 0)}_{hash(tuple(ids))}",
    column_config={"Date": st.column_config.DateColumn("Date", format="MM/DD/YYYY", width="small"), **small},
)

# Totals for the rows shown, and the Excel download.
totals = pd.DataFrame([{
    "Rows": f"Totals ({len(table):,} rows)",
    **{name: int(wide[name].sum()) / 100 for name in names},
    "Total": int(wide["total_cents"].sum()) / 100,
    "Income/Expense": int(wide["signed_total_cents"].sum()) / 100,
    "My share": int(wide["my_share_signed_cents"].sum()) / 100,
}])
t1, t2 = st.columns([5, 1.6], vertical_alignment="center")
t1.dataframe(ui.money_styler(totals, money_cols, signed_cols), hide_index=True, column_config=small)
export_all = t2.checkbox("Export all instead", key="export_all", help="Ignore the filters and export every transaction.")


def build_excel() -> bytes:
    """Runs only when the download button is clicked."""
    with db.session() as conn:
        return export.transactions_workbook(conn, None if export_all else ids)


t2.download_button("Download Excel", build_excel, file_name=export.excel_file_name(), mime=XLSX_MIME,
                   icon=":material/download:", key="btn_excel", width="stretch")

# Edit / delete panel for the selected row.
selected = event.selection.rows
if selected and selected[0] < len(ids):
    tx_id = ids[selected[0]]
    if ss.get("ed_loaded") != tx_id:
        with db.session() as conn:
            current = db.get_transaction(conn, tx_id)
        ss["ed_saving"] = is_saving_row(d, current)      # plain savings rows use the short Saving form
        if ss["ed_saving"]:
            set_saving_form(d, SAVING_EDIT, current)
        else:
            set_form(d, EDIT, current)
        ss["ed_loaded"] = tx_id
        ss.pop("confirm_delete", None)
    with st.container(border=True):
        if ss.get("ed_saving"):
            st.markdown("**Edit saving**")
            saving_form(d, SAVING_EDIT, tx_id)
        else:
            st.markdown("**Edit transaction**")
            transaction_form(d, EDIT, tx_id)
else:
    ss["ed_loaded"] = None
