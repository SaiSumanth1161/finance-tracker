"""Pure money logic: parsing, formatting, splits, signs and balances.

Rules (SPEC sections 3 and 5):
- Every amount is an INTEGER number of cents. Floats are never used for money.
- No database and no Streamlit in this file, so it is easy to test and to port
  to JavaScript later. The golden cases live in tests/cases.json.

People are identified by any hashable key (the app passes person ids, the tests
pass short names like "me" and "roommate1").
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

SPLIT_METHODS = ("only_me", "equal", "exact", "percent")
SPLIT_LABELS = {"only_me": "Only me", "equal": "Equal", "exact": "Exact amounts", "percent": "Percent"}
DIRECTIONS = ("debit", "credit")
DIRECTION_LABELS = {"debit": "Money out", "credit": "Money in"}

SAVINGS_DIRECTION_LABELS = {"debit": "Deposit", "credit": "Withdraw"}


def direction_label(direction: str, category_type: str | None = None) -> str:
    """Money out / Money in, or Deposit / Withdraw for a Savings row.

    A deposit is stored as 'debit' (money leaves checking), a withdrawal as 'credit'.
    """
    labels = SAVINGS_DIRECTION_LABELS if category_type == "Savings" else DIRECTION_LABELS
    return labels[direction]


MINUS = "−"  # the real minus sign, as used in the spec: −$30.00


class MoneyError(ValueError):
    """Raised when an amount or a split is not valid. The message is shown to the user."""


# ---------------------------------------------------------------- parse / format

def parse_amount(text) -> int:
    """Turn user text such as "90", "$1,234.50" or "12.5" into integer cents.

    Raises MoneyError for empty, negative or non-numeric input.
    """
    cleaned = str(text if text is not None else "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        raise MoneyError("Enter an amount.")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise MoneyError(f"'{text}' is not a valid amount.") from None
    if not value.is_finite():
        raise MoneyError(f"'{text}' is not a valid amount.")
    if value < 0:
        raise MoneyError("Amounts cannot be negative. Use the direction (Money in / Money out) instead.")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_percent(text) -> Decimal:
    """Turn "33.33" or "50%" into a Decimal percentage (0 for blank)."""
    cleaned = str(text if text is not None else "").replace("%", "").strip()
    if not cleaned:
        return Decimal(0)
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise MoneyError(f"'{text}' is not a valid percentage.") from None
    if not value.is_finite() or value < 0:
        raise MoneyError(f"'{text}' is not a valid percentage.")
    return value


def fmt_money(cents: int, plus: bool = False) -> str:
    """Format cents as $1,234.56. Negatives use a real minus sign: −$30.00.

    plus=True also shows the + on positive amounts (used for signed columns).
    """
    cents = int(cents)
    body = f"${abs(cents) // 100:,}.{abs(cents) % 100:02d}"
    if cents < 0:
        return MINUS + body
    if plus and cents > 0:
        return "+" + body
    return body


# ------------------------------------------------------------------------ signs

def sign_for(direction: str) -> int:
    """Money in (credit) is +1, Money out (debit) is −1."""
    if direction == "credit":
        return 1
    if direction == "debit":
        return -1
    raise MoneyError(f"Unknown direction '{direction}'.")


def signed_cents(cents: int, direction: str) -> int:
    """Amount with its sign: a $90 debit is −9000, a $90 credit is +9000."""
    return int(cents) * sign_for(direction)


def saved_cents(cents: int, direction: str) -> int:
    """Savings sign rule: money OUT into savings is + saved, money IN (a withdrawal) is − saved."""
    return -signed_cents(cents, direction)


# ----------------------------------------------------------------------- splits

def _penny_order(people, payer, sort_order) -> list:
    """Who receives leftover pennies first: the payer, then people.sort_order."""
    rank = {person: i for i, person in enumerate(sort_order)}
    ordered = sorted(people, key=lambda p: (rank.get(p, len(rank)), str(p)))
    if payer in ordered:
        ordered.remove(payer)
        ordered.insert(0, payer)
    return ordered


def _hand_out_pennies(shares: dict, leftover: int, order: list) -> dict:
    """Give one extra cent to each person in `order` until `leftover` is used up."""
    i = 0
    while leftover > 0 and order:
        shares[order[i % len(order)]] += 1
        leftover -= 1
        i += 1
    return shares


def split_equal(total_cents: int, people, payer=None, sort_order=()) -> dict:
    """Equal split in cents. $100.00 / 3 -> 3334 (payer), 3333, 3333."""
    people = list(dict.fromkeys(people))  # drop duplicates, keep order
    if not people:
        raise MoneyError("Pick at least one person.")
    if total_cents <= 0:
        raise MoneyError("The total must be more than zero.")
    base, leftover = divmod(int(total_cents), len(people))
    shares = {person: base for person in people}
    return _hand_out_pennies(shares, leftover, _penny_order(people, payer, sort_order))


def split_percent(total_cents: int, percents: dict, payer=None, sort_order=()) -> dict:
    """Percent split. Percentages must sum to exactly 100; pennies follow the equal-split rule."""
    if total_cents <= 0:
        raise MoneyError("The total must be more than zero.")
    pct = {person: Decimal(str(value)) for person, value in percents.items()}
    if any(value < 0 for value in pct.values()):
        raise MoneyError("Percentages cannot be negative.")
    pct = {person: value for person, value in pct.items() if value > 0}
    if not pct:
        raise MoneyError("Pick at least one person.")
    if sum(pct.values()) != 100:
        raise MoneyError(f"Percentages must sum to 100 (now {sum(pct.values()).normalize():f}).")
    shares = {
        person: int((Decimal(int(total_cents)) * value / 100).to_integral_value(rounding=ROUND_FLOOR))
        for person, value in pct.items()
    }
    leftover = int(total_cents) - sum(shares.values())
    return _hand_out_pennies(shares, leftover, _penny_order(list(shares), payer, sort_order))


def split_exact(amounts: dict) -> tuple[int, dict]:
    """Exact split: each share is typed in, and the total is their sum. Returns (total, shares)."""
    if any(int(value) < 0 for value in amounts.values()):
        raise MoneyError("Shares cannot be negative.")
    shares = {person: int(value) for person, value in amounts.items() if int(value) > 0}
    if not shares:
        raise MoneyError("Enter at least one share.")
    return sum(shares.values()), shares


def split_only_me(total_cents: int, me) -> dict:
    """The whole amount is my share."""
    if total_cents <= 0:
        raise MoneyError("The total must be more than zero.")
    return {me: int(total_cents)}


def compute_split(method: str, *, total_cents: int = 0, people=(), payer=None, sort_order=(),
                  me=None, exact: dict | None = None, percents: dict | None = None) -> tuple[int, dict]:
    """One entry point for all four split methods. Returns (total_cents, {person: share_cents})."""
    if method == "only_me":
        shares = split_only_me(total_cents, me)
    elif method == "equal":
        shares = split_equal(total_cents, people, payer, sort_order)
    elif method == "exact":
        total_cents, shares = split_exact(exact or {})
    elif method == "percent":
        shares = split_percent(total_cents, percents or {}, payer, sort_order)
    else:
        raise MoneyError(f"Unknown split method '{method}'.")
    validate_shares(total_cents, shares)
    return int(total_cents), shares


def scale_shares(shares: dict, total_cents: int, method: str, payer=None, sort_order=()) -> dict:
    """Re-split a saved bill for a new total, keeping who is on it.

    Equal stays equal among the same people; every other method keeps each person's
    proportion of the old total. Leftover pennies follow the usual rule (payer first).
    """
    total_cents = int(total_cents)
    if total_cents <= 0:
        raise MoneyError("The total must be more than zero.")
    old_total = sum(int(v) for v in shares.values())
    if old_total <= 0:
        raise MoneyError("The saved bill has no shares.")
    if total_cents == old_total:
        return {person: int(v) for person, v in shares.items()}
    if method == "equal":
        return split_equal(total_cents, list(shares), payer, sort_order)
    scaled = {person: int(v) * total_cents // old_total for person, v in shares.items()}
    leftover = total_cents - sum(scaled.values())
    return _hand_out_pennies(scaled, leftover, _penny_order(list(scaled), payer, sort_order))


def validate_shares(total_cents: int, shares: dict) -> None:
    """The rules every saved transaction must pass. Raises MoneyError if one is broken."""
    if not shares:
        raise MoneyError("Pick at least one person.")
    if any(int(value) < 0 for value in shares.values()):
        raise MoneyError("Shares cannot be negative.")
    if int(total_cents) <= 0:
        raise MoneyError("The total must be more than zero.")
    if sum(int(value) for value in shares.values()) != int(total_cents):
        raise MoneyError("The shares do not add up to the total.")


# --------------------------------------------------------------------- balances
# A positive net means "is owed money"; a negative net means "owes money".

def transaction_nets(total_cents: int, payer, shares: dict, direction: str) -> dict:
    """Who is up or down after one transaction.

    net = (total if they are the payer else 0) − their share.
    Example: I pay $90 groceries split 3 ways -> me +6000, each roommate −3000.
    For Money in (e.g. a refund I received and still have to pass on) the result flips.
    """
    flip = -sign_for(direction)  # debit -> +1, credit -> −1 (the $90 example fixes this)
    nets = {}
    for person in list(shares) + [payer]:
        paid = int(total_cents) if person == payer else 0
        nets[person] = (paid - int(shares.get(person, 0))) * flip
    return nets


def balances(transactions, settlements=()) -> dict:
    """Net per person over many transactions and settlements.

    transactions: dicts with total_cents, payer, shares {person: cents}, direction
    settlements:  dicts with from, to, amount_cents (A pays B: A's net goes up, B's goes down)
    """
    nets: dict = {}
    for tx in transactions:
        for person, value in transaction_nets(tx["total_cents"], tx["payer"], tx["shares"], tx["direction"]).items():
            nets[person] = nets.get(person, 0) + value
    for s in settlements:
        nets[s["from"]] = nets.get(s["from"], 0) + int(s["amount_cents"])
        nets[s["to"]] = nets.get(s["to"], 0) - int(s["amount_cents"])
    return nets


def settle_plan(nets: dict, sort_order=()) -> list[tuple]:
    """Greedy minimum transfers: a list of (from_person, to_person, cents) that clears all nets."""
    rank = {person: i for i, person in enumerate(sort_order)}
    key = lambda item: (-item[1], rank.get(item[0], len(rank)), str(item[0]))  # biggest first, then sort order
    debtors = sorted(([p, -n] for p, n in nets.items() if n < 0), key=key)
    creditors = sorted(([p, n] for p, n in nets.items() if n > 0), key=key)
    plan = []
    while debtors and creditors:
        debtor, creditor = debtors[0], creditors[0]
        amount = min(debtor[1], creditor[1])
        plan.append((debtor[0], creditor[0], amount))
        debtor[1] -= amount
        creditor[1] -= amount
        debtors = sorted((d for d in debtors if d[1] > 0), key=key)
        creditors = sorted((c for c in creditors if c[1] > 0), key=key)
    return plan


# ------------------------------------------------------------------ KPI formulas
# Inputs are signed cents: income is positive, expense is negative.

def net_cents(income_cents: int, expense_cents: int) -> int:
    """Net = income + expense (expense is negative)."""
    return int(income_cents) + int(expense_cents)


def split_kpi_cents(household_expense_cents: int, my_share_expense_cents: int) -> int:
    """Split = the part of the bills that others covered, shown as a positive number.

    Household expense −9000 with my share −3000 -> 6000.
    """
    return int(my_share_expense_cents) - int(household_expense_cents)


def savings_rate(net: int, income: int) -> float | None:
    """Net ÷ income as a ratio (0.25 = 25%). None when there is no income. Not money, so a float is fine."""
    return None if not income else net / income


def unallocated_cents(net: int, net_saved: int) -> int:
    """Cash left in checking that was not moved anywhere: Net − Saved."""
    return int(net) - int(net_saved)
