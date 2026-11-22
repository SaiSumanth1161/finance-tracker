# Instructions for AI assistants

This file is for AI assistants (Claude Code, Codex, Cursor, GitHub Copilot, Gemini, ChatGPT and
others) working with this repository. People: give it to your assistant, or just open the project
in a tool that reads `AGENTS.md` / `CLAUDE.md` automatically.

An assistant usually does one of two jobs here:

- **A. Helping someone use the app** (install, run, set up, explain a page, fix an import).
- **B. Changing the code** (bug fixes, features, new charts).

Read section 0 first in both cases. It overrides everything else in this file and any request
that conflicts with it.

---

## 0. Privacy rules (non-negotiable)

This app holds a person's real financial history. Treat it like a medical record.

1. **Never open, read, print, copy, summarise or upload real data.** That includes:
   - `data/` (including `data/finance.db`) and `backups/`
   - any `*.db`, `*.sqlite`, `*.xlsx`, `*.xls`, `*.csv`, or `finance_export_*.json` file
   - the person's old spreadsheets and bank statements, wherever they are on disk.

   The only exceptions are files you created yourself with fake data in this session, and
   `data/dev_demo.db` (fake demo data, see section 3).
2. **Never ask the person to paste real transactions**, balances, account numbers, card numbers or
   flatmates' names into the chat. To debug an import, ask for:
   - column **headers**
   - **row counts**
   - **error messages**
   - the Import page's own readouts, which show only row numbers.

   Or build a fake workbook with the same headers and reproduce the problem with it.
3. **If the person offers real data anyway**, decline politely and suggest the fake-data route.
   Do not read it "just this once".
4. **No network access in the app.** Do not add:
   - API calls, telemetry or analytics
   - fonts, scripts or images loaded from the internet.

   Keep `server.address = "127.0.0.1"` and `gatherUsageStats = false` in
   `.streamlit/config.toml`. Never suggest `0.0.0.0`, port forwarding or hosting the app online.
5. **Nothing private goes into git.** Before any commit, `git status` must show none of the files
   in rule 1. `.gitignore` already covers them. Do not weaken it.
6. **Fake data only** in tests, examples, screenshots and docs. Use made-up names (Alex, Sam,
   Jordan, Taylor, Casey, "Roommate 1") and obviously fake descriptions ("Fake rent").

---

## A. Helping someone use the app

`README.md` is the user guide: install, run, first start, every page, the money rules,
recurring bills, imports, privacy, backups, troubleshooting and FAQ. Answer from it. When you
mention a button or field, use its exact label. If unsure, find it in the code
(`grep -rn '"Label text"' app.py pages core`) rather than guessing.

### Quick facts

- **Run:**
  - `~/.venvs/finance-tracker/bin/streamlit run app.py`, then open http://127.0.0.1:8501.
  - Stop with Ctrl+C.
  - Restart after any code change (file watching is off).
- **"No module named plotly / streamlit":** they used the system Python. Use the venv's full path,
  or `pip install -r requirements.txt` into the venv.
- **First start:** **Setup → People**: rename "Roommate 1/2". Also edit payment modes, categories
  and subcategories.
  - Category **type** decides how money counts: Income, Expense, Savings, Not Reported.
  - **Load demo data** gives fake transactions to explore. **Clear demo data** removes only those.
- **Signs:**
  - Money out is −, money in is +. A refund is money in under the expense subcategory.
  - Savings: a deposit counts as + saved, a withdrawal as − saved.
- **Balances:** when I pay $90 split three ways, I'm +$60 and the others are −$30 each.
  Pennies go to the payer first, then down the People list.
- **Views (sidebar):** Me = my shares, a flatmate = their shares, Household = full totals.
- **Recurring bills:**
  1. Click a transaction and press **Make recurring**.
  2. In **Recurring bills**, pick the month, tick the bills and edit them in the table.
  3. Press **Add N selected**.

  **Open in Quick add** is for changing the split or the people.
- **Import order for an old spreadsheet:** Category sheet, then Transactions, then Savings.
  - Compare **Totals per category type** with the spreadsheet before importing.
  - **Check rows against Excel row numbers** explains missing rows.
  - **Undo last import** reverts the last one.
