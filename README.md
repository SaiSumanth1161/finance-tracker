# 💸 Finance Tracker
### *For people who share a flat, a fridge, and a deeply complicated Costco receipt.*

You split rent three ways. Someone paid for the internet. Someone else "will pay you back for the
groceries." It is now March, and nobody remembers who owes whom for January's electricity bill.

This app fixes that. It is a private money tracker that runs **on your own computer**. You enter
your bills, it does the math down to the last penny, draws you some honestly rather pretty
charts, and tells everyone exactly how much they owe. No more spreadsheets with 14 tabs called
`Final`, `Final_v2`, and `Final_ACTUAL_use_this_one`.

> **No accounts. No cloud. No bank logins. No ads. No "Premium tier".**
> Your money data lives in one file on your laptop and never goes anywhere. It doesn't even phone
> home to say hi.

---

## 🧭 Table of contents

1. [What it does](#-what-it-does-the-highlight-reel)
2. [Install it](#-install-it-about-5-minutes-most-of-it-is-waiting-for-pip)
3. [Run it](#-run-it)
4. [First start: make it yours](#-first-start-make-it-yours)
5. [A tour of the pages](#-a-tour-of-the-pages)
6. [How the money math works](#-how-the-money-math-works-aka-where-did-that-extra-penny-go)
7. [Recurring bills](#-recurring-bills-rent-the-gift-that-keeps-on-taking)
8. [Importing old spreadsheets and bank statements](#-importing-old-spreadsheets-and-bank-statements)
9. [Your data and privacy](#-your-data-and-privacy-the-serious-bit)
10. [Backups](#-backups-because-hard-drives-have-feelings-too)
11. [Troubleshooting](#-troubleshooting)
12. [FAQ](#-faq-frequently-avoided-questions)
13. [For developers (and their AI assistants)](#-for-developers)
14. [License](#-license)

---

## ✨ What it does (the highlight reel)

- **Splits bills four ways:** equally, by exact amounts, by percent, or "only me" (for that
  impulse purchase you'd rather not discuss).
- **Knows who owes whom** and suggests the *fewest possible payments* to settle everyone up.
  It even writes a little summary you can paste straight into the group chat. Passive-aggressive
  emoji not included.
- **Dashboards** for income, expenses, savings rate, spending by category and by person, month
  vs month, and the top 10 things you spend money on (brace yourself).
- **Savings tracking**, kept separate from spending, so moving $500 into your savings account
  doesn't look like you bought a very expensive sandwich.
- **Monthly budgets** per category, with a polite red flag when you blow through one.
- **Recurring bills**: rent, internet, and subscriptions added in two clicks every month.
- **Imports** your old spreadsheet and your bank's CSV statements, with rules like
  "anything containing COSTCO is groceries".
- **Build-your-own charts** without writing a line of code, then pin them to your dashboard.
- **Excel export** for when your accountant (or your mum) asks.
- **Automatic daily backups**, because we've all been there.

---

## 🛠 Install it (about 5 minutes, most of it is waiting for pip)

You need **Python 3.11 or newer**. Check with:

```bash
python3 --version
```

Then:

```bash
git clone <this repository> finance-tracker
cd finance-tracker
python3 -m venv ~/.venvs/finance-tracker
~/.venvs/finance-tracker/bin/pip install -r requirements.txt
```

> 🧊 **Pro tip:** keep that virtual environment (`~/.venvs/...`) **outside** iCloud, Dropbox,
> OneDrive or Google Drive folders. It contains thousands of tiny files, and sync tools will
> spend the rest of their lives trying to upload them while your fan sounds like a jet engine.
> (Ask us how we know.)

---

## ▶️ Run it

```bash
cd finance-tracker
~/.venvs/finance-tracker/bin/streamlit run app.py
```

Open **http://127.0.0.1:8501** in your browser. That's it. You're doing finance now.

- **To stop it:** press `Ctrl+C` in the terminal.
- **After updating the code:** stop it and start it again. The app deliberately doesn't watch its
  own files (it saves CPU and keeps cloud-sync tools calm).

**Want a shortcut?** Add an alias so typing `fin` starts the app (macOS / Linux, zsh):

```bash
echo "alias fin='cd ~/finance-tracker && ~/.venvs/finance-tracker/bin/streamlit run app.py'" >> ~/.zshrc
```

(Change `~/finance-tracker` to wherever you cloned it, then open a new terminal tab.)

---

## 🏠 First start: make it yours

The first start creates `data/finance.db` with some starter lists. They're placeholders, like the
"Lorem ipsum" of household finance. Open **Setup** and fix them:

| Tab | What to do |
|---|---|
| **People** | Type your flatmates' real names over **Roommate 1** and **Roommate 2**. Add more, or untick *Active* for someone who moved out (their history stays). The row marked **Me** is you. You can't delete yourself, no matter how bad the month was. |
| **Payment modes** | Replace the sample cards (visa, mastercard, …) with your own. Set the kind: credit card, debit card, bank, cash or app. |
| **Categories** | Rename, add or remove. The **type** decides how a category counts: Income, Expense, Savings, or Not Reported (ignored everywhere, which is perfect for moving money between your own accounts). |
| **Subcategories** | What you actually pick when entering a bill: `rent`, `groceries`, `outside food`, `the 3am pizza`. |
| **Budgets** | A monthly limit per expense category, in dollars. Leave it empty if you prefer to live dangerously. |

**Just want to poke around first?** Press **Load demo data** in Setup. You get about 150
made-up transactions over six months. **Clear demo data** removes exactly those and never touches
your own entries. (The app keeps a list of demo IDs, so it can't get confused.)

> Anything in use can't be deleted (the app will say so and change nothing). Untick *Active*
> instead: it disappears from the forms but your history stays intact.

---

## 🗺 A tour of the pages

### 📝 Transactions (the home page)

This is where you'll live.

**Quick add** has two tabs:

- **Bill / Income:** date, description, amount, direction (money out or money in), subcategory,
  payment mode and split. A live preview shows the total, the signed amount and *your* share
  before you save.
- **Saving:** a short form for moving money into savings (**Deposit**) or out of it
  (**Withdraw**). No splits, no fuss.

Some small things that save a lot of typing:

- **Smart defaults:** type a description you've used before ("Costco") and the subcategory,
  payment mode and split fill in from last time. The app remembers so you don't have to.
- **Save & add another** keeps the date, payment mode and split, so you can rattle through a
  pile of receipts.
- **Duplicate last** copies the last transaction you entered *including its date*, which is
  handy for "same thing, slightly different amount".
- **More options** hides *Paid by* (when a flatmate paid) and *Notes*.

Below that sits **every transaction** in a table with search and filters (type, category,
person, payment mode). **Click a row to edit or delete it.** Totals sit underneath, and
**Download Excel** exports what you're looking at (or everything).

### 📊 Insights

Your money, but make it charts.

- **Income & Expense tab:** KPI cards (income, expense, net, savings rate, my share vs the
  flatmates' share), pivot-style tables with category → subcategory and little data bars,
  built-in charts, and **Budget vs spent** with a warning when a category goes over.
- **Savings tab:** saved, withdrawn, net saved, and *unallocated* (money you earned but didn't
  spend or save. Where did it go? A mystery for the ages).
- Your own **pinned views** from Explore show up here too.

### 🔬 Explore

A chart builder for the curious. Pick a measure, group by up to two things (month, category,
person, payment mode, …), add filters and choose a chart type. Like what you made? **Save** it,
or **Pin to Insights** so it greets you every time. Download as CSV or Excel.

### 🤝 Settle up

The page that keeps friendships alive.

- A card per person: "*Sam owes you $142.17*" (or, awkwardly, "*you owe*").
- A suggested plan with the fewest payments that clears everyone.
- **Record a settlement** when someone pays up, and the balances update.
- A **plain-text summary** to paste into the group chat. Use the copy icon on the box.

### ⚙️ Setup

People, payment modes, categories, subcategories, budgets and demo data. See
[First start](#-first-start-make-it-yours).

### 💾 Backup

Back up now, see the last backup time, and export or import **everything as JSON** (a readable
full copy, handy for moving to a new computer). Importing JSON replaces all data, so the app shows
a preview and asks first.

### 📥 Import

Bring in history from an old spreadsheet or a bank statement. See
[Importing](#-importing-old-spreadsheets-and-bank-statements).

### 🎛 The sidebar

**Year**, **Month**, quick presets (*This month*, *Last month*, *Last 3 months*, *YTD*, *Custom*)
and **View** apply on every page and stay put when you switch pages. No year or month picked
means "all of it".

**View** answers "whose money am I looking at?":

| View | Shows |
|---|---|
| **Me** (default) | Your shares only. "What did *I* spend?" |
| **A flatmate's name** | Their shares only |
| **Household** | Full totals for everyone, spreadsheet-style |

---

## 🧮 How the money math works (aka "where did that extra penny go?")

**Money is stored as whole cents.** Not floats. Never floats. So $0.10 + $0.20 is always exactly
$0.30 here, unlike in some programming languages we won't name.

**Direction decides the sign:** money out is −, money in is +. A refund is *money in* under the
expense it came from, so it lowers that expense.

**The famous $90 example.** You pay a $90 internet bill, split equally among you, Alex and Sam:

| Person | Share | Balance |
|---|---|---|
| You (paid) | $30.00 | **+$60.00** (you're owed) |
| Alex | $30.00 | −$30.00 |
| Sam | $30.00 | −$30.00 |

**Pennies that don't divide evenly** go to the payer first, then down the People list in order.
So $100.00 split three ways becomes $33.34 (payer), $33.33 and $33.33. Every share always adds up
to exactly the total, every time. No penny is ever lost, created or quietly pocketed.

**Savings** count the other way round: moving money *into* savings is **+ saved**, taking it back
out is **− saved**.

**Category types:**

| Type | Where it counts |
|---|---|
| Income | Income totals and the savings rate |
| Expense | Spending, budgets, charts |
| Savings | Only on the Savings tab |
| Not Reported | Nowhere (transfers between your own accounts, settling up with flatmates) |

---

## 🔁 Recurring bills (rent: the gift that keeps on taking)

1. In the transactions table, click a bill (say, Rent) and press **Make recurring**. It
   remembers the amount, split, payment mode and day of the month.
2. Each month, open **Recurring bills** on the Transactions page and pick the month (last, this
   or next).
3. **Tick the bills to add.** Anything already entered that month starts unticked, so you won't
   pay rent twice (in the app, anyway).
4. Different this month? **Edit the date, description, amount, payment mode or notes right in
   the table.** A new amount is split the same way: equal stays equal, and other splits keep each
   person's part.
5. Rent actually went up? Tick **Also save … as the new default**. (Our condolences.)
6. Press **Add N selected**.

Need to change *who* is on the bill or *how* it's split this month? Pick the bill and press
**Open in Quick add**, where every field is editable.

---

## 📥 Importing old spreadsheets and bank statements

Upload an `.xlsx` or `.csv` on the **Import** page. The file is read **in memory only**. It's
never saved, a backup is made before anything is written, and **Undo last import** removes
exactly what the last import added. Nothing permanent until you say so.

### An old spreadsheet

Do one sheet at a time, in this order:

1. **Category sheet:** adds your categories and subcategories (both side-by-side tables are read).
   It never changes existing ones.
2. **Transactions sheet:** the bills.
3. **Savings sheet:** deposits and withdrawals.

Along the way:

- **The header row is found automatically**, even if your table starts on row 7 under a title
  and some motivational quotes. If the guess is wrong, use *Show the top of this sheet* and the
  *Header row* box.
- **Check the column mapping.** Person columns (one per flatmate) are found between *Debit Total*
  and *Sub Category*.
- **Compare *Totals per category type* with your spreadsheet** before pressing Import. If they
  match, you're golden.
- **Rows that don't add up?**
  - *Check rows against Excel row numbers* shows every row that wasn't read, and why (blank line,
    problem, outside the table).
  - Rows whose Total differs from the sum of the person columns are listed too. Usually that's a
    typo in the old sheet, not in the app. Spreadsheets lie; cents don't.
- **Payment mode missing** on some rows? Pick one for them from the dropdown.
- Rows already in the app (same date, description, direction and total) are skipped, so
  importing twice is harmless.

### A bank statement

Upload the bank's CSV (or Excel) export and choose **Bank statement**.

- **Description rules** pick the subcategory: `contains COSTCO → groceries`,
  `contains NETFLIX → subscriptions`. Rules are tried top to bottom.
- Rows no rule matches go to a **default subcategory** and are listed, so you can add rules and
  watch them sort themselves.
- Choose whether a negative amount means money out, or map separate debit and credit columns.
- Importing the same statement twice adds nothing.

---

## 🔒 Your data and privacy (the serious bit)

We'll drop the jokes for a minute. This is your financial life.

- **Everything stays on your computer**, in this folder: `data/finance.db` and `backups/`.
- **Only your user account can read them.** The app creates them with owner-only permissions.
- **They're excluded from git** (see `.gitignore`), along with spreadsheets, CSVs, exports and
  secrets. Pushing your code never pushes your money.
- **The app only listens on `127.0.0.1`**, so other devices on your Wi-Fi can't open it. Don't
  change the address to `0.0.0.0` unless you enjoy your flatmate's cousin browsing your spending.
- **No telemetry, no network calls.** Usage statistics are switched off.
- **Downloads are safe to open.** Excel and CSV exports store text exactly as typed, so a
  description like `=HYPERLINK(...)` from a dodgy bank statement can never run as a formula.
- **Text is shown exactly as typed.** A name or description can't sneak in formatting or load
  images from the internet.
- **Uploaded spreadsheets** are parsed with hardened XML handling (`defusedxml`).
- **Turn on disk encryption** (FileVault on a Mac, BitLocker on Windows) so a lost laptop doesn't
  become a lost bank history.
- **Never paste real transactions into AI chatbots, bug reports or screenshots.** Use demo data.

---

## 💾 Backups (because hard drives have feelings too)

- **Automatic:** once a day when the app is open (if the last backup is older than 24 hours).
- **Manual:** **Backup → Back up now**, any time.
- **Kept:** the newest 30, in `backups/finance_YYYYMMDD_HHMM.db`.
- **Warning:** the Backup page nags you if the last backup is more than 7 days old.
- **To restore:** stop the app, copy a file from `backups/` over `data/finance.db`, start again.
- **Off-site:** now and then, copy `backups/` to a drive *outside* your cloud sync. If the database
  ever gets damaged, cloud sync will helpfully sync the damage everywhere.

---

## 🧯 Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'plotly'` (or streamlit, pandas, …) | You're using the system Python instead of the virtual environment. Run `~/.venvs/finance-tracker/bin/streamlit run app.py` (full path), or reinstall with `~/.venvs/finance-tracker/bin/pip install -r requirements.txt`. |
| The page opened on port **8502** instead of 8501 | An old copy is still running. Find its terminal and press `Ctrl+C`, then start again. |
| A change in the code doesn't show up | Stop and start the app (file watching is off on purpose). |
| `AttributeError` right after updating the code | Same thing: a running app keeps the old code. Restart it. |
| iCloud / Dropbox is stuck syncing forever and your fan is screaming | Move the virtual environment outside the synced folder (see [Install](#-install-it-about-5-minutes-most-of-it-is-waiting-for-pip)). In Finder, right-click the project folder → **Keep Downloaded**. |
| Import says *"No column names were found on that row"* | Set **Header row** to the row that holds the column names. *Show the top of this sheet* helps. |
| Import totals differ from the spreadsheet | Use *Check rows against Excel row numbers*. Also save the spreadsheet first: unsaved rows aren't in the file you uploaded. |
| `command not found: compdef` after editing `~/.zshrc` | Harmless and unrelated to the app. Add `autoload -Uz compinit && compinit` near the top of `~/.zshrc`, or ignore it. |

---

## 🙋 FAQ (Frequently Avoided Questions)

**Does it connect to my bank?**
No, on purpose. You download a CSV from your bank and import it. Your bank password never comes
near this app.

**Can my flatmates use it on their phones?**
Not directly. It runs on one computer and only that computer can open it. Share the **Settle up**
summary in your group chat instead. (A phone version could use the JSON export one day.)

**Can each flatmate run their own copy?**
Absolutely. Everyone clones the repository and gets their own private database. Nothing is shared
between copies.

**Is it accurate to the cent?**
Yes. Everything is integer cents, every split adds up to exactly the total, and there's a test
suite that checks pennies, signs and balances against golden cases.

**What if I enter something wrong?**
Click it in the table, fix it, press **Update**. Or **Delete** it. Imported rows can be undone in
one go with **Undo last import**.

**Why is my savings rate so low?**
That's between you and your takeout habit. The app just counts.

**Is my data really not uploaded anywhere?**
Really. There's no server, no account and no analytics. Unplug your Wi-Fi and it works the same.

---

## 👩‍💻 For developers

### Run the tests

```bash
~/.venvs/finance-tracker/bin/pip install -r requirements-dev.txt
~/.venvs/finance-tracker/bin/python -m pytest -q
```

- `tests/cases.json`: golden cases for splits, pennies, signs, balances and KPIs, plus a small
  ledger. A future JavaScript port can reuse it.
- `test_pages.py`: runs every page headless (no browser) on empty and demo data, and clicks
  through the main flows.
- `test_security.py`: data stays out of git, the app is local-only, and text from people or files
  is shown and exported safely.
- Tests never touch `data/finance.db`. They use in-memory or temporary databases.

### Develop with fake data

```bash
FINANCE_DB=data/dev_demo.db FINANCE_BACKUPS=data/dev_backups \
  ~/.venvs/finance-tracker/bin/streamlit run app.py --server.fileWatcherType auto
```

`FINANCE_DB` points the app at a separate database, so development never opens the real one, and
`--server.fileWatcherType auto` reloads the page when you save a file.

### 🤖 Working with an AI assistant

Hand your assistant **[`AGENTS.md`](AGENTS.md)**. Claude Code, Codex, Cursor and friends pick it up
automatically (`CLAUDE.md` simply loads it). It explains:

- how to help someone **use** the app (install, setup, imports, troubleshooting), and
- how to **change** it safely: the money rules, where things live, the security rules, and how to
  test.

Most importantly, it tells the assistant to **never read your real data**. It works from the code,
demo data and fake files, and asks you for things like column headers and row counts instead of
your actual transactions. Your bank history stays between you and your laptop.

### Rules the code follows

- **Money is integer cents.** It becomes dollars only when shown or written to Excel.
- **Every database read is cached**, and every write calls `ui.invalidate()`, so pages refresh on
  the same click.
- **Text from people or files** goes through `ui.plain()` before being shown with Markdown.
- **Exports** go through `export.workbook_bytes()` / `export.csv_bytes()`, which neutralise
  formulas.
- **SQL** always uses `?` parameters. Table and column names only ever come from constants in the
  code.

### Make changes

- **Add a chart without code:** build it in Explore and press *Pin to Insights*.
- **Add a chart with code:** create one file in `analyses/` with `TITLE`, `ORDER`, `build()` and
  `render()` (copy `analyses/expense_by_category.py`), plus a test in `tests/test_analyses.py`.
  Insights finds it automatically. Add `TAB = "Savings"` to put it on the Savings tab.
- **Change the database:** add the next number to `MIGRATIONS` in `core/db.py` and bump
  `SCHEMA_VERSION`. The SQL views are rebuilt on every start, so edit them freely.

### Folder layout

```
app.py                      Transactions (home page)
pages/                      Insights, Explore, Settle up, Setup, Backup, Import
core/money.py               splits, signs, balances (pure Python, no database)
core/db.py                  schema, migrations, starter lists, SQL views, all writes
core/analytics.py           data frames, periods, aggregations, dashboard tables
core/export.py              Excel / CSV export, JSON export / import
core/importer.py            spreadsheet and bank statement import (pure logic)
core/backup.py              database backups
core/demo.py                demo data generator
core/ui.py                  page frame, sidebar filters, KPI cards, tables, chart style
analyses/                   one file per built-in chart (found automatically)
tests/                      golden cases and tests
.streamlit/config.toml      local-only settings
data/  backups/             your data (never in git)
```

### Before every commit

```bash
git status                  # data/, backups/, *.db, *.xlsx, *.csv must NEVER appear here
python -m pytest -q
```

If something private gets committed by mistake, deleting it in a later commit is **not enough**:
git history remembers everything, like that one flatmate who still brings up the 2023 electricity
bill.

### Decisions made while building

- **Balance sign:** when I pay $90 split three ways, I'm +$60 and each flatmate is −$30.
- **Default view is Me**, so Net and Savings rate describe your own money. Household shows the
  spreadsheet-style totals.
- **Month and Year pills:** none selected means all.
- **Charts draw spending as positive bars**; tables keep the signs (expenses negative, in red).
- **"Last 3 months"** across a year boundary switches to a custom date range, because month pills
  can't express Nov–Jan.
- **Grouping by Person** in Explore always shows everyone, so the View filter isn't applied there.
- **Demo rows** are tracked by ID, so *Clear demo data* can never delete a real entry.
- **Duplicate last** keeps the original date.

---

## 📜 License

[MIT](LICENSE). In plain English:

- **You can** use it, copy it, change it, share it, and even build something bigger on top of it.
- **Please keep** the copyright notice and the license text with any copy.
- **No warranty:** it's free software made with love, not a bank. If your budget still doesn't
  balance, that's between you and the 3am pizza.

---

<p align="center"><i>Made with integer cents, mild spreadsheet trauma, and a strong belief that
nobody should have to chase a flatmate for $14.37.</i></p>
