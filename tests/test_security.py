"""Safety checks for sharing the code: no data in git, local-only settings, and safe handling
of text that comes from people or files (descriptions, names, imported spreadsheets)."""
from __future__ import annotations

import io
import stat
import tomllib

import pandas as pd
from openpyxl import load_workbook

from conftest import ROOT
from core import backup, db, export, ui


def test_gitignore_keeps_money_data_out_of_git():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for pattern in ["data/", "backups/", "*.db", "*.xlsx", "*.csv", "finance_export_*.json",
                    ".env", ".streamlit/secrets.toml", ".venv/", "finance-tracker-SPEC.md"]:
        assert pattern in ignored, f"{pattern} must be in .gitignore"


def test_streamlit_settings_are_local_only():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["address"] == "127.0.0.1"
    assert config["server"]["enableXsrfProtection"] is True
    assert config["server"]["enableStaticServing"] is False
    assert config["browser"]["gatherUsageStats"] is False


def test_starter_lists_hold_no_personal_names():
    assert db.SEED_PEOPLE == ["Mine", "Roommate 1", "Roommate 2", "Others"]


def test_plain_shows_markdown_as_typed():
    escaped = ui.plain("![x](https://example.com/a.png) **bold** :red[hi] $5")
    for raw in ["![", "](", "**", ":red[", " $5"]:
        assert raw not in escaped                        # nothing left that Markdown would act on
    assert escaped.replace("\\", "") == "![x](https://example.com/a.png) **bold** :red[hi] $5"
    assert ui.plain(None) == ""


def test_csv_download_neutralises_formulas():
    frame = pd.DataFrame({"Description": ["=HYPERLINK(\"http://x\")", "+1", "@SUM(A1)", "-cmd", "Costco"],
                          "Amount": [-1.5, 2.0, 3.0, -4.0, 5.0]})
    text = export.csv_bytes(frame).decode("utf-8")
    assert "'=HYPERLINK" in text and "'+1" in text and "'@SUM" in text and "'-cmd" in text
    assert ",Costco," not in text and "Costco,5.0" in text
    assert "-1.5" in text and "'-1.5" not in text          # numbers keep their sign


def test_excel_download_never_contains_formulas():
    frame = pd.DataFrame({"Description": ["=1+1", "=HYPERLINK(\"http://x\",\"click\")"], "Total": [100, 200]})
    book = load_workbook(io.BytesIO(export.workbook_bytes({"Sheet": (frame, ["Total"])})))
    cells = [cell for row in book["Sheet"].iter_rows() for cell in row]
    assert not [cell.coordinate for cell in cells if cell.data_type == "f"]
    assert book["Sheet"]["A2"].value == "=1+1"            # kept as the text that was typed


def test_database_and_backups_are_private_files(tmp_path):
    path = tmp_path / "new" / "finance.db"
    with db.session(path) as conn:
        db.init_schema(conn)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    copy = backup.backup_now(path, tmp_path / "backups")
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(copy.parent.stat().st_mode) == 0o700


def test_ai_assistant_instructions_keep_the_privacy_rules():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Never open, read, print, copy, summarise or upload real data" in agents
    assert "@AGENTS.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