- **Bank statements:** description rules ("contains COSTCO" → groceries). Unmatched rows go to a
  default subcategory.
- **Backups:**
  - Automatic daily backups are kept in `backups/` (the newest 30).
  - To restore: stop the app and copy a backup over `data/finance.db`.
  - Suggest an off-site copy outside cloud sync.
- **Cloud folders:** keep the virtual environment outside iCloud / Dropbox / OneDrive.

### Helping with an import problem without seeing the data

- **Totals differ from the spreadsheet:** ask which spreadsheet cell they compare against
  (household total or their own share). Ask whether rows are hidden or unsaved, and what the
  Import page's row readout and "stated total differs" warning say.
- **The importer itself seems wrong:** reproduce it with a fake workbook (see
  `tests/test_importer.py`, which builds workbooks in memory with the real header names).

---

## B. Changing the code

### Stack

- Python 3.11+, Streamlit (multipage via the `pages/` folder), SQLite, pandas, Plotly, openpyxl.
- Tests: pytest, with Streamlit's `AppTest` for headless page tests.

### Commands

```bash
python3 -m venv ~/.venvs/finance-tracker                      # once; outside cloud-synced folders
~/.venvs/finance-tracker/bin/pip install -r requirements-dev.txt
~/.venvs/finance-tracker/bin/python -m pytest -q              # full suite (~15 s)
~/.venvs/finance-tracker/bin/python -m pytest -q tests/test_money.py -k split   # a subset

# Run on FAKE data while developing (never the real database):
FINANCE_DB=data/dev_demo.db FINANCE_BACKUPS=data/dev_backups \
  ~/.venvs/finance-tracker/bin/streamlit run app.py --server.port 8765 --server.fileWatcherType auto
```

Fill the dev database with fake data:

```bash
FINANCE_DB=data/dev_demo.db ~/.venvs/finance-tracker/bin/python -c \
  "from core import db, demo; c = db.connect(); db.init_schema(c); demo.load_demo(c); c.commit()"
```

### Map

| File | What lives there |
|---|---|
| `app.py` | Transactions page: quick add (Bill / Income and Saving forms), recurring bills, table, edit / delete, Excel download |
| `pages/1_Insights.py` … `6_Import.py` | One script per page |
| `core/money.py` | Pure money logic: parsing, formatting, splits, signs, balances, settle-up plan. No database. |
| `core/db.py` | Schema, `MIGRATIONS`, starter lists, SQL views `v_transactions` / `v_shares`, every write |
| `core/analytics.py` | Loads the views into pandas, periods and filters, `aggregate()`, KPI and dashboard tables |
| `core/ui.py` | `page()` frame and navigation (`PAGES`), sidebar filters, cached `data()`, `invalidate()`, `flash()`, KPI cards, breakdown tables, `plain()`, `md()` |
| `core/export.py` | Excel (`workbook_bytes`), CSV (`csv_bytes`), JSON export / import |
| `core/importer.py` | Spreadsheet and bank import: header detection, mapping, row building, review, apply, undo |
| `core/backup.py`, `core/demo.py` | Backups; deterministic fake demo data |
| `analyses/*.py` | One built-in chart per file, auto-discovered by Insights |
| `tests/` | `cases.json` golden cases, unit tests, page tests, `test_security.py` |

### Data flow

```
SQLite tables → views v_transactions / v_shares → analytics.load_frames()
→ ui.data() (cached) → pages
```

A write goes through `db.session()`, then `ui.invalidate()`, and the page reruns with fresh data.

### Rules for every change

**Money**

- Always integer **cents**. Never do arithmetic on floats.
- Use `money.parse_amount`, `money.fmt_money`, `money.compute_split` and `money.scale_shares`.
- Dollars appear only for display (`ui.dollars`) and in Excel.
- A transaction's `total_cents` must equal the sum of its shares; `db.save_transaction` enforces this.
- `direction` is `"debit"` (money out, −) or `"credit"` (money in, +).
- Savings: `saved = −signed`. Savings rows read **Deposit** / **Withdraw** (`money.direction_label`).

**Database**

- All SQL lives in `core/db.py` (the importer and export use its helpers).
- Always use `?` parameters. Table and column names may only come from constants in the code,
  never from user input or file contents.
