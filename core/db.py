"""SQLite storage: schema, migrations, seed data, SQL views and every write.

Rules (SPEC sections 2 and 4):
- The database lives at ./data/finance.db (override with the FINANCE_DB env var, used by tests).
- Money is stored as integer cents. Primary keys are UUID text.
- Every table has created_at and updated_at.
- Only parameterized SQL (never build SQL from user text).
- Derived values are never stored: they come from the views v_transactions and v_shares.

How to change the schema later: add a new entry to MIGRATIONS with the next
number and bump SCHEMA_VERSION. init_db() runs the missing ones in order.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from core import money

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 2

# Tables in an order that respects foreign keys (parents first). Used by JSON export/import.
TABLES = [
    "app_meta", "people", "payment_modes", "category_types", "categories", "subcategories",
    "transactions", "transaction_shares", "settlements", "saved_views",
    "description_rules", "recurring_templates", "budgets",
]
# Tables added after schema version 1. An older JSON export may not have them.
OPTIONAL_TABLES = ["description_rules", "recurring_templates", "budgets"]

PAYMENT_KINDS = ["Credit card", "Debit card", "Bank", "Cash", "App"]
CATEGORY_TYPES = ["Income", "Expense", "Savings", "Not Reported"]


# ------------------------------------------------------------------ connections

def db_path() -> Path:
    """Where the database file lives."""
    return Path(os.environ.get("FINANCE_DB") or ROOT / "data" / "finance.db")


def make_private(path: str | Path) -> None:
    """Owner-only permissions (file 600, folder 700), so other accounts on the computer cannot read it."""
    target = Path(path)
    try:
        if target.is_dir():
            target.chmod(0o700)
        elif target.exists():
            target.chmod(0o600)
    except OSError:                                       # e.g. a file system without Unix permissions
        pass


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys on and rows that behave like dicts."""
    target = str(path) if path is not None else str(db_path())
    if target != ":memory:":
        folder = Path(target).parent
        if not folder.exists():
            folder.mkdir(parents=True)
            make_private(folder)
        if not Path(target).exists():
            Path(target).touch(mode=0o600)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path: str | Path | None = None):
    """`with db.session() as conn:` commits on success, rolls back on error, always closes."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> str:
    """Timestamp for created_at / updated_at (local time, ISO format)."""
    return datetime.now().isoformat(timespec="microseconds")


# ----------------------------------------------------------------------- schema

MIGRATIONS = {
    1: """
    CREATE TABLE app_meta (
        key TEXT PRIMARY KEY,
        value TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE people (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        is_me INTEGER NOT NULL DEFAULT 0 CHECK (is_me IN (0, 1)),
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    -- exactly one row may have is_me = 1
    CREATE UNIQUE INDEX people_one_me ON people (is_me) WHERE is_me = 1;

    CREATE TABLE payment_modes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind TEXT NOT NULL CHECK (kind IN ('Credit card', 'Debit card', 'Bank', 'Cash', 'App')),
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE category_types (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE categories (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        category_type_id TEXT NOT NULL REFERENCES category_types (id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE subcategories (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        category_id TEXT NOT NULL REFERENCES categories (id),
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE transactions (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,                       -- YYYY-MM-DD
        description TEXT,
        direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
        subcategory_id TEXT NOT NULL REFERENCES subcategories (id),
        payment_mode_id TEXT NOT NULL REFERENCES payment_modes (id),
        paid_by_person_id TEXT NOT NULL REFERENCES people (id),
        total_cents INTEGER NOT NULL,             -- always = SUM(shares), enforced in save_transaction()
        split_method TEXT NOT NULL CHECK (split_method IN ('equal', 'exact', 'percent', 'only_me')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX transactions_date ON transactions (date);
    CREATE TABLE transaction_shares (
        transaction_id TEXT NOT NULL REFERENCES transactions (id) ON DELETE CASCADE,
        person_id TEXT NOT NULL REFERENCES people (id),
        share_cents INTEGER NOT NULL CHECK (share_cents >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (transaction_id, person_id)
    );
    CREATE TABLE settlements (
        id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        from_person_id TEXT NOT NULL REFERENCES people (id),
        to_person_id TEXT NOT NULL REFERENCES people (id),
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        payment_mode_id TEXT REFERENCES payment_modes (id),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE saved_views (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        config_json TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
}

# Version 2 (phases 4 and 5): bank-import rules, recurring bills and monthly budgets.
MIGRATIONS[2] = """
    CREATE TABLE description_rules (            -- bank import: description contains pattern -> subcategory
        id TEXT PRIMARY KEY,
        pattern TEXT NOT NULL UNIQUE COLLATE NOCASE,
        subcategory_id TEXT NOT NULL REFERENCES subcategories (id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE recurring_templates (          -- a bill that comes back every month
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        day_of_month INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
        description TEXT,
        direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
        subcategory_id TEXT NOT NULL REFERENCES subcategories (id),
        payment_mode_id TEXT NOT NULL REFERENCES payment_modes (id),
        paid_by_person_id TEXT NOT NULL REFERENCES people (id),
        split_method TEXT NOT NULL,
        shares_json TEXT NOT NULL,              -- {person_id: share_cents}; the total is their sum
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE budgets (                      -- monthly budget per category
        id TEXT PRIMARY KEY,
        category_id TEXT NOT NULL UNIQUE REFERENCES categories (id) ON DELETE CASCADE,
        monthly_cents INTEGER NOT NULL CHECK (monthly_cents > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
"""

# Views are dropped and re-created on every start, so they can be changed freely.
VIEWS_SQL = """
DROP VIEW IF EXISTS v_shares;
DROP VIEW IF EXISTS v_transactions;

-- One row per transaction with every derived column.
CREATE VIEW v_transactions AS
SELECT
    t.id, t.date, t.description, t.direction,
    t.subcategory_id, s.name AS subcategory,
    c.id AS category_id, c.name AS category,
    ct.name AS category_type,
    t.payment_mode_id, pm.name AS payment_mode,
    t.paid_by_person_id, pb.name AS paid_by,
    t.total_cents, t.split_method, t.notes,
    CASE t.direction WHEN 'credit' THEN 1 ELSE -1 END AS sign,
    t.total_cents * CASE t.direction WHEN 'credit' THEN 1 ELSE -1 END AS signed_total_cents,
    COALESCE(mine.share_cents, 0) AS my_share_cents,
    COALESCE(mine.share_cents, 0) * CASE t.direction WHEN 'credit' THEN 1 ELSE -1 END AS my_share_signed_cents,
    CAST(strftime('%Y', t.date) AS INTEGER) AS year,
    strftime('%Y-%m', t.date) AS month,
    strftime('%Y-W%W', t.date) AS week,
    t.created_at, t.updated_at
FROM transactions t
JOIN subcategories s ON s.id = t.subcategory_id
JOIN categories c ON c.id = s.category_id
JOIN category_types ct ON ct.id = c.category_type_id
JOIN payment_modes pm ON pm.id = t.payment_mode_id
JOIN people pb ON pb.id = t.paid_by_person_id
LEFT JOIN transaction_shares mine
       ON mine.transaction_id = t.id
      AND mine.person_id = (SELECT id FROM people WHERE is_me = 1);

-- Long format: one row per transaction x person. All per-person analysis uses it.
CREATE VIEW v_shares AS
SELECT
    sh.transaction_id, sh.person_id, p.name AS person, p.is_me, p.sort_order AS person_sort,
    sh.share_cents,
    sh.share_cents * v.sign AS share_signed_cents,
    v.date, v.year, v.month, v.week, v.description, v.direction, v.sign,
    v.subcategory, v.category, v.category_type, v.payment_mode, v.paid_by,
    v.total_cents, v.signed_total_cents
FROM transaction_shares sh
JOIN v_transactions v ON v.id = sh.transaction_id
JOIN people p ON p.id = sh.person_id;
"""

# ------------------------------------------------------------------------- seed
# Starter lists for a brand-new database. Everyone renames them in Setup (People: put your
# roommates' names over "Roommate 1" and "Roommate 2"; Payment modes: your own cards).

SEED_PEOPLE = ["Mine", "Roommate 1", "Roommate 2", "Others"]  # the first one is "me"
SEED_PAYMENT_MODES = [
    ("visa", "Credit card"), ("mastercard", "Credit card"), ("debit card", "Debit card"),
    ("checking", "Bank"), ("Zelle", "App"), ("Cash", "Cash"),
]
SEED_CATEGORIES = [  # (category, type, subcategories)
    ("Income", "Income", ["salary", "misc-income"]),
    ("Home", "Expense", ["rent", "phone", "electricity", "internet", "washer dryer", "groceries", "home-others"]),
    ("Auto", "Expense", ["car loan", "insurance", "tolls", "charging", "car-others"]),
    ("Vacation", "Expense", ["vacation-misc"]),
    ("Personal", "Expense", ["misc-expense", "travel", "outside food", "movies", "subscriptions"]),
    ("Future", "Savings", ["gold"]),
    ("Retirement", "Savings", ["401k"]),
    ("Stocks", "Savings", ["stocks"]),
    ("HYSA", "Savings", ["high-yield savings"]),
    ("Transfer", "Not Reported", ["bank transfer", "roommate settlement"]),
]


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value) -> None:
    ts = now()
    conn.execute(
        "INSERT INTO app_meta (key, value, created_at, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, None if value is None else str(value), ts, ts),
    )


def _current_version(conn) -> int:
    has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_meta'").fetchone()
    return int(get_meta(conn, "schema_version", 0)) if has_meta else 0


def _seed(conn) -> None:
    """Fill the master lists once, on a brand-new database."""
    ts = now()
    for i, name in enumerate(SEED_PEOPLE):
        conn.execute(
            "INSERT INTO people (id, name, is_me, active, sort_order, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
            (new_id(), name, 1 if i == 0 else 0, i + 1, ts, ts),
        )
    for i, (name, kind) in enumerate(SEED_PAYMENT_MODES):
        conn.execute(
            "INSERT INTO payment_modes (id, name, kind, active, sort_order, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
            (new_id(), name, kind, i + 1, ts, ts),
        )
    type_ids = {}
    for name in CATEGORY_TYPES:
        type_ids[name] = new_id()
        conn.execute("INSERT INTO category_types (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                     (type_ids[name], name, ts, ts))
    for category, type_name, subcategories in SEED_CATEGORIES:
        category_id = new_id()
        conn.execute("INSERT INTO categories (id, name, category_type_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (category_id, category, type_ids[type_name], ts, ts))
        for sub in subcategories:
            conn.execute("INSERT INTO subcategories (id, name, category_id, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                         (new_id(), sub, category_id, ts, ts))
    set_meta(conn, "seeded", 1)


def init_schema(conn) -> None:
    """Create or upgrade the schema, seed a new database and (re)create the views."""
    version = _current_version(conn)
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.executescript(MIGRATIONS[target])
        set_meta(conn, "schema_version", target)
    if get_meta(conn, "seeded") is None:
        _seed(conn)
    conn.executescript(VIEWS_SQL)
    conn.commit()


def init_db(path: str | Path | None = None) -> None:
    """Make sure the database file exists and is up to date. Safe to call on every start."""
    with session(path) as conn:
        init_schema(conn)


# ---------------------------------------------------------------------- lookups

def rows(conn, sql: str, params=()) -> list[dict]:
    """Run a SELECT and return plain dicts."""
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def me_id(conn) -> str:
    return conn.execute("SELECT id FROM people WHERE is_me = 1").fetchone()["id"]


def list_people(conn) -> list[dict]:
    return rows(conn, "SELECT * FROM people ORDER BY sort_order, name")


def list_payment_modes(conn) -> list[dict]:
    return rows(conn, "SELECT * FROM payment_modes ORDER BY sort_order, name")


def list_category_types(conn) -> list[dict]:
    return rows(conn, "SELECT * FROM category_types ORDER BY rowid")


def list_categories(conn) -> list[dict]:
    return rows(conn, """
        SELECT c.*, ct.name AS category_type
        FROM categories c JOIN category_types ct ON ct.id = c.category_type_id
        ORDER BY c.rowid""")


def list_subcategories(conn) -> list[dict]:
    return rows(conn, """
        SELECT s.*, c.name AS category, ct.name AS category_type
        FROM subcategories s
        JOIN categories c ON c.id = s.category_id
        JOIN category_types ct ON ct.id = c.category_type_id
        ORDER BY c.rowid, s.rowid""")


def list_settlements(conn) -> list[dict]:
    return rows(conn, """
        SELECT s.*, pf.name AS from_person, pt.name AS to_person, pm.name AS payment_mode
        FROM settlements s
        JOIN people pf ON pf.id = s.from_person_id
        JOIN people pt ON pt.id = s.to_person_id
        LEFT JOIN payment_modes pm ON pm.id = s.payment_mode_id
        ORDER BY s.date DESC, s.created_at DESC""")


def list_saved_views(conn) -> list[dict]:
    return rows(conn, "SELECT * FROM saved_views ORDER BY sort_order, name")


# ----------------------------------------------------------------- transactions

def _iso(day) -> str:
    """Accept a date or a YYYY-MM-DD string and return the string (validated)."""
    if isinstance(day, datetime):
        day = day.date()
    if isinstance(day, date):
        return day.isoformat()
    return date.fromisoformat(str(day)).isoformat()


def save_transaction(conn, tx: dict, shares: dict[str, int], tx_id: str | None = None) -> str:
    """Insert a transaction (tx_id None) or update one, and replace its shares.

    tx keys: date, description, direction, subcategory_id, payment_mode_id,
             paid_by_person_id, split_method, notes
    shares:  {person_id: share_cents}. total_cents is always computed as their sum.
    """
    shares = {person: int(cents) for person, cents in shares.items()}
    total = sum(shares.values())
    money.validate_shares(total, shares)
    if tx["direction"] not in money.DIRECTIONS:
        raise money.MoneyError(f"Unknown direction '{tx['direction']}'.")
    if tx["split_method"] not in money.SPLIT_METHODS:
        raise money.MoneyError(f"Unknown split method '{tx['split_method']}'.")
    ts = now()
    values = (
        _iso(tx["date"]), (tx.get("description") or "").strip(), tx["direction"], tx["subcategory_id"],
        tx["payment_mode_id"], tx["paid_by_person_id"], total, tx["split_method"], (tx.get("notes") or "").strip(),
    )
    if tx_id is None:
        tx_id = new_id()
        conn.execute(
            "INSERT INTO transactions (date, description, direction, subcategory_id, payment_mode_id, "
            "paid_by_person_id, total_cents, split_method, notes, id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values + (tx_id, ts, ts),
        )
    else:
        conn.execute(
            "UPDATE transactions SET date = ?, description = ?, direction = ?, subcategory_id = ?, "
            "payment_mode_id = ?, paid_by_person_id = ?, total_cents = ?, split_method = ?, notes = ?, "
            "updated_at = ? WHERE id = ?",
            values + (ts, tx_id),
        )
        conn.execute("DELETE FROM transaction_shares WHERE transaction_id = ?", (tx_id,))
    conn.executemany(
        "INSERT INTO transaction_shares (transaction_id, person_id, share_cents, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        [(tx_id, person, cents, ts, ts) for person, cents in shares.items()],
    )
    return tx_id


def delete_transaction(conn, tx_id: str) -> None:
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))  # shares go with it (ON DELETE CASCADE)


def get_transaction(conn, tx_id: str) -> dict | None:
    """One transaction as a dict, with its shares under the key 'shares'."""
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if row is None:
        return None
    tx = dict(row)
    tx["shares"] = {r["person_id"]: r["share_cents"] for r in conn.execute(
        "SELECT person_id, share_cents FROM transaction_shares WHERE transaction_id = ?", (tx_id,))}
    return tx


def last_transaction(conn, description: str | None = None) -> dict | None:
    """The most recently entered transaction, optionally the last one with this description."""
    if description is None:
        row = conn.execute("SELECT id FROM transactions ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM transactions WHERE lower(trim(description)) = lower(trim(?)) "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (description,)).fetchone()
    return get_transaction(conn, row["id"]) if row else None


def balance_inputs(conn) -> tuple[list[dict], list[dict]]:
    """All transactions and settlements in the shape money.balances() expects (people by id)."""
    shares: dict[str, dict] = {}
    for r in conn.execute("SELECT transaction_id, person_id, share_cents FROM transaction_shares"):
        shares.setdefault(r["transaction_id"], {})[r["person_id"]] = r["share_cents"]
    transactions = [
        {"total_cents": r["total_cents"], "payer": r["paid_by_person_id"], "direction": r["direction"],
         "shares": shares.get(r["id"], {})}
        for r in conn.execute("SELECT id, total_cents, paid_by_person_id, direction FROM transactions")
    ]
    settlements = [
        {"from": r["from_person_id"], "to": r["to_person_id"], "amount_cents": r["amount_cents"]}
        for r in conn.execute("SELECT from_person_id, to_person_id, amount_cents FROM settlements")
    ]
    return transactions, settlements


# ------------------------------------------------------------------ settlements

def add_settlement(conn, day, from_person_id: str, to_person_id: str, amount_cents: int,
                   payment_mode_id: str | None = None, notes: str = "") -> str:
    if from_person_id == to_person_id:
        raise money.MoneyError("Pick two different people.")
    if int(amount_cents) <= 0:
        raise money.MoneyError("The amount must be more than zero.")
    settlement_id, ts = new_id(), now()
    conn.execute(
        "INSERT INTO settlements (id, date, from_person_id, to_person_id, amount_cents, payment_mode_id, notes, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (settlement_id, _iso(day), from_person_id, to_person_id, int(amount_cents), payment_mode_id,
         (notes or "").strip(), ts, ts),
    )
    return settlement_id


def delete_settlement(conn, settlement_id: str) -> None:
    conn.execute("DELETE FROM settlements WHERE id = ?", (settlement_id,))


# ------------------------------------------------------------------ saved views

def save_view(conn, name: str, config_json: str, pinned: bool = False) -> str:
    """Create a saved view, or overwrite the one with the same name."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the view a name.")
    ts = now()
    existing = conn.execute("SELECT id FROM saved_views WHERE name = ?", (name,)).fetchone()
    if existing:
        conn.execute("UPDATE saved_views SET config_json = ?, pinned = ?, updated_at = ? WHERE id = ?",
                     (config_json, int(pinned), ts, existing["id"]))
        return existing["id"]
    view_id = new_id()
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM saved_views").fetchone()["n"]
    conn.execute(
        "INSERT INTO saved_views (id, name, config_json, pinned, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (view_id, name, config_json, int(pinned), order, ts, ts))
    return view_id


def set_view_pinned(conn, view_id: str, pinned: bool) -> None:
    conn.execute("UPDATE saved_views SET pinned = ?, updated_at = ? WHERE id = ?", (int(pinned), now(), view_id))


def delete_view(conn, view_id: str) -> None:
    conn.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))


# ----------------------------------------------------- master lists (Setup page)
# For each master table: which columns can be edited, and where its rows are referenced.

MASTER = {
    "people": {
        "fields": ["name", "active", "sort_order"],
        "refs": [("transactions", "paid_by_person_id"), ("transaction_shares", "person_id"),
                 ("settlements", "from_person_id"), ("settlements", "to_person_id"),
                 ("recurring_templates", "paid_by_person_id")],
    },
    "payment_modes": {
        "fields": ["name", "kind", "active", "sort_order"],
        "refs": [("transactions", "payment_mode_id"), ("settlements", "payment_mode_id"),
                 ("recurring_templates", "payment_mode_id")],
    },
    "categories": {
        "fields": ["name", "category_type_id"],
        "refs": [("subcategories", "category_id")],
    },
    "subcategories": {
        "fields": ["name", "category_id", "active"],
        "refs": [("transactions", "subcategory_id"), ("description_rules", "subcategory_id"),
                 ("recurring_templates", "subcategory_id")],
    },
}


class InUseError(ValueError):
    """Raised when someone tries to delete a master row that other rows still point to."""


def usage_count(conn, table: str, row_id: str) -> int:
    """How many rows elsewhere reference this master row."""
    total = 0
    for ref_table, ref_column in MASTER[table]["refs"]:
        # Table and column names come from the MASTER constant above, never from user input.
        total += conn.execute(f"SELECT COUNT(*) AS n FROM {ref_table} WHERE {ref_column} = ?", (row_id,)).fetchone()["n"]
    return total


def save_master_row(conn, table: str, values: dict, row_id: str | None = None) -> str:
    """Insert (row_id None) or update one row of a master table. `values` holds the editable fields."""
    fields = MASTER[table]["fields"]
    clean = {}
    for field in fields:
        value = values.get(field)
        if field == "name":
            value = (str(value) if value is not None else "").strip()
            if not value:
                raise ValueError("Names cannot be empty.")
        elif field == "active":
            value = 1 if value else 0
        elif field == "sort_order":
            value = int(value or 0)
        clean[field] = value
    ts = now()
    if row_id is None:
        row_id = new_id()
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        extra_cols, extra_vals = "", ()
        if table == "people":  # new people are never "me"
            extra_cols, extra_vals = ", is_me", (0,)
        conn.execute(
            f"INSERT INTO {table} ({columns}, id, created_at, updated_at{extra_cols}) "
            f"VALUES ({marks}, ?, ?, ?{', ?' if extra_vals else ''})",
            tuple(clean[f] for f in fields) + (row_id, ts, ts) + extra_vals,
        )
    else:
        if table == "people" and not clean["active"]:
            if conn.execute("SELECT is_me FROM people WHERE id = ?", (row_id,)).fetchone()["is_me"]:
                raise ValueError("You cannot deactivate yourself.")
        assignments = ", ".join(f"{f} = ?" for f in fields)
        conn.execute(f"UPDATE {table} SET {assignments}, updated_at = ? WHERE id = ?",
                     tuple(clean[f] for f in fields) + (ts, row_id))
    return row_id


def delete_master_row(conn, table: str, row_id: str) -> None:
    """Delete a master row, but only if nothing uses it (otherwise raise InUseError)."""
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return
    if table == "people" and row["is_me"]:
        raise InUseError("You cannot delete yourself.")
    used = usage_count(conn, table, row_id)
    if used:
        hint = "Untick 'active' instead." if "active" in MASTER[table]["fields"] else "Move or delete its subcategories first."
        raise InUseError(f"'{row['name']}' is used by {used} row(s), so it cannot be deleted. {hint}")
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


# ------------------------------------------------- description rules (bank import)

def list_rules(conn) -> list[dict]:
    return rows(conn, """
        SELECT r.*, s.name AS subcategory FROM description_rules r
        JOIN subcategories s ON s.id = r.subcategory_id ORDER BY r.rowid""")


def replace_rules(conn, rules: list[tuple[str, str]]) -> None:
    """Store the full list of rules: (pattern, subcategory_id). Earlier rules win when several match."""
    conn.execute("DELETE FROM description_rules")
    ts = now()
    for pattern, subcategory_id in rules:
        if (pattern or "").strip():
            conn.execute("INSERT INTO description_rules (id, pattern, subcategory_id, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?)", (new_id(), pattern.strip(), subcategory_id, ts, ts))


# ---------------------------------------------------------------------- budgets

def list_budgets(conn) -> list[dict]:
    return rows(conn, "SELECT b.*, c.name AS category FROM budgets b JOIN categories c ON c.id = b.category_id")


def set_budget(conn, category_id: str, monthly_cents: int | None) -> None:
    """Set a category's monthly budget in cents. None or 0 removes it."""
    if not monthly_cents or int(monthly_cents) <= 0:
        conn.execute("DELETE FROM budgets WHERE category_id = ?", (category_id,))
        return
    ts = now()
    conn.execute(
        "INSERT INTO budgets (id, category_id, monthly_cents, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (category_id) DO UPDATE SET monthly_cents = excluded.monthly_cents, updated_at = excluded.updated_at",
        (new_id(), category_id, int(monthly_cents), ts, ts))


# ------------------------------------------------------------ recurring templates

def list_templates(conn) -> list[dict]:
    """Recurring bills, each with its total in cents under 'total_cents'."""
    import json
    found = rows(conn, """
        SELECT t.*, s.name AS subcategory, pm.name AS payment_mode
        FROM recurring_templates t
        JOIN subcategories s ON s.id = t.subcategory_id
        JOIN payment_modes pm ON pm.id = t.payment_mode_id
        ORDER BY t.day_of_month, t.name""")
    for template in found:
        template["shares"] = json.loads(template["shares_json"])
        template["total_cents"] = sum(template["shares"].values())
    return found


def save_template_from_transaction(conn, tx_id: str) -> str:
    """Make a recurring bill from an existing transaction (same split, same day of the month)."""
    import json
    tx = get_transaction(conn, tx_id)
    template_id, ts = new_id(), now()
    conn.execute(
        "INSERT INTO recurring_templates (id, name, day_of_month, description, direction, subcategory_id, "
        "payment_mode_id, paid_by_person_id, split_method, shares_json, active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (template_id, tx["description"] or "Recurring", date.fromisoformat(tx["date"]).day, tx["description"],
         tx["direction"], tx["subcategory_id"], tx["payment_mode_id"], tx["paid_by_person_id"],
         tx["split_method"], json.dumps(tx["shares"]), ts, ts))
    return template_id


def delete_template(conn, template_id: str) -> None:
    conn.execute("DELETE FROM recurring_templates WHERE id = ?", (template_id,))


def recurring_plan(conn, year: int, month: int) -> list[dict]:
    """What "Add recurring" offers for a month: one item per active recurring bill.

    Each item has template_id, name, description, date (the bill's day, moved back when the
    month is shorter), total_cents, payment_mode_id, split_method, subcategory and
    already_entered (that month already has a transaction with the same description,
    subcategory and total, so it is offered unticked).
    """
    import calendar
    prefix, last_day = f"{year:04d}-{month:02d}", calendar.monthrange(year, month)[1]
    plan = []
    for template in list_templates(conn):
        if not template["active"]:
            continue
        exists = conn.execute(
            "SELECT 1 FROM transactions WHERE substr(date, 1, 7) = ? AND lower(trim(description)) = lower(trim(?)) "
            "AND subcategory_id = ? AND total_cents = ?",
            (prefix, template["description"] or "", template["subcategory_id"], template["total_cents"])).fetchone()
        plan.append({
            "template_id": template["id"], "name": template["name"], "description": template["description"] or "",
            "date": date(year, month, min(template["day_of_month"], last_day)),
            "total_cents": template["total_cents"], "payment_mode_id": template["payment_mode_id"],
            "payment_mode": template["payment_mode"], "split_method": template["split_method"],
            "subcategory": template["subcategory"], "already_entered": exists is not None,
        })
    return plan


def add_recurring_items(conn, items: list[dict], *, save_as_default: bool = False) -> int:
    """Add chosen recurring bills, each possibly edited. Returns how many were added.

    items: dicts with template_id, date, total_cents, payment_mode_id and optional
    description and notes. A changed total is re-split the way the bill was split
    (equal stays equal; other splits keep each person's proportion).
    save_as_default also stores the new total, payment mode and day on the recurring bill.
    """
    import json
    from core import money
    templates = {t["id"]: t for t in list_templates(conn)}
    order = [p["id"] for p in list_people(conn)]
    added = 0
    for item in items:
        template = templates[item["template_id"]]
        shares = money.scale_shares(template["shares"], item["total_cents"], template["split_method"],
                                    template["paid_by_person_id"], order)
        day = item["date"] if isinstance(item["date"], date) else date.fromisoformat(str(item["date"]))
        description = item.get("description")
        save_transaction(conn, {
            "date": day, "description": template["description"] if description is None else description,
            "direction": template["direction"], "subcategory_id": template["subcategory_id"],
            "payment_mode_id": item.get("payment_mode_id") or template["payment_mode_id"],
            "paid_by_person_id": template["paid_by_person_id"], "split_method": template["split_method"],
            "notes": item.get("notes", "recurring")}, shares)
        if save_as_default:
            conn.execute(
                "UPDATE recurring_templates SET shares_json = ?, payment_mode_id = ?, day_of_month = ?, updated_at = ? "
                "WHERE id = ?",
                (json.dumps(shares), item.get("payment_mode_id") or template["payment_mode_id"], day.day, now(),
                 template["id"]))
        added += 1
    return added


def add_recurring(conn, year: int, month: int) -> tuple[int, int]:
    """Add every active recurring bill that is not entered yet to a month. Returns (added, skipped).

    Pressing it twice never doubles anything. The day is moved back when the month is
    shorter (the 31st becomes the 30th or 28th).
    """
    plan = recurring_plan(conn, year, month)
    new = [item for item in plan if not item["already_entered"]]
    return add_recurring_items(conn, new), len(plan) - len(new)
