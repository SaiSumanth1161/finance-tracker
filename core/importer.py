"""One-time import of the old spreadsheet (SPEC phase 4). Pure logic, no Streamlit.

Privacy: the workbook is read from the bytes the user uploads in the app, in memory.
Nothing is written to disk, and problem messages name the Excel row and column only,
never a cell value.

Two sheet layouts are understood (the column names are only defaults; the Import page
lets the user map any column):

  Transactions sheet: Bill, Date, Credit Total, Debit Total, one column per person,
                      Sub Category, Category, Category Type, Mode
  Savings sheet:      Item, Date, Amount, Sub Category Type2, Category Type2
  Bank statement:     Date, Description, Amount (or separate Debit / Credit columns), from a
                      CSV or Excel export. It has no categories, so description rules
                      ("contains COSTCO" -> groceries) choose the subcategory; rows no rule
                      matches go to a default subcategory. Every row is "only me".

  Categories sheet:   up to two tables side by side, each with a category, a subcategory
                      and a category type column. It creates master rows only, no transactions.

The header does not have to be on row 1: detect_header_row() finds it.

Flow: read_raw() -> detect_header_row() -> frame_from_raw() -> guess_mapping()
      -> build_rows() -> review() -> apply()          (transactions, savings, bank)
      -> build_categories() -> review_categories() -> apply_categories()   (categories)
"""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import pandas as pd

from core import db

TRANSACTIONS, SAVINGS, BANK, CATEGORIES = "Transactions", "Savings", "Bank statement", "Categories"
META_LAST_IMPORT = "last_import_transaction_ids"

# Field -> header names to look for (lower case), used to pre-fill the mapping screen.
_GUESSES = {
    TRANSACTIONS: {
        "description": ["bill", "description", "item"], "date": ["date"],
        "credit": ["credit total", "credit"], "debit": ["debit total", "debit"],
        "subcategory": ["sub category", "subcategory"], "category": ["category"],
        "category_type": ["category type"], "mode": ["mode", "payment mode"],
    },
    SAVINGS: {
        "description": ["item", "description"], "date": ["date"], "amount": ["amount"],
        "subcategory": ["sub category type2", "sub category", "subcategory"],
        "category": ["category type2", "category"],
    },
    CATEGORIES: {       # table 1 (income / expense) and table 2 (savings) of the old Category sheet
        "category": ["category"], "subcategory": ["subcategory", "sub category"], "category_type": ["category type"],
        "category2": ["category type 2", "category type2", "category 2"],
        "subcategory2": ["subcategory type 2", "sub category type 2", "sub category type2", "subcategory type2"],
        "category_type2": ["category type 3", "category type3"],
    },
    BANK: {
        "description": ["description", "memo", "details", "payee", "name"],
        "date": ["date", "transaction date", "posting date", "post date"], "amount": ["amount"],
        "debit": ["debit", "withdrawal", "withdrawals"], "credit": ["credit", "deposit", "deposits"],
    },
}
# Columns of the old sheet that are totals or formulas, never a person's share.
_NOT_PEOPLE = {"total again", "total", "income/expense total", "income/expense mine"}
_TYPES = {"income": "Income", "expense": "Expense", "expenses": "Expense", "savings": "Savings",
          "saving": "Savings", "not reported": "Not Reported", "not-reported": "Not Reported"}


# ---------------------------------------------------------------------- reading

def sheet_names(data: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(data)).sheet_names


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == ""


def read_raw(data: bytes, sheet: str) -> pd.DataFrame:
    """One sheet exactly as it is, without deciding where the header is. Row i is Excel row i + 1."""
    return pd.read_excel(io.BytesIO(data), sheet_name=sheet, header=None, dtype=object)


_KNOWN_HEADERS = {name for fields in _GUESSES.values() for names in fields.values() for name in names}


