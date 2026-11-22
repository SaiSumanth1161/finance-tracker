"""Tests for core/export.py and core/backup.py: Excel and JSON round-trips, backups (SPEC section 9)."""
from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from core import backup, db, demo, export


@pytest.fixture
def demo_conn(conn):
    demo.load_demo(conn, today=date(2026, 11, 16))
    conn.commit()
    return conn


def _cents(series) -> int:
    """Dollars read back from Excel -> whole cents."""
    return int(round(float(series.sum()) * 100))


def test_excel_round_trip(demo_conn):
    book = pd.read_excel(io.BytesIO(export.transactions_workbook(demo_conn)), sheet_name=None)
    assert list(book) == ["Transactions", "Shares", "Categories", "Balances"]
    sheet = book["Transactions"]
    in_db = demo_conn.execute(
        "SELECT COUNT(*) AS n, SUM(total_cents) AS total, SUM(signed_total_cents) AS signed, "
        "SUM(my_share_signed_cents) AS mine FROM v_transactions").fetchone()
    assert len(sheet) == in_db["n"]
    assert _cents(sheet["Total"]) == in_db["total"]
    assert _cents(sheet["Income/Expense"]) == in_db["signed"]
    assert _cents(sheet["My share"]) == in_db["mine"]
    # One column per person, and together they add up to the total.
    assert {"Mine", "Roommate 1", "Roommate 2", "Others"} <= set(sheet.columns)
    assert _cents(sheet[["Mine", "Roommate 1", "Roommate 2", "Others"]].sum(axis=1)) == in_db["total"]
    shares = demo_conn.execute("SELECT COUNT(*) AS n, SUM(share_cents) AS total FROM transaction_shares").fetchone()
    assert len(book["Shares"]) == shares["n"] and _cents(book["Shares"]["Share"]) == shares["total"]
    assert len(book["Categories"]) == len(db.list_subcategories(demo_conn))
    assert "Net" in book["Balances"].columns


def test_excel_export_respects_selected_rows(demo_conn):
    ids = [r["id"] for r in demo_conn.execute("SELECT id FROM transactions ORDER BY date LIMIT 10")]
    book = pd.read_excel(io.BytesIO(export.transactions_workbook(demo_conn, ids)), sheet_name=None)
    assert len(book["Transactions"]) == 10
    expected = demo_conn.execute(
        f"SELECT SUM(total_cents) AS total FROM transactions WHERE id IN ({', '.join('?' for _ in ids)})", ids).fetchone()
    assert _cents(book["Transactions"]["Total"]) == expected["total"]


def test_excel_export_of_empty_database(conn):
    book = pd.read_excel(io.BytesIO(export.transactions_workbook(conn)), sheet_name=None)
    assert len(book["Transactions"]) == 0 and len(book["Categories"]) == 26


def test_json_round_trip_gives_identical_tables(demo_conn):
    db.save_view(demo_conn, "Coffee trend", json.dumps({"measure": "Expense"}), pinned=True)
    dump = json.loads(json.dumps(export.export_json(demo_conn)))      # through real JSON text, like a file
    assert dump["schema_version"] == db.SCHEMA_VERSION
    preview = export.json_preview(dump)
    assert preview["counts"]["people"] == 4 and preview["counts"]["transactions"] > 100

    other = db.connect(":memory:")
    db.init_schema(other)
    export.import_json(other, dump)
    other.commit()
    for table in db.TABLES:
        original = db.rows(demo_conn, f"SELECT * FROM {table} ORDER BY rowid")
        restored = db.rows(other, f"SELECT * FROM {table} ORDER BY rowid")
        assert restored == original, table
    # The views work on the restored data too.
    assert other.execute("SELECT COUNT(*) AS n FROM v_shares").fetchone()["n"] == \
        demo_conn.execute("SELECT COUNT(*) AS n FROM v_shares").fetchone()["n"]
    other.close()


def test_json_import_rejects_bad_files(conn):
    good = export.export_json(conn)
    for bad in ({}, {"app": "something-else", "tables": {}, "schema_version": 1},
                {**good, "schema_version": db.SCHEMA_VERSION + 1},
                {**good, "tables": {"people": []}}):
        with pytest.raises(ValueError):
            export.import_json(conn, bad)
    # Unknown column names are refused (they are never put into SQL).
    hostile = json.loads(json.dumps(good))
    hostile["tables"]["people"][0]["name) VALUES (1); DROP TABLE people; --"] = "x"
    with pytest.raises(ValueError):
        export.import_json(conn, hostile)
    conn.rollback()
    assert len(db.list_people(conn)) == 4  # nothing was lost


def test_backup_now_and_keep_last_30(tmp_path):
    path, folder = tmp_path / "finance.db", tmp_path / "backups"
    db.init_db(path)
    assert backup.last_backup_at(path) is None
    start = datetime(2026, 11, 1, 9, 0)
    for minute in range(backup.KEEP + 3):
        target = backup.backup_now(path, folder, start + timedelta(minutes=minute))
    assert target.name == "finance_20261101_0932.db"
    files = backup.list_backups(folder)
    assert len(files) == backup.KEEP and files[0] == target
    assert backup.last_backup_at(path) == start + timedelta(minutes=backup.KEEP + 2)
    # The copy is a real database with the same data.
    copy = db.connect(target)
    assert len(db.list_people(copy)) == 4
    copy.close()


def test_auto_backup_only_when_older_than_24_hours(tmp_path):
    path, folder = tmp_path / "finance.db", tmp_path / "backups"
    db.init_db(path)
    first = datetime(2026, 11, 1, 9, 0)
    assert backup.auto_backup_if_due(path, folder, first) is not None            # none yet
    assert backup.auto_backup_if_due(path, folder, first + timedelta(hours=5)) is None
    assert backup.auto_backup_if_due(path, folder, first + timedelta(hours=25)) is not None
    assert len(backup.list_backups(folder)) == 2
