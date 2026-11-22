"""Settle up: who owes whom, recording a settlement, history and a summary to paste into a chat.

Balances are a running total over ALL transactions and settlements, so the sidebar
period filter does not apply here (SPEC sections 5 and 7.4).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import db, money, ui

d, filters = ui.page("Settle up")
ss = st.session_state

people = d["people"]
name_of = {p["id"]: p["name"] for p in people}
order = [p["id"] for p in people]
me = next(p["id"] for p in people if p["is_me"])
nets = money.balances(*d["balance_inputs"])               # + is owed money, − owes money
plan = money.settle_plan(nets, order)                     # [(from, to, cents)], fewest transfers
others = [p for p in people if not p["is_me"] and (p["active"] or nets.get(p["id"], 0) != 0)]


def who(person_id: str, capital: bool = False) -> str:
    """A person's name, or you / You for me."""
    if person_id == me:
        return "You" if capital else "you"
    return name_of[person_id]


def card_text(person_id: str) -> tuple[str, str]:
    """(headline, colour) for one roommate, read from the transfer plan."""
    pays = [(to, cents) for frm, to, cents in plan if frm == person_id]
    gets = [(frm, cents) for frm, to, cents in plan if to == person_id]
    if pays:
        return " · ".join(f"owes {who(to)} {money.fmt_money(cents)}" for to, cents in pays), "green" if pays[0][0] == me else "gray"
    if gets:
        return " · ".join(f"{who(frm, True)} owe{'s' if frm != me else ''} {name_of[person_id]} {money.fmt_money(cents)}"
                          for frm, cents in gets), "red" if gets[0][0] == me else "gray"
    return "settled up", "gray"


# ------------------------------------------------------------------- balances

st.caption("Balances count every transaction and settlement ever entered; the sidebar period does not apply here.")
if not others:
    st.info("Add a roommate in Setup to start splitting bills.")
for column, person in zip(st.columns(max(len(others), 1)), others):
    text, color = card_text(person["id"])
    with column.container(border=True):
        st.caption(ui.plain(person["name"]))
        if text.startswith("owes"):
            st.markdown(f"#### :{color}[{ui.plain(person['name'] + ' ' + text)}]")
        else:
            st.markdown(f"#### :{color}[{ui.plain(text[0].upper() + text[1:])}]")

left, right = st.columns([1, 1.4])
with left.container(border=True):
    st.markdown("**Who pays whom**")
    if not plan:
        st.caption("Everyone is settled up.")
    for payer, receiver, cents in plan:
        st.markdown(f"{ui.plain(who(payer, True))} → {ui.plain(who(receiver))}: **{ui.md(money.fmt_money(cents))}**")

# ---------------------------------------------------------- record settlement


def owed(payer: str, receiver: str) -> int:
    """What the plan says `payer` should pay `receiver` (0 if nothing)."""
    return next((cents for frm, to, cents in plan if frm == payer and to == receiver), 0)


def record_settlement() -> None:
    payer, receiver = ss["su_from"], ss["su_to"]
    try:
        cents = money.parse_amount(ss[f"su_amount_{payer}_{receiver}"])
        with db.session() as conn:
            db.add_settlement(conn, ss["su_date"], payer, receiver, cents, ss["su_mode"], ss["su_notes"])
    except money.MoneyError as problem:
        ui.flash(str(problem), "error")
        return
    ui.invalidate()
    ss["su_notes"] = ""
    ss.pop(f"su_amount_{payer}_{receiver}", None)
    ui.flash(f"Recorded: {name_of[payer]} paid {name_of[receiver]} {money.fmt_money(cents)}.")


def delete_settlement(settlement_id: str) -> None:
    with db.session() as conn:
        db.delete_settlement(conn, settlement_id)
    ui.invalidate()
    ui.flash("Settlement deleted.")


active_ids = [p["id"] for p in people if p["active"]]
active_modes = [m for m in d["modes"] if m["active"]]
first_debtor = next((frm for frm, to, _ in plan if to == me), next((i for i in active_ids if i != me), me))
zelle = next((m["id"] for m in active_modes if m["name"].lower() == "zelle"), active_modes[0]["id"] if active_modes else None)
ss.setdefault("su_from", first_debtor)
ss.setdefault("su_to", me)
ss.setdefault("su_date", date.today())
ss.setdefault("su_mode", zelle)
ss.setdefault("su_notes", "")
for key in ("su_from", "su_to"):
    if ss[key] not in active_ids:
        ss[key] = me

