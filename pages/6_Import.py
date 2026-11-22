"""Import: the old spreadsheet (once) and bank statements (any time). SPEC phase 4.

Upload a file, pick a sheet, check the column mapping, read the summary, then import.
- Old spreadsheet: one sheet at a time. Category sheet first (it only adds categories and
  subcategories), then the Transactions sheet, then the Savings sheet.
- The header row is found automatically; tables do not have to start on row 1.
- Bank statement (CSV or Excel): rows have no category, so description rules pick the
  subcategory ("contains COSTCO" -> groceries). Rows already in the app are skipped.

Privacy: the file is read in memory by this app on this laptop. It is not saved to disk
and nothing leaves the machine. A backup is made before anything is written, and
"Undo last import" removes exactly the rows the last import added.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import backup, db, importer, money, ui

NONE, CREATE = "(none)", "(create new)"

d, filters = ui.page("Import")
ss = st.session_state
me = ui.me_name(d)

st.caption("Import your old spreadsheet, one sheet at a time (Category sheet first, then Transactions, then Savings), "
           "or a bank statement. The file stays on this laptop and is only read in memory.")
uploaded = st.file_uploader("Spreadsheet (.xlsx) or bank statement (.csv)", type=["xlsx", "csv"], key="imp_file")

if uploaded is not None:
    data = uploaded.getvalue()
    is_csv = uploaded.name.lower().endswith(".csv")
    try:
        sheets = ["(csv file)"] if is_csv else importer.sheet_names(data)
    except Exception:
        st.error("This file could not be opened as an Excel workbook.")
        st.stop()

    c1, c2, c3 = st.columns([2, 3.4, 1], vertical_alignment="bottom")
    sheet = c1.selectbox("Sheet", sheets, key="imp_sheet")
    kinds = [importer.BANK] if is_csv else [importer.TRANSACTIONS, importer.SAVINGS, importer.CATEGORIES, importer.BANK]
    guessed_kind = (importer.BANK if is_csv else importer.CATEGORIES if "categor" in sheet.lower()
                    else importer.SAVINGS if "saving" in sheet.lower() else importer.TRANSACTIONS)
    kind = c2.segmented_control("This sheet holds", kinds, default=guessed_kind, required=True,
                                key=f"imp_kind_{is_csv}_{sheet}")

    # Read the sheet once per file and sheet (kept in memory only), then find the header row.
    file_key = (getattr(uploaded, "file_id", uploaded.name), sheet)
    try:
        if is_csv:
            raw, found = None, 1
        else:
            if ss.get("imp_raw_key") != file_key:
                ss["imp_raw"], ss["imp_raw_key"] = importer.read_raw(data, sheet), file_key
            raw = ss["imp_raw"]
            found = importer.detect_header_row(raw)
        header_row = c3.number_input("Header row", min_value=1, value=found or 1, step=1, disabled=is_csv,
                                     key=f"imp_header_{file_key[0]}_{sheet}",
                                     help="The Excel row number that holds the column names. It is found "
                                          "automatically; change it if the guess is wrong.")
        frame = importer.read_csv(data) if is_csv else importer.frame_from_raw(raw, int(header_row))
    except Exception:
        st.error("This file could not be read. Check that it is a plain CSV or Excel export.")
        st.stop()
    if raw is not None:
        with st.expander("Show the top of this sheet (to check the header row)"):
            top = raw.head(15).copy()
            top.index = [f"Row {i + 1}" for i in range(len(top))]
            top.columns = [f"Col {i + 1}" for i in range(top.shape[1])]
            st.dataframe(top.astype(str).replace({"nan": "", "None": "", "NaT": ""}), alt="First rows of the sheet")
    columns = list(frame.columns)
    if raw is not None and found is None:
        st.warning("The header row could not be found on this sheet. It may be an analysis sheet, or the column "
                   "names are unusual: open the box above and set Header row to the row with the column names.")
    if not columns:
        st.warning(f"Row {int(header_row)} has no column names. Set Header row to the row that holds them.")
        st.stop()
    guess = importer.guess_mapping(columns, kind, [p["name"] for p in d["people"]])
    tag = f"{sheet}_{kind}_{header_row}"                  # new sheet or kind -> fresh mapping widgets

    def pick(label: str, field: str, column, required: bool = False):
        """A selectbox that maps one field to a sheet column, pre-filled from the header names."""
        options = columns if required else [NONE] + columns
        default = guess.get(field)
        index = options.index(default) if default in options else 0
        choice = column.selectbox(label, options, index=index, key=f"imp_{field}_{tag}")
        return None if choice == NONE else choice

    if kind == importer.CATEGORIES:
        # ---- Category sheet: adds categories and subcategories only, never transactions.
        st.markdown("**Column mapping**")
        st.caption("The sheet can hold two tables side by side. Leave a table on “(none)” to skip it.")
        m = st.columns(3)
        mapping = {"category": pick("Table 1: category", "category", m[0]),
                   "subcategory": pick("Table 1: subcategory", "subcategory", m[1]),
                   "category_type": pick("Table 1: category type", "category_type", m[2])}
        m = st.columns(3)
        mapping.update({"category2": pick("Table 2: category", "category2", m[0]),
                        "subcategory2": pick("Table 2: subcategory", "subcategory2", m[1]),
                        "category_type2": pick("Table 2: category type", "category_type2", m[2])})
        m = st.columns(3, vertical_alignment="bottom")
        default_type2 = m[0].selectbox("Table 2 type when its type cell is empty", db.CATEGORY_TYPES,
                                       index=db.CATEGORY_TYPES.index("Savings"), key=f"imp_type2_{tag}")
        fill_down = m[1].checkbox("An empty category cell repeats the one above", value=True, key=f"imp_fill_{tag}")
        if not mapping["category"] and not mapping["category2"]:
            st.warning("Map the category column of at least one table.")
            st.stop()
        items, problems = importer.build_categories(frame, mapping, default_type2, fill_down)
        with db.session() as conn:
            summary = importer.review_categories(conn, items)

        st.markdown("**What will be added**")
        k = st.columns(4)
        k[0].metric("New categories", f"{summary['new_categories']:,}")
        k[1].metric("New subcategories", f"{summary['new_subcategories']:,}")
        k[2].metric("Already in the app", f"{summary['already_there']:,}")
        k[3].metric("Left alone (conflicts)", f"{summary['conflicts']:,}",
                    help="A subcategory name used by another category, a category with a different type, or an unknown type.")
        listing = pd.DataFrame([{"Row": i["row"], "Category": i["category"], "Subcategory": i["subcategory"],
                                 "Type": i["category_type"], "Status": i["status"]} for i in summary["items"]],
                               columns=["Row", "Category", "Subcategory", "Type", "Status"])
        st.dataframe(listing, hide_index=True, height=min(420, 38 * (len(listing) + 1) + 4), alt="Categories found in the sheet")
        if problems:
            with st.expander(f"{len(problems)} row(s) cannot be read and will be skipped"):
                st.text("\n".join(problems[:200]))
        to_add = summary["new_categories"] + summary["new_subcategories"]
        if not to_add:
            st.info("Nothing to add: everything in this sheet is already in the app.")
        elif st.button(f"Add {to_add} new categories and subcategories", type="primary", key="imp_cat_go"):
            backup.backup_now()
            with db.session() as conn:
                created = importer.apply_categories(conn, items)
            ui.invalidate()
            ui.flash(f"Added {created['categories']} categories and {created['subcategories']} subcategories. "
                     "Rename or remove them in Setup.")
            st.rerun()
        st.stop()

    st.markdown("**Column mapping**")
    m = st.columns(4)
    mapping = {"description": pick("Description", "description", m[0]),
               "date": pick("Date", "date", m[1], required=True)}
    if kind != importer.BANK:
        mapping["subcategory"] = pick("Subcategory", "subcategory", m[2], required=True)
        mapping["category"] = pick("Category", "category", m[3])
    default_mode_id, positive_is_deposit = None, True
    options = {}                                          # extra arguments for build_rows (bank statements)
    modes = {x["id"]: x["name"] for x in d["modes"] if x["active"]}
    if kind == importer.BANK:
        mapping["amount"] = pick("Amount (one signed column)", "amount", m[2])
        m2 = st.columns(4)
        mapping["debit"] = pick("or: Money out column", "debit", m2[0])
        mapping["credit"] = pick("and: Money in column", "credit", m2[1])
        negative_out = m2[2].radio("A negative amount is", ["Money out", "Money in"], horizontal=True,
                                   key=f"imp_bank_sign_{tag}") == "Money out"
        default_mode_id = m2[3].selectbox("Account of this statement", list(modes), format_func=modes.get,
                                          key=f"imp_mode_{tag}")
        if not mapping["amount"] and not mapping["debit"] and not mapping["credit"]:
            st.warning("Map the Amount column, or the Money out / Money in columns.")
            st.stop()
        active_subs = [s["name"] for s in d["subcategories"] if s["active"]]
        fallback = "misc-expense" if "misc-expense" in active_subs else active_subs[0]
        default_sub = st.selectbox("Subcategory for rows no rule matches", active_subs, index=active_subs.index(fallback),
                                   key=f"imp_default_sub_{tag}")
        with st.expander(f"Description rules ({len(d['rules'])})"):
            st.caption("When a description contains the text on the left, the row gets that subcategory. "
                       "The first matching rule wins. Rules are kept for the next statement.")
            sub_ids = {s["name"]: s["id"] for s in d["subcategories"]}
            rules_frame = pd.DataFrame({"Description contains": [r["pattern"] for r in d["rules"]],
                                        "Subcategory": [r["subcategory"] for r in d["rules"]]})
            edited_rules = st.data_editor(
                rules_frame, num_rows="dynamic", hide_index=True, key=f"imp_rules_{len(d['rules'])}_{ss.get('imp_rules_version', 0)}",
                column_config={"Description contains": st.column_config.TextColumn(required=True),
                               "Subcategory": st.column_config.SelectboxColumn(options=list(sub_ids), required=True)})
            if st.button("Save rules", key="imp_rules_save"):
                with db.session() as conn:
                    db.replace_rules(conn, [(row["Description contains"], sub_ids[row["Subcategory"]])
                                            for row in edited_rules.to_dict("records")
                                            if row["Description contains"] and row["Subcategory"] in sub_ids])
                ui.invalidate()
                ss["imp_rules_version"] = ss.get("imp_rules_version", 0) + 1
                ui.flash("Rules saved.")
                st.rerun()
        options = {"rules": d["rules"], "subcategories": d["subcategories"], "default_subcategory": default_sub,
                   "negative_is_out": negative_out}
    elif kind == importer.TRANSACTIONS:
        m = st.columns(4)
        mapping["credit"] = pick("Money in (credit total)", "credit", m[0])
        mapping["debit"] = pick("Money out (debit total)", "debit", m[1])
        mapping["category_type"] = pick("Category type", "category_type", m[2])
        mapping["mode"] = pick("Payment mode", "mode", m[3])
        m = st.columns([3, 1])
        person_columns = m[0].multiselect("Columns with each person's share", columns, key=f"imp_people_{tag}",
                                          default=list(guess.get("people", {})))
        mine = [c for c in person_columns if c.lower() == me.lower()]
        my_column = m[1].selectbox("My column", person_columns or [NONE], key=f"imp_me_{tag}",
                                   index=person_columns.index(mine[0]) if mine else 0)
        known = {p["name"].lower(): p["name"] for p in d["people"]}
        mapping["people"] = {c: (me if c == my_column else known.get(c.lower(), c)) for c in person_columns}
        if not mapping["credit"] and not mapping["debit"]:
            st.warning("Map at least one of the Money in / Money out columns.")
            st.stop()
        # Rows with an empty Mode cell get this payment mode.
        default_mode_id = st.selectbox("Payment mode for rows with an empty Mode cell", list(modes),
                                       format_func=modes.get, key=f"imp_fallback_mode_{tag}")
    else:
        m = st.columns(4)
        mapping["amount"] = pick("Amount", "amount", m[0], required=True)
        positive_is_deposit = m[1].radio("A positive amount is a", ["Deposit", "Withdrawal"], horizontal=True,
                                         key=f"imp_sign_{tag}") == "Deposit"
        default_mode_id = m[2].selectbox("Account for these rows", list(modes), format_func=modes.get, key=f"imp_mode_{tag}",
                                         help="The Savings sheet has no payment mode, so every row gets this one.")

    rows, problems = importer.build_rows(frame, mapping, kind, me, positive_is_deposit, **options)
    with db.session() as conn:
        summary = importer.review(conn, rows)

    st.markdown("**What will be imported**")
    k = st.columns(4)
    k[0].metric("Rows ready", f"{summary['rows']:,}")
    k[1].metric("Rows skipped (problems)", f"{len(problems):,}")
    k[2].metric("Already in the app", f"{summary['duplicates']:,}", help="Same date, description, direction and total.")
    if kind == importer.BANK:
        k[3].metric("No rule matched", f"{summary['unmatched']:,}", help="These rows get the default subcategory.")
    else:
        k[3].metric("New subcategories", f"{len(summary['unknown_subcategories']):,}")
    # Income and expense keep the checking-account sign (money out is negative). Savings is shown
    # as net saved instead (deposits +, withdrawals −), the way the Savings dashboard counts it.
    labels, values = [], []
    for kind_name, cents in summary["signed_total_by_type"].items():
        is_savings = kind_name == "Savings"
        labels.append("Savings (net saved: deposits +)" if is_savings else kind_name)
        values.append((-cents if is_savings else cents) / 100)
    totals = pd.DataFrame({"Category type": labels, "Total": values})
    st.caption("Totals per category type. Income is +, expense is −, savings shows net saved. "
               "Compare them with your spreadsheet before importing.")
    st.dataframe(ui.money_styler(totals, ["Total"]), hide_index=True, alt="Totals per category type")
    gaps = importer.total_mismatches(rows) if kind == importer.TRANSACTIONS else []
    if gaps:
        net = sum(g["gap_cents"] * (1 if g["direction"] == "credit" else -1) for g in gaps)
        st.warning(f"{len(gaps)} row(s) have a Credit / Debit Total that differs from the sum of the person columns. "
                   f"The app uses the person columns, which moves the signed total by {money.fmt_money(abs(net))}. "
                   "Fix those cells in the sheet, or import and correct the rows afterwards.")
        with st.expander("Rows where the stated total and the person columns disagree"):
            st.dataframe(ui.money_styler(pd.DataFrame([{
                "Excel row": g["row"], "Type": g["category_type"],
                "Sheet total": g["stated_cents"] / 100, "Person columns add up to": g["shares_cents"] / 100,
                "Difference": g["gap_cents"] / 100} for g in gaps]),
                ["Sheet total", "Person columns add up to", "Difference"]), hide_index=True)
    if kind == importer.TRANSACTIONS and summary["no_mode"]:
        st.info(f"{summary['no_mode']:,} row(s) have an empty Mode cell and will get "
                f"“{ui.plain(modes.get(default_mode_id, '?'))}” (chosen above).")
    if summary["new_people"]:
        st.info("People that will be added: " + ui.plain(", ".join(summary["new_people"])))
    if summary["new_modes"]:
        st.info("Payment modes that will be added (as credit cards; change the kind in Setup): " + ui.plain(", ".join(summary["new_modes"])))
    if summary["bad_types"]:
        st.error("These category types are not Income, Expense, Savings or Not Reported: "
                 + ui.plain(", ".join(repr(t) for t in summary["bad_types"])) + ". Fix them in the sheet, or map the type column.")
    if problems:
        with st.expander(f"{len(problems)} row(s) cannot be read and will be skipped"):
            st.text("\n".join(problems[:200]))

    if kind == importer.BANK and summary["unmatched"]:
        with st.expander(f"{summary['unmatched']} row(s) matched no rule (add rules above, then they sort themselves)"):
            loose = pd.Series([r["description"] for r in rows if not r["matched"]]).value_counts().head(50)
            st.dataframe(pd.DataFrame({"Description": loose.index, "Rows": loose.values}), hide_index=True)

    subcategory_map = {}
    if summary["unknown_subcategories"]:
        st.markdown("**Subcategories the app does not know yet**")
        st.caption("Keep “(create new)” to add one under the category from the sheet, or pick an existing one to import into.")
        existing = {s["name"]: s["id"] for s in d["subcategories"]}
        table = pd.DataFrame(summary["unknown_subcategories"]).rename(columns={
            "subcategory": "In the sheet", "category": "Category", "category_type": "Type", "rows": "Rows"})
        table["Import as"] = CREATE
        edited = st.data_editor(table, hide_index=True, key=f"imp_subs_{tag}", disabled=["In the sheet", "Category", "Type", "Rows"],
                                column_config={"Import as": st.column_config.SelectboxColumn(options=[CREATE] + list(existing), required=True)})
        subcategory_map = {row["In the sheet"]: existing[row["Import as"]] for row in edited.to_dict("records")
                           if row["Import as"] in existing}

    with st.expander("Check rows against Excel row numbers"):
        cover = importer.coverage(frame, rows, problems)
        if cover["last"] < cover["first"]:
            st.caption("The table has no data rows.")
        else:
            st.caption(f"The table runs from Excel row {cover['first']} to row {cover['last']}. "
                       "Enter the range you counted in Excel to see which rows are not ready. Only row numbers are shown.")
            r = st.columns(2)
            start = r[0].number_input("From Excel row", min_value=1, value=cover["first"], step=1, key=f"imp_from_{tag}")
            end = r[1].number_input("To Excel row", min_value=1, value=cover["last"], step=1, key=f"imp_to_{tag}")
            if end < start:
                st.warning("“To” must not be smaller than “From”.")
            else:
                found = importer.rows_in_range(cover, int(start), int(end))
                c = found["counts"]
                st.write(f"Rows {int(start)} to {int(end)}: **{int(end) - int(start) + 1}** lines in Excel, "
                         f"**{c['ready']}** ready, {c['skipped']} skipped, {c['blank']} blank, {c['outside']} outside the table.")
                if found["not_ready"]:
                    st.dataframe(pd.DataFrame(found["not_ready"][:300], columns=["Excel row", "Why it is not ready"]),
                                 hide_index=True)

    with st.expander("Preview the first 20 rows"):
        preview = pd.DataFrame([{
            "Row": r["row"], "Date": r["date"], "Description": r["description"],
            "Dir": money.direction_label(r["direction"], r["category_type"]), "Total": r["total_cents"] / 100,
            "Subcategory": r["subcategory"], "Type": r["category_type"], "Mode": r["mode"],
            "Shares": ", ".join(f"{name} {money.fmt_money(cents)}" for name, cents in r["shares"].items()),
        } for r in rows[:20]])
        st.dataframe(ui.money_styler(preview, ["Total"]) if len(preview) else preview, hide_index=True)

    skip = st.checkbox("Skip rows that are already in the app", value=True, key="imp_skip")
    sure = st.checkbox("The totals look right. Import these rows (a backup is made first).", key=f"imp_sure_{tag}")
    if st.button("Import", type="primary", disabled=not sure or not rows or bool(summary["bad_types"]), key="imp_go"):
        backup.backup_now()
        try:
            with db.session() as conn:
                result = importer.apply(conn, rows, subcategory_map=subcategory_map,
                                        default_mode_id=default_mode_id, skip_duplicates=skip)
        except Exception as problem:                       # nothing was written: the transaction rolled back
            st.error(f"Import failed and nothing was changed: {problem}")
        else:
            ui.invalidate()
            ui.flash(f"Imported {result['imported']:,} rows ({result['skipped_duplicates']:,} duplicates skipped).")
            st.rerun()

# ------------------------------------------------------------------------- undo

st.divider()
with db.session() as conn:
    last = importer.last_import_count(conn)
st.caption(f"The last import added {last:,} transaction(s)." if last else "Nothing to undo.")
if st.button("Undo last import", disabled=not last, key="imp_undo", icon=":material/undo:"):
    with db.session() as conn:
        removed = importer.undo_last(conn)
    ui.invalidate()
    ui.flash(f"Removed {removed:,} imported transactions.")
    st.rerun()
