"""Fake sample data for trying the app (SPEC section 4: "Load demo data" / "Clear demo data").

About 150 made-up transactions over the last 6 months, plus a few settlements.
The ids of everything created here are remembered in app_meta, so "Clear demo data"
removes exactly these rows and never touches real entries.

The generator is deterministic (fixed random seed), so tests can rely on it.
"""
from __future__ import annotations

import json
import random
from datetime import date

from core import db, money

META_TX = "demo_transaction_ids"
META_SETTLEMENTS = "demo_settlement_ids"


def has_demo(conn) -> bool:
    return bool(json.loads(db.get_meta(conn, META_TX, "[]")))


def _month_starts(today: date, months: int) -> list[date]:
    """First day of each of the last `months` months, oldest first, ending with the current month."""
    starts, year, month = [], today.year, today.month
    for _ in range(months):
        starts.append(date(year, month, 1))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return list(reversed(starts))


def load_demo(conn, today: date | None = None, seed: int = 42) -> int:
    """Create the demo rows. Returns how many transactions were added (0 if demo data is already loaded)."""
    if has_demo(conn):
        return 0
    today = today or date.today()
    rng = random.Random(seed)

    people = {p["name"]: p["id"] for p in db.list_people(conn)}
    order = [p["id"] for p in db.list_people(conn)]
    modes = {m["name"]: m["id"] for m in db.list_payment_modes(conn)}
    subs = {s["name"]: s["id"] for s in db.list_subcategories(conn)}
    me = db.me_id(conn)
    # Roommates and "outside the flat" person, by seed name with a fallback to whoever exists.
    others = [pid for pid in order if pid != me]
    roommate1 = people.get("Roommate 1", others[0] if others else me)
    roommate2 = people.get("Roommate 2", others[1] if len(others) > 1 else roommate1)
    outside = people.get("Others", others[-1] if others else me)
    flat = list(dict.fromkeys([me, roommate1, roommate2]))
    any_mode = next(iter(modes.values()))

    tx_ids: list[str] = []
    ledger: list[tuple[date, dict]] = []  # (day, balance input) used to compute realistic settlements

    def add(day: date, description: str, sub: str, cents: int, mode: str, *, direction="debit",
            method="only_me", who=None, payer=None, percents=None, exact=None):
        """Add one transaction (skipped when it is in the future or its subcategory was deleted)."""
        if day > today or sub not in subs:
            return
        payer = payer or me
        total, shares = money.compute_split(method, total_cents=cents, people=who or [], payer=payer,
                                            sort_order=order, me=me, percents=percents, exact=exact)
        tx = {"date": day, "description": description, "direction": direction, "subcategory_id": subs[sub],
              "payment_mode_id": modes.get(mode, any_mode), "paid_by_person_id": payer,
              "split_method": method, "notes": ""}
        tx_ids.append(db.save_transaction(conn, tx, shares))
        ledger.append((day, {"total_cents": total, "payer": payer, "shares": shares, "direction": direction}))

    def dollars(low: int, high: int) -> int:
        """A random amount between low and high dollars, in cents."""
        return rng.randint(low * 100, high * 100)

    groceries = ["Costco", "Trader Joe's", "Patel Brothers", "Walmart", "Whole Foods"]
    food = ["Chipotle", "Biryani Pot", "Starbucks", "Thai Basil", "Pizza night", "Dosa Corner"]

    for i, start in enumerate(_month_starts(today, 6)):
        day = lambda n: start.replace(day=n)  # noqa: E731 - tiny helper
        # Income
        add(day(1), "Paycheck", "salary", 385000, "Zelle", direction="credit")
        add(day(15), "Paycheck", "salary", 385000, "Zelle", direction="credit")
        # Home bills, shared with the flat
        add(day(1), "Rent", "rent", 270000, "Zelle", method="equal", who=flat)
        add(day(5), "Family phone plan", "phone", 12000, "visa", method="equal", who=flat + [outside])
        add(day(8), "Xfinity", "internet", 7500, "checking", method="equal", who=flat)
        add(day(12), "Electricity", "electricity", dollars(80, 160), "checking", method="equal", who=flat)
        if i % 2 == 0:
            add(day(20), "Laundry card", "washer dryer", 4500, "mastercard", method="equal", who=flat)
        for n in rng.sample(range(2, 28), 3):
            payer = roommate1 if rng.random() < 0.15 else me
            add(day(n), rng.choice(groceries), "groceries", dollars(40, 180), "mastercard",
                method="equal", who=flat, payer=payer)
        # Car
        add(day(3), "Car loan", "car loan", 41250, "Zelle")
        add(day(18), "Geico", "insurance", 14820, "checking")
        add(day(rng.randint(2, 27)), "EZ-Pass", "tolls", dollars(5, 20), "checking")
        add(day(rng.randint(2, 27)), "EV charging", "charging", dollars(8, 25), "visa")
        # Personal
        add(day(10), "Netflix", "subscriptions", 1599, "visa")
        for n in rng.sample(range(2, 28), 3):
            if rng.random() < 0.25:
                add(day(n), rng.choice(food), "outside food", dollars(30, 80), "visa", method="equal", who=[me, roommate1])
            else:
                add(day(n), rng.choice(food), "outside food", dollars(12, 45), "visa")
        add(day(rng.randint(2, 27)), "AMC", "movies", dollars(15, 40), "mastercard",
            method="equal" if i % 2 else "only_me", who=flat)
        add(day(rng.randint(2, 27)), "Amazon", "misc-expense", dollars(10, 90), "debit card")
        # Savings (money out into savings counts as + saved)
        add(day(2), "Gold SIP", "gold", 20000, "Zelle")
        add(day(6), "401k contribution", "401k", 50000, "Zelle")
        add(day(16), "Brokerage deposit", "stocks", 30000, "Zelle")
        add(day(17), "HYSA", "high-yield savings", 40000, "Zelle")
        # Not reported (transfer between my own accounts)
        if i % 2 == 1:
            add(day(21), "Checking to savings acct", "bank transfer", 100000, "Zelle")
        # One-off items, spread over the months
        if i == 1:
            add(day(14), "Flight to Chicago", "travel", 28640, "mastercard")
            add(day(24), "Costco", "groceries", 2499, "mastercard", direction="credit", method="equal", who=flat)  # refund
        if i == 2:
            add(day(9), "Airbnb Lake Tahoe", "vacation-misc", 64000, "mastercard", method="percent",
                percents={me: 50, roommate1: 25, roommate2: 25})
            add(day(11), "Tahoe ski rental", "vacation-misc", 18000, "visa",
                method="exact", exact={me: 9000, roommate1: 6000, roommate2: 3000})
        if i == 3:
            add(day(19), "HYSA withdrawal", "high-yield savings", 100000, "Zelle", direction="credit")
            add(day(23), "Sold old bike", "misc-income", 12000, "Cash", direction="credit")
        if i == 4:
            add(day(7), "Train to NYC", "travel", 9650, "visa")
            add(day(13), "Amazon", "misc-expense", 3299, "debit card", direction="credit")  # refund
            add(day(26), "Tax refund", "misc-income", 23000, "Zelle", direction="credit")

    # Settlements: for the first four months each roommate pays back what they owe at month end.
    settlement_ids: list[str] = []
    paid_so_far: list[dict] = []
    for start in _month_starts(today, 6)[:4]:
        settle_day = start.replace(day=28)
        nets = money.balances([entry for day_, entry in ledger if day_ <= settle_day], paid_so_far)
        for roommate in (roommate1, roommate2, outside):
            owed = -nets.get(roommate, 0)
            if roommate != me and owed > 0 and settle_day <= today:
                settlement_ids.append(db.add_settlement(conn, settle_day, roommate, me, owed, modes.get("Zelle"), "demo"))
                paid_so_far.append({"from": roommate, "to": me, "amount_cents": owed})

    db.set_meta(conn, META_TX, json.dumps(tx_ids))
    db.set_meta(conn, META_SETTLEMENTS, json.dumps(settlement_ids))
    return len(tx_ids)


def clear_demo(conn) -> int:
    """Delete the demo rows only. Returns how many transactions were removed."""
    tx_ids = json.loads(db.get_meta(conn, META_TX, "[]"))
    settlement_ids = json.loads(db.get_meta(conn, META_SETTLEMENTS, "[]"))
    for tx_id in tx_ids:
        db.delete_transaction(conn, tx_id)
    for settlement_id in settlement_ids:
        db.delete_settlement(conn, settlement_id)
    db.set_meta(conn, META_TX, "[]")
    db.set_meta(conn, META_SETTLEMENTS, "[]")
    return len(tx_ids)
