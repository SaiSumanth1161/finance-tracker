"""Excel export, and JSON export / import (SPEC sections 7.1, 7.2.3 and 7.6).

- Excel: money is stored as integer cents, and is converted to dollars only here,
  at the moment a cell is written, so Excel shows real numbers ($1,234.56).
- JSON: a full dump of every table with a schema version. It is the bridge to a
  future phone app, and also a human-readable backup.
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
from openpyxl.utils import get_column_letter

from core import analytics, db, money

MONEY_FORMAT = '$#,##0.00;[Red]-$#,##0.00'
APP_NAME = "finance-tracker"


# ------------------------------------------------------------------------ Excel

def workbook_bytes(sheets: dict[str, tuple[pd.DataFrame, list[str]]]) -> bytes:
    """Build an .xlsx file in memory.

    sheets: {sheet name: (frame, [names of the columns that hold integer cents])}
    Cents columns are written as dollars with a $ number format.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl", date_format="YYYY-MM-DD", datetime_format="YYYY-MM-DD") as writer:
        for name, (frame, cents_columns) in sheets.items():
            out = frame.copy()
            for column in cents_columns:
                if column in out:
                    out[column] = out[column].astype("int64") / 100   # display only: cents -> dollars
            out.to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]
            for position, column in enumerate(out.columns, start=1):
                cells = next(sheet.iter_cols(min_col=position, max_col=position, min_row=2), ())
                if column in cents_columns:
                    for cell in cells:
                        cell.number_format = MONEY_FORMAT
                longest = max([len(str(column))] + [len(str(v)) for v in out[column].head(200)])
                sheet.column_dimensions[get_column_letter(position)].width = min(max(10, longest + 2), 45)
            sheet.freeze_panes = "A2"
            # The app never writes formulas. A description such as "=HYPERLINK(...)" (for example from a
            # bank statement) must stay plain text, or Excel would run it when the file is opened.
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.data_type == "f":
                        cell.data_type = "s"
    return buffer.getvalue()


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """A frame as CSV. Text that a spreadsheet would read as a formula gets a leading apostrophe."""
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_object_dtype(out[column]) or pd.api.types.is_string_dtype(out[column]):
            out[column] = [f"'{v}" if isinstance(v, str) and v.startswith(_FORMULA_START) else v for v in out[column]]
    return out.to_csv(index=False).encode("utf-8")


def transaction_sheets(conn, tx_ids: list[str] | None = None) -> dict[str, tuple[pd.DataFrame, list[str]]]:
    """The four sheets of the Transactions download.

    tx_ids limits the Transactions and Shares sheets to those transactions
    (the page passes the currently filtered rows); None exports everything.
    Balances always cover all data, because who owes whom is a running total.
    """
    tx, shares = analytics.load_frames(conn)
    people = db.list_people(conn)
    names = [p["name"] for p in people]
    if tx_ids is not None:
        tx = tx[tx["id"].isin(tx_ids)]
        shares = shares[shares["transaction_id"].isin(tx_ids)]
    shown = [n for n in names if n in set(shares["person"])] or names[:1]
    wide = analytics.wide_transactions(tx, shares, shown)

    transactions = pd.DataFrame({
        "Date": wide["date"].dt.date,
        "Description": wide["description"],
        "Direction": [money.direction_label(direction, kind) for direction, kind in zip(wide["direction"], wide["category_type"])],
        "Subcategory": wide["subcategory"],
        "Category": wide["category"],
        "Type": wide["category_type"],
        "Payment mode": wide["payment_mode"],
        "Paid by": wide["paid_by"],
        **{name: wide[name] for name in shown},
        "Total": wide["total_cents"],
        "Income/Expense": wide["signed_total_cents"],
        "My share": wide["my_share_signed_cents"],
        "Split": wide["split_method"].map(money.SPLIT_LABELS),
        "Notes": wide["notes"],
    })
    long = pd.DataFrame({
        "Date": shares["date"].dt.date,
        "Description": shares["description"],
        "Person": shares["person"],
        "Share": shares["share_cents"],
        "Signed share": shares["share_signed_cents"],
        "Subcategory": shares["subcategory"],
        "Category": shares["category"],
        "Type": shares["category_type"],
        "Payment mode": shares["payment_mode"],
        "Paid by": shares["paid_by"],
    })
    categories = pd.DataFrame(
        [{"Category": s["category"], "Type": s["category_type"], "Subcategory": s["name"],
          "Active": "yes" if s["active"] else "no"} for s in db.list_subcategories(conn)],
        columns=["Category", "Type", "Subcategory", "Active"])

    name_of = {p["id"]: p["name"] for p in people}
    order = [p["id"] for p in people]
    nets = money.balances(*db.balance_inputs(conn))
    balance_rows = [{"Person": name_of[pid], "Net": nets.get(pid, 0), "Pays": "", "To": "", "Amount": None}
                    for pid in order if pid in nets]
    for payer, receiver, cents in money.settle_plan(nets, order):
        balance_rows.append({"Person": "", "Net": None, "Pays": name_of[payer], "To": name_of[receiver], "Amount": cents})
    balances = pd.DataFrame(balance_rows, columns=["Person", "Net", "Pays", "To", "Amount"])
    # Net and Amount can hold blanks, so convert them to dollars here instead of in workbook_bytes().
    for column in ("Net", "Amount"):
        balances[column] = [None if pd.isna(v) else int(v) / 100 for v in balances[column]]

    return {
        "Transactions": (transactions, shown + ["Total", "Income/Expense", "My share"]),
        "Shares": (long, ["Share", "Signed share"]),
        "Categories": (categories, []),
        "Balances": (balances, []),
    }