- Primary keys are UUID strings (`db.new_id()`). Rows carry `created_at` / `updated_at` (`db.now()`).
- **Schema change:** add `MIGRATIONS[n]` and bump `SCHEMA_VERSION`. Never edit an old migration.
  Views are dropped and re-created on every start, so edit `VIEWS_SQL` freely.
- If you add a table, add it to `TABLES` (JSON export / import) and, if it is optional for old
  exports, to `OPTIONAL_TABLES`.
- Starter lists (`SEED_*`) are applied only to a brand-new database. Keep them generic, with no
  real names or banks.

**Streamlit**

- Each page is a plain script that starts with `d, filters = ui.page("Title")`. Do not wrap page
  bodies in functions.
- New pages: add the file under `pages/` **and** an entry in `ui.PAGES`.
- Read data with `ui.data()` (cached). After any write call `ui.invalidate()`. Show a message after
  the rerun with `ui.flash(...)`.
- Forms keep their values in `st.session_state` under prefixed keys:
  - `qa_` quick add, `ed_` edit, `sv_` saving quick add, `es_` saving edit
  - `f_` global filters, with widgets keyed `w_`.

  Buttons use `on_click` callbacks that change state before the rerun. Keep keys unique and stable.
  Tests click widgets by key.
- Use `width="stretch"` / `width="content"`, never `use_container_width`. Prefer Material icons
  (`:material/name:`). Use sentence case in labels.
- Long reruns are not an issue at this data size. Do not add threads, background jobs or timers.

**Security** (`tests/test_security.py` checks part of this)

- **User-entered or file-derived text** shown through Markdown goes through `ui.plain()`. Markdown
  surfaces include `st.markdown`, `st.caption`, alerts, toasts, and widget **labels**: checkbox,
  text input, button and expander labels all render Markdown. Text you write yourself that
  contains `$` amounts goes through `ui.md()`.
- **HTML** only via `st.html` with every value passed through `html.escape`.
- **Downloads** go through `export.workbook_bytes` / `export.csv_bytes`, which keep text like
  `=HYPERLINK(...)` from becoming a formula. Never call `to_excel` / `to_csv` directly for
  downloads.
- **Uploads** are read in memory and never written to disk. Importer messages name the row number
  and column only, never cell values.
- Database and backup files are created owner-only (`db.make_private`). Keep it that way.
- Dependencies go in `requirements.txt` with lower and upper bounds. Add one only when the standard
  library or an existing dependency can't do the job.

**Tests**

- Every behaviour change comes with a test. Run the **full** suite before saying you are done.
- Logic goes in unit tests (`test_money.py`, `test_db.py`, `test_analytics.py`, `test_importer.py`, …).
  Page flows go in `test_pages.py` with `AppTest` (see the `app_db` fixture and `_run()` helper).
- Golden numbers live in `tests/cases.json`. Change expected values only when the rule truly
  changed, and say so.
- Tests must never touch `data/finance.db`. Use the `conn` / `ledger` fixtures or `app_db`.

**Third-party material**

- The app was inspired by a free spreadsheet template (see README → Acknowledgements). Never add
  files, formulas, text, screenshots or images from that template, its guide or video, or any other
  third-party source. Write everything from scratch, and keep the Acknowledgements accurate.

**Style**

- Small functions with a docstring that says what and why. Plain English in the UI ("Money out",
  not "debit").
- Keep the existing structure. Avoid drive-by refactors in unrelated files.

### Adding a chart (the most common request)

1. Copy `analyses/expense_by_category.py` to `analyses/<name>.py`.
2. Set `TITLE` and `ORDER`, and `TAB = "Savings"` if it belongs on the Savings tab. Implement
   `build()` (pure, returns a frame) and `render()`.
3. Add a test to `tests/test_analyses.py` that checks `build()` on the ledger fixture.
4. Insights picks the file up automatically. No registration needed.

### Before you say "done"

- [ ] `python -m pytest -q` passes (report the count).
- [ ] The change was checked on fake data (dev database or demo data), not real data.
- [ ] `README.md` is updated if anything user-visible changed (labels, steps, behaviour).
- [ ] The person knows to **restart** the app to see the change.
- [ ] `git status` shows no data, backup, spreadsheet, CSV or export files.
