"""Backup: database copies, JSON export and JSON import (SPEC section 7.6).

- "Back up now" copies the database to backups/finance_YYYYMMDD_HHMM.db (the last 30 are kept).
  A backup also runs by itself on app start when the last one is older than 24 hours.
- The JSON export is a full dump of every table. It is the bridge to a future phone app.
- Importing a JSON file REPLACES all data, so it shows a preview, asks for confirmation
  and makes a backup first.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from core import backup, db, export, ui

d, filters = ui.page("Backup")
ss = st.session_state

# ---------------------------------------------------------------------- backups

last = backup.last_backup_at()
if last is None:
    st.warning("No backup has been made yet.")
elif datetime.now() - last > timedelta(days=backup.WARN_DAYS):
    st.warning(f"The last backup is from {last:%b %d, %Y %H:%M}, more than {backup.WARN_DAYS} days ago.")
else:
    st.success(f"Last backup: {last:%b %d, %Y %H:%M}")

if st.button("Back up now", type="primary", icon=":material/backup:", key="backup_now"):
    target = backup.backup_now()
    ui.flash(f"Backup saved as {target.name}.")
    st.rerun()

files = backup.list_backups()
st.caption(f"{len(files)} backup file(s) in {backup.backup_dir()} (the newest {backup.KEEP} are kept). "
           "To restore one, close the app and copy the file over data/finance.db.")
if files:
    st.dataframe(pd.DataFrame({
        "File": [f.name for f in files],
        "Saved": [datetime.fromtimestamp(f.stat().st_mtime).strftime("%b %d, %Y %H:%M") for f in files],
        "Size": [f"{f.stat().st_size / 1024:,.0f} KB" for f in files],
    }), hide_index=True, height=min(300, 38 * (len(files) + 1) + 4))

# ------------------------------------------------------------------ JSON export

st.subheader("Export JSON")
st.caption("Every table in one readable file, with a schema version. Keep it somewhere safe; it contains all your data.")


def build_json() -> bytes:
    """Runs when the download button is clicked."""
    with db.session() as conn:
        return json.dumps(export.export_json(conn), indent=2).encode("utf-8")


st.download_button("Export JSON", build_json, file_name=f"finance_export_{date.today().isoformat()}.json",
                   mime="application/json", icon=":material/download:", key="export_json")

# ------------------------------------------------------------------ JSON import

st.subheader("Import JSON")
st.caption("Restores a file made with Export JSON. This replaces everything currently in the app.")
uploaded = st.file_uploader("Export file", type=["json"], key="import_file", label_visibility="collapsed")
if uploaded is not None:
    try:
        incoming = json.loads(uploaded.getvalue().decode("utf-8"))
        preview = export.json_preview(incoming)
    except (ValueError, UnicodeDecodeError) as problem:
        st.error(f"This file cannot be imported: {problem}")
    else:
        with db.session() as conn:
            current = {table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] for table in db.TABLES}
        st.markdown(f"**Preview** · exported {ui.plain(preview['exported_at'] or 'at an unknown time')} · "
                    f"schema version {preview['schema_version']}")
        st.dataframe(pd.DataFrame({
            "Table": db.TABLES,
            "Rows in file": [preview["counts"][t] for t in db.TABLES],
            "Rows now (will be replaced)": [current[t] for t in db.TABLES],
        }), hide_index=True)
        sure = st.checkbox("I understand this replaces all current data. A backup is made first.", key="import_sure")
        if st.button("Import and replace", type="primary", disabled=not sure, key="import_go"):
            backup.backup_now()
            try:
                with db.session() as conn:
                    export.import_json(conn, incoming)
            except Exception as problem:  # nothing was changed: the transaction was rolled back
                st.error(f"Import failed and nothing was changed: {problem}")
            else:
                db.init_db()                # recreate the views and run any newer migrations
                ui.invalidate()
                ui.flash(f"Imported {preview['counts']['transactions']:,} transactions.")
                st.rerun()