def detect_header_row(raw: pd.DataFrame, scan: int = 50) -> int | None:
    """The Excel row number that holds the column names (None when no row looks like a header).

    Sheets often start with a title or empty rows. The header is the row, among the first
    `scan`, with the most cells that are known column names (Date, Amount, Sub Category, ...).
    When no row has two of those, it is the first row with at least two cells that are all text.
    """
    best_row, best_hits, first_text_row = None, 0, None
    for index in range(min(len(raw), scan)):
        cells = [v for v in raw.iloc[index].tolist() if not _blank(v)]
        hits = sum(str(v).strip().lower() in _KNOWN_HEADERS for v in cells)
        if hits > best_hits:
            best_row, best_hits = index + 1, hits
        if first_text_row is None and len(cells) >= 2 and all(isinstance(v, str) for v in cells):
            first_text_row = index + 1
    return best_row if best_hits >= 2 else first_text_row


def frame_from_raw(raw: pd.DataFrame, header_row: int) -> pd.DataFrame:
    """Cut a raw sheet into a table: the names on `header_row`, the data below it.

    Columns without a name are dropped (empty columns beside or between tables), and a
    repeated name gets " (2)", " (3)" so every column can be mapped.
    """
    if header_row < 1 or header_row > len(raw):
        frame = pd.DataFrame()
        frame.attrs["first_data_row"] = header_row + 1
        return frame
    names, keep, seen = [], [], {}
    for position, value in enumerate(raw.iloc[header_row - 1].tolist()):
        if _blank(value):
            continue
        name = str(value).strip()
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
        keep.append(position)
    frame = raw.iloc[header_row:, keep].reset_index(drop=True)
    frame.columns = names
    frame.attrs["first_data_row"] = header_row + 1      # Excel row number of the first data row
    return frame


def read_sheet(data: bytes, sheet: str, header_row: int | None = None) -> pd.DataFrame:
    """One sheet as a table. header_row is the Excel row with the column names (None = find it)."""
    raw = read_raw(data, sheet)
    return frame_from_raw(raw, header_row or detect_header_row(raw) or 1)


def read_csv(data: bytes) -> pd.DataFrame:
    """A bank CSV export as a frame (everything is read as text; amounts are parsed later)."""
    frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding_errors="replace")
    frame.columns = [str(c).strip() for c in frame.columns]
    frame.attrs["first_data_row"] = 2
    return frame


def guess_mapping(columns: list[str], kind: str, known_people: list[str]) -> dict:
    """Pre-fill the mapping from the header names. Returns {field: column or None, "people": {column: person}}."""
    lower = {c.lower(): c for c in columns}
    mapping = {field: next((lower[name] for name in names if name in lower), None)
               for field, names in _GUESSES[kind].items()}
    if kind == TRANSACTIONS:
        used = {c for c in mapping.values() if c}
        # Person columns sit between Debit Total and Sub Category in the old sheet.
        start = columns.index(mapping["debit"]) + 1 if mapping.get("debit") in columns else 0
        end = columns.index(mapping["subcategory"]) if mapping.get("subcategory") in columns else len(columns)
        known = {name.lower(): name for name in known_people}
        people = {column: known.get(column.lower(), column) for column in columns[start:end]
                  if column not in used and column.lower() not in _NOT_PEOPLE}
        mapping["people"] = people
    return mapping


# ---------------------------------------------------------------- row conversion

