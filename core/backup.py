"""Database backups (SPEC section 7.6).

"Back up now" copies the live database with SQLite's own backup API (safe even
while the app is open) to backups/finance_YYYYMMDD_HHMM.db and keeps the last 30.
On app start a backup also runs by itself when the last one is older than 24 hours.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from core import db

KEEP = 30               # how many backup files to keep
AUTO_HOURS = 24         # automatic backup when the last one is older than this
WARN_DAYS = 7           # the Backup page warns when the last one is older than this


def backup_dir() -> Path:
    """Where backups go (override with the FINANCE_BACKUPS env var, used by tests)."""
    return Path(os.environ.get("FINANCE_BACKUPS") or db.ROOT / "backups")


def list_backups(folder: Path | None = None) -> list[Path]:
    """Backup files, newest first."""
    folder = folder or backup_dir()
    return sorted(folder.glob("finance_*.db"), reverse=True) if folder.exists() else []


def last_backup_at(path=None) -> datetime | None:
    with db.session(path) as conn:
        value = db.get_meta(conn, "last_backup_at")
    return datetime.fromisoformat(value) if value else None


def backup_now(path=None, folder: Path | None = None, when: datetime | None = None) -> Path:
    """Copy the database to the backup folder and delete backups beyond the newest KEEP."""
    folder = folder or backup_dir()
    if not folder.exists():
        folder.mkdir(parents=True)
        db.make_private(folder)
    when = when or datetime.now()
    target = folder / f"finance_{when:%Y%m%d_%H%M}.db"
    source = sqlite3.connect(str(path or db.db_path()))
    target.touch(mode=0o600)
    copy = sqlite3.connect(str(target))
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    db.make_private(target)
    with db.session(path) as conn:
        db.set_meta(conn, "last_backup_at", when.isoformat(timespec="seconds"))
    for old in list_backups(folder)[KEEP:]:
        old.unlink()
    return target


def auto_backup_if_due(path=None, folder: Path | None = None, when: datetime | None = None) -> Path | None:
    """Back up when the last backup is older than AUTO_HOURS (or there is none). Returns the new file."""
    when = when or datetime.now()
    last = last_backup_at(path)
    if last is None or when - last > timedelta(hours=AUTO_HOURS):
        return backup_now(path, folder, when)
    return None