def transactions_workbook(conn, tx_ids: list[str] | None = None) -> bytes:
    """The Transactions page download: sheets Transactions, Shares, Categories, Balances."""
    return workbook_bytes(transaction_sheets(conn, tx_ids))


def excel_file_name(today=None) -> str:
    return f"finance_{(today or datetime.now().date()).isoformat()}.xlsx"


# ------------------------------------------------------------------------- JSON

def export_json(conn) -> dict:
    """Every table as a list of rows, plus the schema version."""
    return {
        "app": APP_NAME,
        "schema_version": int(db.get_meta(conn, "schema_version", db.SCHEMA_VERSION)),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tables": {table: db.rows(conn, f"SELECT * FROM {table} ORDER BY rowid") for table in db.TABLES},
    }


def check_json(data) -> None:
    """Raise ValueError when a file is not a dump this version of the app can read."""
    if not isinstance(data, dict) or data.get("app") != APP_NAME or not isinstance(data.get("tables"), dict):
        raise ValueError("This is not a Finance Tracker export file.")
    version = data.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise ValueError("The file has no valid schema version.")
    if version > db.SCHEMA_VERSION:
        raise ValueError(f"The file is from a newer app version (schema {version}); this app reads up to {db.SCHEMA_VERSION}.")
    missing = [table for table in db.TABLES if table not in data["tables"] and table not in db.OPTIONAL_TABLES]
    if missing:
        raise ValueError(f"The file is missing these tables: {', '.join(missing)}.")


def json_preview(data) -> dict:
    """What an import would load: {"schema_version", "exported_at", "counts": {table: rows}}."""
    check_json(data)
    return {
        "schema_version": data["schema_version"],
        "exported_at": data.get("exported_at", ""),
        "counts": {table: len(data["tables"].get(table, [])) for table in db.TABLES},
    }


def import_json(conn, data) -> dict:
    """Replace ALL data with the content of an export. Returns the row counts.

    Runs inside the caller's transaction: if anything fails, nothing is changed.
    Column names from the file are checked against the real table, so the file
    cannot inject SQL.
    """
    check_json(data)
    for table in reversed(db.TABLES):                    # children first
        conn.execute(f"DELETE FROM {table}")
    counts = {}
    for table in db.TABLES:                              # parents first
        allowed = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for row in data["tables"].get(table, []):
            unknown = set(row) - allowed
            if unknown:
                raise ValueError(f"Unknown column(s) in table {table}: {', '.join(sorted(unknown))}.")
            columns = list(row)
            marks = ", ".join("?" for _ in columns)
            conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", [row[c] for c in columns])
        counts[table] = len(data["tables"].get(table, []))
    return counts