def _cents(value) -> int | None:
    """A cell as whole cents (absolute value kept signed). None for a blank cell."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError("is not a number") from None
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _day(value) -> date:
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        raise ValueError("is empty")
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(str(value)).date()
    except Exception:
        raise ValueError("is not a date") from None


def _text(row, column) -> str:
    value = row.get(column) if column else None
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()


def match_rule(description: str, rules: list[dict]) -> str | None:
    """The subcategory name of the first rule whose pattern is inside the description (any case)."""
    text = (description or "").lower()
    for rule in rules:
        if rule["pattern"].lower() in text:
            return rule["subcategory"]
    return None


def build_rows(frame: pd.DataFrame, mapping: dict, kind: str, me: str, positive_is_deposit: bool = True, *,
               rules: list[dict] | None = None, subcategories: list[dict] | None = None,
               default_subcategory: str | None = None, negative_is_out: bool = True) -> tuple[list[dict], list[str]]:
    """Turn sheet rows into import rows.

    Returns (rows, problems). A row is a dict with: row (Excel row number), date, description,
    direction, shares {person name: cents}, total_cents, split_method, subcategory, category,
    category_type, mode, and matched (False when a bank row fell back to the default subcategory).
    Rows that cannot be read are left out and described in `problems` by row number and
    column name only.

    Transactions: Money in when Credit Total is filled, otherwise Money out. Shares come from
    the person columns; a row without any share is treated as "only me".
    Savings: a positive Amount is a deposit (flip with positive_is_deposit=False).
    Bank statement: a negative Amount is money out (flip with negative_is_out=False), or use
    separate debit / credit columns. `rules` (dicts with pattern and subcategory) pick the
    subcategory; `subcategories` are the app's subcategories (name, category, category_type).
    """
    rows, problems = [], []
    first = frame.attrs.get("first_data_row", 2)
    known = {s["name"].lower(): s for s in (subcategories or [])}
    for offset, record in enumerate(frame.to_dict("records")):
        number = first + offset
        if all(_text(record, c) == "" for c in frame.columns):
            continue                                       # a completely empty line
        matched = True
        try:
            column = mapping["date"]
            day = _day(record.get(column))
            description = _text(record, mapping.get("description"))
            if kind == SAVINGS:
                column = mapping["amount"]
                amount = _cents(record.get(column))
                if not amount:
                    raise ValueError("is empty or zero")
                deposit = (amount > 0) == positive_is_deposit
                direction, shares = ("debit" if deposit else "credit"), {me: abs(amount)}
                category_type, mode = "Savings", ""
            elif kind == BANK:
                if mapping.get("amount"):
                    column = mapping["amount"]
                    amount = _cents(record.get(column))
                    if not amount:
                        raise ValueError("is empty or zero")
                    out = (amount < 0) == negative_is_out
                else:
                    column = "debit / credit"
                    credit = abs(_cents(record.get(mapping["credit"])) or 0) if mapping.get("credit") else 0
                    debit = abs(_cents(record.get(mapping["debit"])) or 0) if mapping.get("debit") else 0
                    amount = credit or debit
                    if not amount:
                        raise ValueError("are both empty")
                    out = not (credit and not debit)
                direction, shares, mode = ("debit" if out else "credit"), {me: abs(amount)}, ""
                name = match_rule(description, rules or [])
                matched = name is not None
                chosen = known.get((name or default_subcategory or "").lower())
                column = "subcategory"
                if chosen is None:
                    raise ValueError("could not be chosen: pick a default subcategory")
                subcategory, category, category_type = chosen["name"], chosen["category"], chosen["category_type"]
            else:
                column = mapping["credit"]
                credit = abs(_cents(record.get(column)) or 0) if column else 0
                column = mapping["debit"]
                debit = abs(_cents(record.get(column)) or 0) if column else 0
                direction = "credit" if credit and not debit else "debit"
                stated = credit or debit
                shares = {}
                for column, person in mapping.get("people", {}).items():
                    cents = abs(_cents(record.get(column)) or 0)
                    if cents:
                        shares[person] = shares.get(person, 0) + cents
                if not shares:
                    column = "Credit Total / Debit Total"
                    if not stated:
                        raise ValueError("are both empty and no person has a share")
                    shares = {me: stated}
                raw = _text(record, mapping.get("category_type"))
                category_type = _TYPES.get(raw.lower(), raw)
                mode = _text(record, mapping.get("mode"))
            if kind != BANK:
                column = mapping["subcategory"]
                subcategory = _text(record, column)
                if not subcategory:
                    raise ValueError("is empty")
                category = _text(record, mapping.get("category")) or subcategory
        except ValueError as problem:
            problems.append(f"Row {number}: column '{column}' {problem}. Row skipped.")
            continue
        rows.append({
            "row": number, "date": day, "description": description,
            "direction": direction, "shares": shares, "total_cents": sum(shares.values()),
            "split_method": "only_me" if set(shares) == {me} else "exact",
            "subcategory": subcategory, "category": category,
            "category_type": category_type, "mode": mode, "matched": matched,
            "stated_cents": stated if kind == TRANSACTIONS else None,
        })
    return rows, problems


def total_mismatches(rows: list[dict]) -> list[dict]:
    """Transactions rows whose Credit / Debit Total differs from the sum of the person columns.

    The app stores the sum of the person shares as the total, so these rows add up differently
    here than in a sheet that sums the Total columns. Returns Excel row, both amounts and the gap.
    """
    found = []
    for row in rows:
        stated = row.get("stated_cents")
        if stated and stated != row["total_cents"]:
            found.append({"row": row["row"], "stated_cents": stated, "shares_cents": row["total_cents"],
                          "gap_cents": row["total_cents"] - stated, "direction": row["direction"],
                          "category_type": row["category_type"]})
    return found


# ----------------------------------------------------------------------- coverage

def coverage(frame: pd.DataFrame, rows: list[dict], problems: list[str]) -> dict:
    """Where every Excel row of the table went: ready, skipped (problem) or blank.

    Only row numbers are reported, never cell values, so it can be shown or shared safely.
    Returns first / last Excel row of the table and `status`, {excel row: "ready" | "skipped" | "blank"}.
    """
    first = frame.attrs.get("first_data_row", 2)
    status = {first + i: "blank" for i in range(len(frame))}
    for problem in problems:
        match = re.match(r"Row (\d+):", problem)
        if match and int(match.group(1)) in status:
            status[int(match.group(1))] = "skipped"
    for row in rows:
        status[row["row"]] = "ready"
    return {"first": first, "last": first + len(frame) - 1 if len(frame) else first - 1, "status": status}


def rows_in_range(cover: dict, start: int, end: int) -> dict:
    """Counts for Excel rows start..end, plus the row numbers that were not ready."""
    counts = {"ready": 0, "skipped": 0, "blank": 0, "outside": 0}
    not_ready = []
    for number in range(start, end + 1):
        state = cover["status"].get(number)
        if state is None:
            counts["outside"] += 1
            not_ready.append((number, "outside the table"))
            continue
        counts[state] += 1
        if state != "ready":
            not_ready.append((number, "blank line" if state == "blank" else "skipped (problem)"))
    return {"counts": counts, "not_ready": not_ready}


# ----------------------------------------------------------------------- review

def _key(row_date, description, direction, total) -> tuple:
    return (str(row_date), (description or "").strip().lower(), direction, int(total))


def review(conn, rows: list[dict]) -> dict:
    """What an import would do, before anything is written.

    Returns new_people, new_modes, unknown_subcategories (one dict per name with the sheet's
    category and type), bad_types (type texts the app does not know), duplicates (rows already
    in the database: same date, description, direction and total) and totals in cents.
    """
    people = {p["name"].lower() for p in db.list_people(conn)}
    modes = {m["name"].lower() for m in db.list_payment_modes(conn)}
    subs = {s["name"].lower() for s in db.list_subcategories(conn)}
    existing = {_key(r["date"], r["description"], r["direction"], r["total_cents"])
                for r in conn.execute("SELECT date, description, direction, total_cents FROM transactions")}
    unknown: dict[str, dict] = {}
    for row in rows:
        if row["subcategory"].lower() not in subs:
            unknown.setdefault(row["subcategory"].lower(), {
                "subcategory": row["subcategory"], "category": row["category"],
                "category_type": row["category_type"], "rows": 0})["rows"] += 1
    signed = lambda r: r["total_cents"] * (1 if r["direction"] == "credit" else -1)   # noqa: E731
    by_type = {}
    for row in rows:
        by_type[row["category_type"]] = by_type.get(row["category_type"], 0) + signed(row)
    return {
        "rows": len(rows),
        "no_mode": sum(not r["mode"] for r in rows),
        "unmatched": sum(not r.get("matched", True) for r in rows),
        "new_people": sorted({n for r in rows for n in r["shares"] if n.lower() not in people}),
        "new_modes": sorted({r["mode"] for r in rows if r["mode"] and r["mode"].lower() not in modes}),
        "unknown_subcategories": list(unknown.values()),
        "bad_types": sorted({r["category_type"] for r in rows if r["category_type"] not in db.CATEGORY_TYPES}),
        "duplicates": sum(_key(r["date"], r["description"], r["direction"], r["total_cents"]) in existing for r in rows),
        "signed_total_by_type": by_type,
    }


# ------------------------------------------------------------------------ apply

def apply(conn, rows: list[dict], *, subcategory_map: dict[str, str] | None = None,
          default_mode_id: str | None = None, skip_duplicates: bool = True) -> dict:
    """Write the rows. Runs inside the caller's transaction (all or nothing).

    subcategory_map: {sheet subcategory (lower case): existing subcategory id} for names the user
                     mapped by hand. Unmapped unknown names are created, with their category.
    default_mode_id: payment mode for rows without one (the Savings sheet has no Mode column).
    Missing people and payment modes are created. Returns counts, and remembers the new
    transaction ids so undo_last() can remove exactly this import.
    """
    subcategory_map = {k.lower(): v for k, v in (subcategory_map or {}).items()}
    people = {p["name"].lower(): p["id"] for p in db.list_people(conn)}
    modes = {m["name"].lower(): m["id"] for m in db.list_payment_modes(conn)}
    subs = {s["name"].lower(): s["id"] for s in db.list_subcategories(conn)}
    categories = {c["name"].lower(): c["id"] for c in db.list_categories(conn)}
    types = {t["name"]: t["id"] for t in db.list_category_types(conn)}
    me = db.me_id(conn)
    existing = {_key(r["date"], r["description"], r["direction"], r["total_cents"])
                for r in conn.execute("SELECT date, description, direction, total_cents FROM transactions")}
    created = {"people": 0, "modes": 0, "categories": 0, "subcategories": 0}
    ids, skipped = [], 0

    for row in rows:
        if skip_duplicates and _key(row["date"], row["description"], row["direction"], row["total_cents"]) in existing:
            skipped += 1
            continue
        if row["category_type"] not in types:
            raise ValueError(f"Row {row['row']}: unknown category type. Fix the type column or map the subcategory.")
        name = row["subcategory"].lower()
        sub_id = subcategory_map.get(name) or subs.get(name)
        if sub_id is None:                                 # create the subcategory (and its category)
            category_id = categories.get(row["category"].lower())
            if category_id is None:
                category_id = db.save_master_row(conn, "categories", {
                    "name": row["category"], "category_type_id": types[row["category_type"]]})
                categories[row["category"].lower()] = category_id
                created["categories"] += 1
            sub_id = db.save_master_row(conn, "subcategories", {"name": row["subcategory"], "category_id": category_id, "active": 1})
            subs[name] = sub_id
            created["subcategories"] += 1
        mode_id = modes.get(row["mode"].lower()) if row["mode"] else default_mode_id
        if mode_id is None:
            if not row["mode"]:
                raise ValueError("Pick a default payment mode for rows without one.")
            mode_id = db.save_master_row(conn, "payment_modes", {
                "name": row["mode"], "kind": "Credit card", "active": 1, "sort_order": len(modes) + 1})
            modes[row["mode"].lower()] = mode_id
            created["modes"] += 1
        shares = {}
        for person, cents in row["shares"].items():
            if person.lower() not in people:
                people[person.lower()] = db.save_master_row(conn, "people", {
                    "name": person, "active": 1, "sort_order": len(people) + 1})
                created["people"] += 1
            shares[people[person.lower()]] = cents
        ids.append(db.save_transaction(conn, {
            "date": row["date"], "description": row["description"], "direction": row["direction"],
            "subcategory_id": sub_id, "payment_mode_id": mode_id, "paid_by_person_id": me,
            "split_method": row["split_method"], "notes": "imported"}, shares))
    db.set_meta(conn, META_LAST_IMPORT, json.dumps(ids))
    return {"imported": len(ids), "skipped_duplicates": skipped, "created": created}


def last_import_count(conn) -> int:
    return len(json.loads(db.get_meta(conn, META_LAST_IMPORT, "[]")))


def undo_last(conn) -> int:
    """Delete the transactions of the most recent import (master rows it created are kept)."""
    ids = json.loads(db.get_meta(conn, META_LAST_IMPORT, "[]"))
    for tx_id in ids:
        db.delete_transaction(conn, tx_id)
    db.set_meta(conn, META_LAST_IMPORT, "[]")
    return len(ids)


# ------------------------------------------------------------------- categories
# The Category sheet only creates master rows (categories and subcategories).

def build_categories(frame: pd.DataFrame, mapping: dict, default_type2: str = "Savings",
                     fill_down: bool = True) -> tuple[list[dict], list[str]]:
    """Read the category list. Returns (items, problems).

    mapping: category / subcategory / category_type for table 1, and category2 / subcategory2 /
             category_type2 for the optional second table (savings). Unmapped tables are skipped.
    default_type2: type for table 2 when it has no type column.
    fill_down: an empty category or type cell repeats the value above it (as in a grouped list).
    An item is {row, category, subcategory ("" = category only), category_type}.
    """
    items, problems, seen = [], [], set()
    first = frame.attrs.get("first_data_row", 2)
    tables = [(mapping.get("category"), mapping.get("subcategory"), mapping.get("category_type"), ""),
              (mapping.get("category2"), mapping.get("subcategory2"), mapping.get("category_type2"), default_type2)]
    for category_column, sub_column, type_column, default_type in tables:
        if not category_column:
            continue
        last_category = last_type = ""
        for offset, record in enumerate(frame.to_dict("records")):
            number = first + offset
            category, sub = _text(record, category_column), _text(record, sub_column)
            raw_type = _text(record, type_column)
            if not category and not sub:
                continue
            if fill_down:
                category = category or last_category
                raw_type = raw_type or (last_type if category == last_category else "")
            if not category:
                problems.append(f"Row {number}: column '{category_column}' is empty. Row skipped.")
                continue
            kind = _TYPES.get(raw_type.lower(), raw_type) or default_type
            if not kind:
                problems.append(f"Row {number}: column '{type_column or 'category type'}' is empty. Row skipped.")
                continue
            last_category, last_type = category, raw_type
            key = (category.lower(), sub.lower())
            if key not in seen:
                seen.add(key)
                items.append({"row": number, "category": category, "subcategory": sub, "category_type": kind})
    return items, problems


def review_categories(conn, items: list[dict]) -> dict:
    """What a category import would do. Each item gets a 'status':

    "new"                       will be created
    "already there"             nothing to do
    "exists under <category>"   the subcategory name is taken by another category; left where it is
    "category is <type>"        the category exists with another type; its type is left unchanged
    "unknown type"              the type is not Income, Expense, Savings or Not Reported; skipped
    """
    categories = {c["name"].lower(): c for c in db.list_categories(conn)}
    subs = {s["name"].lower(): s["category"] for s in db.list_subcategories(conn)}
    new_categories, new_subs, planned_types, out = set(), 0, {}, []
    for item in items:
        name, sub = item["category"].lower(), item["subcategory"].lower()
        existing = categories.get(name)
        if item["category_type"] not in db.CATEGORY_TYPES:
            status = "unknown type"
        elif existing and existing["category_type"] != item["category_type"]:
            status = f"category is {existing['category_type']}"
        elif planned_types.setdefault(name, item["category_type"]) != item["category_type"]:
            status = f"category is {planned_types[name]}"
        elif sub and sub in subs and subs[sub].lower() != name:
            status = f"exists under {subs[sub]}"
        elif (sub and sub in subs) or (not sub and existing):
            status = "already there"
        else:
            status = "new"
            if not existing:
                new_categories.add(name)
            if sub:
                new_subs += 1
                subs[sub] = item["category"]
        out.append({**item, "status": status})
    return {
        "items": out, "new_categories": len(new_categories), "new_subcategories": new_subs,
        "already_there": sum(i["status"] == "already there" for i in out),
        "conflicts": sum(i["status"] not in ("new", "already there") for i in out),
    }


def apply_categories(conn, items: list[dict]) -> dict:
    """Create the missing categories and subcategories. Existing rows are never changed.

    Runs inside the caller's transaction. Returns {"categories": n, "subcategories": n} created.
    """
    types = {t["name"]: t["id"] for t in db.list_category_types(conn)}
    categories = {c["name"].lower(): c["id"] for c in db.list_categories(conn)}
    subs = {s["name"].lower() for s in db.list_subcategories(conn)}
    created = {"categories": 0, "subcategories": 0}
    for item in review_categories(conn, items)["items"]:
        if item["status"] != "new":
            continue
        name = item["category"].lower()
        if name not in categories:
            categories[name] = db.save_master_row(conn, "categories", {
                "name": item["category"], "category_type_id": types[item["category_type"]]})
            created["categories"] += 1
        if item["subcategory"] and item["subcategory"].lower() not in subs:
            db.save_master_row(conn, "subcategories", {
                "name": item["subcategory"], "category_id": categories[name], "active": 1})
            subs.add(item["subcategory"].lower())
            created["subcategories"] += 1
    return created