with right.container(border=True):
    st.markdown("**Record settlement**")
    r1, r2, r3 = st.columns(3)
    r1.selectbox("From", active_ids, key="su_from", format_func=name_of.get)
    r2.selectbox("To", active_ids, key="su_to", format_func=name_of.get)
    r3.date_input("Date", key="su_date", format="MM/DD/YYYY")
    suggested = owed(ss["su_from"], ss["su_to"])
    r1, r2, r3 = st.columns(3)
    # The amount box is keyed by the pair, so picking another person shows what THEY owe.
    amount_key = f"su_amount_{ss['su_from']}_{ss['su_to']}"
    ss.setdefault(amount_key, f"{suggested / 100:.2f}" if suggested else "")
    r1.text_input("Amount", key=amount_key, placeholder="0.00")
    r2.selectbox("Payment mode", [m["id"] for m in active_modes], key="su_mode",
                 format_func={m["id"]: m["name"] for m in active_modes}.get)
    r3.text_input("Notes", key="su_notes")
    st.button("Record settlement", type="primary", on_click=record_settlement, key="su_save",
              disabled=ss["su_from"] == ss["su_to"])
    if ss["su_from"] == ss["su_to"]:
        st.caption("Pick two different people.")

# --------------------------------------------------------------------- history

st.subheader("Settlement history")
history = d["settlements"]
if not history:
    st.caption("No settlements recorded yet.")
else:
    frame = pd.DataFrame({
        "Date": [date.fromisoformat(s["date"]) for s in history],
        "From": [s["from_person"] for s in history],
        "To": [s["to_person"] for s in history],
        "Amount": [s["amount_cents"] / 100 for s in history],        # display only
        "Mode": [s["payment_mode"] or "" for s in history],
        "Notes": [s["notes"] or "" for s in history],
    })
    event = st.dataframe(ui.money_styler(frame, ["Amount"]), hide_index=True, on_select="rerun",
                         selection_mode="single-row", key=f"su_history_{len(history)}",
                         height=min(360, 38 * (len(frame) + 1) + 4),
                         column_config={"Date": st.column_config.DateColumn(format="MM/DD/YYYY")})
    picked = event.selection.rows
    if picked and picked[0] < len(history):
        chosen = history[picked[0]]
        st.button(ui.plain(f"Delete this settlement ({chosen['from_person']} → {chosen['to_person']}, "
                           f"{money.fmt_money(chosen['amount_cents'])})"), on_click=delete_settlement, args=(chosen["id"],),
                  key="su_delete", icon=":material/delete:")
    else:
        st.caption("Click a row to delete a settlement entered by mistake.")

# ---------------------------------------------------------------- copy summary

st.subheader("Copy summary")
st.caption("Plain text for a chat. Use the copy icon at the top right of the box.")
lines = [f"Settle-up summary ({date.today():%b %d, %Y})"]
if not plan:
    lines.append("Everyone is settled up.")
for payer, receiver, cents in plan:
    lines.append(f"{'I' if payer == me else name_of[payer]} owe{'s' if payer != me else ''} "
                 f"{'me' if receiver == me else name_of[receiver]} {money.fmt_money(cents)}")
shares = d["shares"]
if len(shares):
    people_per_tx = shares.groupby("transaction_id")["person_id"].transform("nunique")
    shared = shares[(people_per_tx > 1) & (shares["share_cents"] > 0)]
    for person in others:
        rows = shared[shared["person_id"] == person["id"]].sort_values("date", ascending=False).head(10)
        if rows.empty:
            continue
        lines += ["", f"{person['name']}: last {len(rows)} shared expenses"]
        theirs = person["name"] + ("'" if person["name"].endswith("s") else "'s")
        for row in rows.itertuples():
            refund = " (refund)" if row.direction == "credit" else ""
            lines.append(f"  {row.date:%b %d}  {(row.description or '')[:24]:<24}  total {money.fmt_money(row.total_cents):>10}"
                         f"  {theirs} share {money.fmt_money(row.share_cents)}{refund}")
st.code("\n".join(lines), language=None)
