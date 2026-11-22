"""Shared Streamlit pieces used by every page.

- page():            page config, database start-up, sidebar (navigation + global filters), top bar
- data():            all cached reads; invalidate() clears the cache after every write
- kpi_card():        one number with a "vs previous period" line
- breakdown_table(): the pivot-style Category -> Subcategory table with data bars
- chart helpers:     one colour palette and one look for every Plotly chart

Refresh rule (SPEC section 6): every database read goes through data(), which is
cached. Every write must call invalidate() so the next rerun shows fresh numbers.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import analytics, backup, db, export, money
from core.analytics import HOUSEHOLD_VIEW, ME_VIEW, MONTH_NAMES, Filters, Period

PAGES = [
    ("app.py", "Transactions", ":material/receipt_long:"),
    ("pages/1_Insights.py", "Insights", ":material/insights:"),
    ("pages/2_Explore.py", "Explore", ":material/explore:"),
    ("pages/3_Settle_Up.py", "Settle up", ":material/handshake:"),
    ("pages/4_Setup.py", "Setup", ":material/settings:"),
    ("pages/5_Backup.py", "Backup", ":material/backup:"),
    ("pages/6_Import.py", "Import", ":material/upload_file:"),
]
SIDEBAR_PRESETS = ["This month", "Last month", "Last 3 months", "YTD", "Custom"]


# ------------------------------------------------------------------ cached data

@st.cache_resource(show_spinner=False)
def _boot(path: str) -> bool:
    """Runs once per app start: create/upgrade the database, then the automatic daily backup."""
    db.init_db(path)
    try:
        backup.auto_backup_if_due(path)
    except Exception:  # a failed backup must never stop the app from opening
        pass
    db.secure_existing_files(path, backup.backup_dir())   # owner-only, also for files made by older versions
    return True


@st.cache_data(show_spinner=False)
def _load(path: str) -> dict:
    """Read everything the pages need in one go (cached until invalidate())."""
    with db.session(path) as conn:
        tx, shares = analytics.load_frames(conn)
        return {
            "tx": tx, "shares": shares,
            "people": db.list_people(conn), "modes": db.list_payment_modes(conn),
            "types": db.list_category_types(conn), "categories": db.list_categories(conn),
            "subcategories": db.list_subcategories(conn), "settlements": db.list_settlements(conn),
            "views": db.list_saved_views(conn), "balance_inputs": db.balance_inputs(conn),
            "budgets": db.list_budgets(conn), "rules": db.list_rules(conn), "templates": db.list_templates(conn),
            "loaded_at": datetime.now(),
        }


def data() -> dict:
    """The cached data: frames 'tx' and 'shares', and the master lists as lists of dicts."""
    return _load(str(db.db_path()))


def invalidate() -> None:
    """Call after every write so all pages reload from the database."""
    st.cache_data.clear()


def flash(message: str, kind: str = "success") -> None:
    """Remember a message to show after the next rerun."""
    st.session_state["_flash"] = (kind, message)


def _show_flash() -> None:
    kind, message = st.session_state.pop("_flash", (None, None))
    if kind == "error":
        st.error(plain(message))
    elif message:
        st.toast(plain(message), icon=":material/check_circle:")


def me_name(d: dict) -> str:
    return next(p["name"] for p in d["people"] if p["is_me"])


def person_columns(d: dict) -> list[str]:
    """Names shown as per-person columns: everyone who is active or already has a share."""
    with_shares = set(d["shares"]["person"]) if len(d["shares"]) else set()
    return [p["name"] for p in d["people"] if p["active"] or p["name"] in with_shares]


def roommates(d: dict) -> list[str]:
    """person_columns() without me."""
    mine = me_name(d)
    return [name for name in person_columns(d) if name != mine]


# ------------------------------------------------------------------- page frame

def page(title: str) -> tuple[dict, Filters]:
    """Start a page. Must be the first Streamlit call. Returns (data, global filters)."""
    st.set_page_config(page_title=f"{title} · Finance Tracker", page_icon=":material/savings:", layout="wide")
    st.html("<style>[data-testid='stMainBlockContainer']{padding:2.2rem 2.2rem 3rem}</style>")  # more room for tables
    _boot(str(db.db_path()))
    d = data()
    filters = _sidebar(d)
    left, middle, right = st.columns([5, 4, 1.3], vertical_alignment="bottom")
    left.title(title)
    middle.caption(f"Data as of {d['loaded_at']:%H:%M} · {len(d['tx']):,} transactions")
    if right.button("Refresh", icon=":material/refresh:", key="refresh", width="stretch",
                    help="Reload everything from the database."):
        invalidate()
        st.rerun()
    _show_flash()
    return d, filters


# The sidebar filters live in st.session_state under "f_*" keys, so they survive page changes.
# Widgets use separate "w_*" keys; callbacks copy the widget value into the "f_*" key.

def _on_pills() -> None:
    ss = st.session_state
    ss["f_years"], ss["f_months"], ss["f_preset"] = sorted(ss["w_years"]), sorted(ss["w_months"]), None


def _on_preset() -> None:
    ss = st.session_state
    name = ss["w_preset"]
    ss["f_preset"] = name
    if name in (None, "Custom"):
        return
    period = analytics.named_period(name, date.today())
    if not period.is_range:
        ss["f_years"], ss["f_months"] = list(period.years), list(period.months)
    elif period.start.year == period.end.year:      # e.g. "Last 3 months" inside one year -> pills
        ss["f_years"] = [period.start.year]
        ss["f_months"] = list(range(period.start.month, period.end.month + 1))
    else:                                           # crosses New Year: pills cannot say that, use a range
        ss["f_preset"], ss["f_range"] = "Custom", (period.start, period.end)
        ss.pop("w_range", None)


def _on_range() -> None:
    ss = st.session_state
    if len(ss["w_range"]) == 2:                     # ignore the half-picked state (only a start date)
        ss["f_range"] = tuple(ss["w_range"])


def _on_view() -> None:
    st.session_state["f_view"] = st.session_state["w_view"]


def _clear_filters() -> None:
    ss = st.session_state
    ss["f_years"], ss["f_months"], ss["f_preset"], ss["f_view"] = [], [], None, ME_VIEW


def _sidebar(d: dict) -> Filters:
    """Navigation links plus the global filters. Returns the chosen Filters."""
    ss = st.session_state
    today = date.today()
    years = sorted(set(int(y) for y in d["tx"]["year"]) | {today.year})
    views = [ME_VIEW] + [p["name"] for p in d["people"] if p["active"] and not p["is_me"]] + [HOUSEHOLD_VIEW]

    ss.setdefault("f_years", [today.year])          # default: the current year
    ss.setdefault("f_months", [])                   # empty = all months
    ss.setdefault("f_preset", None)
    ss.setdefault("f_range", (today.replace(day=1), today))
    ss.setdefault("f_view", ME_VIEW)
    ss["f_years"] = [y for y in ss["f_years"] if y in years]
    if ss["f_view"] not in views:
        ss["f_view"] = ME_VIEW
    custom = ss["f_preset"] == "Custom"

    with st.sidebar:
        for path, label, icon in PAGES:
            if (db.ROOT / path).exists():               # a missing page file must not break the others
                st.page_link(path, label=label, icon=icon)
        st.divider()
        st.markdown("**Global filters**")
        # Copy the remembered values into the widgets, then draw them.
        ss["w_years"], ss["w_months"] = list(ss["f_years"]), list(ss["f_months"])
        ss["w_preset"], ss["w_view"] = ss["f_preset"], ss["f_view"]
        st.pills("Year", years, selection_mode="multi", key="w_years", on_change=_on_pills, disabled=custom,
                 help="Pick one or more years. None picked = all years.")
        st.pills("Month", list(range(1, 13)), selection_mode="multi", key="w_months", on_change=_on_pills,
                 format_func=lambda m: MONTH_NAMES[m - 1], disabled=custom,
                 help="Pick one or more months. None picked = all months.")
        st.pills("Quick presets", SIDEBAR_PRESETS, key="w_preset", on_change=_on_preset)
        if custom:
            if "w_range" not in ss:
                ss["w_range"] = ss["f_range"]
            st.date_input("Custom date range", key="w_range", on_change=_on_range, format="MM/DD/YYYY")
        st.selectbox("View", views, key="w_view", on_change=_on_view,
                     help="Me = my shares only. A roommate = their shares. Household = full totals.")
        st.button("Clear filters", on_click=_clear_filters, width="stretch", icon=":material/filter_alt_off:")

    if custom:
        period = Period(start=ss["f_range"][0], end=ss["f_range"][1])
    else:
        period = Period(years=tuple(ss["f_years"]), months=tuple(ss["f_months"]))
    with st.sidebar:
        st.caption(f"Showing: {period.label()} · {plain(ss['f_view'])}")
    return Filters(period=period, view=ss["f_view"])


# ------------------------------------------------------------------- formatting

def md(text: str) -> str:
    """Escape $ so st.markdown does not read two amounts as a LaTeX formula."""
    return text.replace("$", "\\$")


_MARKDOWN_SPECIAL = set("\\`*_{}[]()#+-.!|<>~$:")


def plain(text) -> str:
    """Text typed by a person (or read from a file), escaped so Markdown shows it exactly as written.

    Without this a name like "![x](https://…)" would load an image from the internet, and
    ":red[...]" or "**" would change the formatting.
    """
    return "".join("\\" + ch if ch in _MARKDOWN_SPECIAL else ch for ch in str(text if text is not None else ""))


def dollars(cents: pd.Series) -> pd.Series:
    """Cents -> dollars as floats. For DISPLAY ONLY (tables, charts, Excel), never for maths."""
    return cents.astype("int64") / 100


def fmt_dollars(value, plus: bool = False) -> str:
    """Format a display value (dollars) with the same look as money.fmt_money."""
    if value is None or pd.isna(value):
        return ""
    return money.fmt_money(int(round(float(value) * 100)), plus=plus)


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def negative_color() -> str:
    return "#e66767" if is_dark() else "#d03b3b"


def money_styler(frame: pd.DataFrame, money_cols: list[str], signed_cols: list[str] = (),
                 blank_zero_cols: list[str] = ()) -> "pd.io.formats.style.Styler":
    """Style a display frame (dollars): $ formatting, + on signed columns, negatives in red.

    blank_zero_cols: columns where 0 is shown as an empty cell (e.g. a person without a share).
    """
    def formatter(column):
        def show(value):
            if column in blank_zero_cols and not value:
                return ""
            return fmt_dollars(value, plus=column in signed_cols)
        return show

    red = f"color: {negative_color()}"
    return (frame.style.format({c: formatter(c) for c in money_cols}, na_rep="")
            .map(lambda v: red if isinstance(v, (int, float)) and v < 0 else "", subset=list(money_cols)))


# -------------------------------------------------------------------- KPI cards

def kpi_card(column, label: str, value, previous=None, *, good: str | None = "up", kind: str = "money",
             magnitude: bool = False, help: str | None = None) -> None:
    """One KPI in a bordered card.

    value / previous  cents (kind="money") or a ratio like 0.25 (kind="rate"); None shows a dash
    good              "up" = an increase is good (green), "down" = an increase is bad (red), None = grey
    magnitude         compare sizes (use for expenses, which are negative numbers)
    """
    with column.container(border=True):
        st.caption(label, help=help)
        if value is None:
            st.markdown("#### –")
            return
        text = money.fmt_money(value) if kind == "money" else f"{value * 100:.1f}%"
        st.markdown(f"#### :red[{md(text)}]" if value < 0 else f"#### {md(text)}")
        if previous is None:
            st.caption("nothing earlier to compare")
            return
        change = (abs(value) - abs(previous)) if magnitude else (value - previous)
        if change == 0:
            st.caption("same as prev period")
            return
        arrow = "▲" if change > 0 else "▼"
        amount = money.fmt_money(abs(change)) if kind == "money" else f"{abs(change) * 100:.1f} pts"
        color = "gray" if good is None else ("green" if (change > 0) == (good == "up") else "red")
        st.markdown(f":{color}[{arrow} {md(amount)}] :gray[vs prev]")


# ------------------------------------------------------------- breakdown table

@dataclass
class Col:
    """One value column of a breakdown table."""
    key: str                  # column name in the hierarchy frame
    label: str                # header text
    kind: str = "money"       # "money" (cents) or "pct"
    bar: str | None = None    # data bar: None, "red", "green" or "auto" (green if positive, red if negative)


_TABLE_CSS = """
<style>
.ft-bt{width:100%;border-collapse:collapse;font-size:.86rem;font-variant-numeric:tabular-nums}
.ft-bt th{text-align:right;font-weight:600;padding:6px 10px;border-bottom:1px solid rgba(128,128,128,.45);white-space:nowrap;opacity:.8}
.ft-bt td{text-align:right;padding:5px 10px;border-bottom:1px solid rgba(128,128,128,.16);white-space:nowrap}
.ft-bt th:first-child,.ft-bt td:first-child{text-align:left}
.ft-bt tr.parent td{font-weight:600}
.ft-bt tr.child td:first-child{padding-left:28px}
.ft-bt tr.total td{font-weight:700;border-top:2px solid rgba(128,128,128,.55);border-bottom:none}
.ft-bt td.neg{color:NEGATIVE}
.ft-bt td.zero{opacity:.4}
</style>
"""
_BAR_COLORS = {"red": "rgba(227,73,72,.28)", "green": "rgba(12,163,12,.26)"}


def _breakdown_html(tree: pd.DataFrame, cols: list[Col], first_label: str, bars: str = "all") -> str:
    """Build the HTML for a breakdown table. `tree` comes from analytics.hierarchy().

    bars="all":   every row gets a data bar, scaled against the biggest parent row
                  (use for Category -> Subcategory, so bars compare down the whole column)
    bars="child": only child rows get a bar, scaled against the biggest child
                  (use for Year -> Month, where the year row is just a subtotal)
    """
    if bars == "child" and not (tree["level"] == "child").any():
        bars = "all"                                     # collapsed: fall back to the parent rows
    scale_rows = tree[tree["level"] == ("child" if bars == "child" else "parent")]
    biggest = {c.key: (scale_rows[c.key].abs().max() if len(scale_rows) else 0) or 1 for c in cols}
    head = "".join(f"<th>{html.escape(c.label)}</th>" for c in cols)
    body = []
    for row in tree.to_dict("records"):
        cells = [f"<td>{html.escape(str(row['label']))}</td>"]
        for c in cols:
            value = row[c.key]
            if c.kind == "money" and not value and row["level"] != "total":
                cells.append('<td class="zero">–</td>')    # nothing here: a quiet dash instead of $0.00
                continue
            text = f"{value:.1f}%" if c.kind == "pct" else money.fmt_money(value)
            css = ' class="neg"' if c.kind == "money" and value < 0 else ""
            style = ""
            if c.bar and value and row["level"] != "total" and (bars == "all" or row["level"] == "child"):
                width = min(100, round(100 * abs(value) / biggest[c.key]))
                color = _BAR_COLORS[c.bar if c.bar != "auto" else ("red" if value < 0 else "green")]
                style = f' style="background:linear-gradient(90deg,{color} {width}%,transparent {width}%)"'
            cells.append(f"<td{css}{style}>{text}</td>")
        body.append(f'<tr class="{row["level"]}">{"".join(cells)}</tr>')
    css = _TABLE_CSS.replace("NEGATIVE", negative_color())
    return (f'{css}<div style="overflow-x:auto"><table class="ft-bt"><thead><tr><th>{html.escape(first_label)}</th>'
            f'{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def tree_for_download(tree: pd.DataFrame, cols: list[Col], first_label: str) -> tuple[pd.DataFrame, list[str]]:
    """A breakdown table as a plain frame for CSV / Excel. Returns (frame, names of the cents columns)."""
    out = pd.DataFrame({
        first_label: [("    " if level == "child" else "") + label for level, label in zip(tree["level"], tree["label"])],
        **{c.label: tree[c.key] for c in cols},
    })
    return out, [c.label for c in cols if c.kind == "money"]


def breakdown_table(title: str, tree: pd.DataFrame, cols: list[Col], key: str, first_label: str = "Category",
                    bars: str = "all") -> None:
    """Draw a pivot-style table: bold parent rows with subtotals, indented child rows,
    inline data bars, a Grand Total row, an "Expand all" toggle and a CSV download.
    See _breakdown_html() for the `bars` option."""
    has_children = (tree["level"] == "child").any()
    empty = len(tree) <= 1                               # only the Grand Total row
    frame, cents_cols = tree_for_download(tree, cols, first_label)
    for name in cents_cols:
        frame[name] = dollars(frame[name])
    # Title, toggle and download share one row and wrap by themselves when the card is narrow.
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.markdown(f"**{plain(title)}**")
        expanded = st.toggle("Expand all", value=True, key=f"{key}_expand") if has_children else True
        if not empty:
            st.download_button("CSV", export.csv_bytes(frame), file_name=f"{key}.csv",
                               mime="text/csv", key=f"{key}_csv", icon=":material/download:")
    if empty:
        st.caption("No data for these filters.")
        return
    shown = tree if expanded else tree[tree["level"] != "child"]
    st.html(_breakdown_html(shown, cols, first_label, bars))


def month_label(value) -> str:
    """ "2026-06" -> "Jun 2026" for chart axes (other text is returned unchanged)."""
    text = str(value)
    if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
        return f"{MONTH_NAMES[int(text[5:]) - 1]} {text[:4]}"
    return text


# ----------------------------------------------------------------------- charts
# One palette for every chart (validated for colour-blind safety on the light and dark page).
# Order matters: series take these colours in this order and never cycle past eight.

_SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
_SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"]
MAX_SERIES = 8
OTHER = "Other"


def palette() -> dict:
    dark = is_dark()
    return {
        "series": _SERIES_DARK if dark else _SERIES_LIGHT,
        "surface": "#0e1117" if dark else "#ffffff",
        "grid": "#2c2c2a" if dark else "#e1e0d9",
        "axis": "#383835" if dark else "#c3c2b7",
        "muted": "#898781",
        "ink": "#f0efec" if dark else "#0b0b0b",
        "other": "#6b6a66" if dark else "#b5b3ab",
    }


def series_colors(names: list[str], master: list[str] | None = None) -> dict[str, str]:
    """Colour per series name.

    When the full list of possible names (`master`) fits in the palette, a name always
    gets the same colour, whatever else is on the chart. "Other" is always grey.
    """
    colors = palette()
    order = master if master and len(master) <= MAX_SERIES else [n for n in (master or names) if n in names]
    order = order + [n for n in names if n not in order and n != OTHER]
    mapping = {name: colors["series"][i % MAX_SERIES] for i, name in enumerate(order)}
    mapping[OTHER] = colors["other"]
    return mapping


def fold_small(frame: pd.DataFrame, column: str, value: str = "value", keep: int = MAX_SERIES - 1) -> pd.DataFrame:
    """Keep the `keep` biggest values of `column` and fold the rest into "Other"."""
    sizes = frame.groupby(column, sort=False)[value].sum().abs().sort_values(ascending=False, kind="stable")
    if len(sizes) <= keep + 1:
        return frame
    small = set(sizes.index[keep:])
    out = frame.assign(**{column: [OTHER if v in small else v for v in frame[column]]})
    others = [c for c in out.columns if c != value]
    return out.groupby(others, sort=False, as_index=False)[value].sum()


def money_axis(biggest: float) -> dict:
    """Axis settings for dollars: in full below $10,000, compact ($10k) above."""
    return {"tickformat": "$,.0f" if abs(biggest) < 10000 else "$~s"}


def style_figure(fig: go.Figure, *, height: int = 340, legend: bool = True, unified_hover: bool = False) -> go.Figure:
    """Apply the shared look: quiet grid, thin marks, legend on top, small margins."""
    colors = palette()
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=30 if legend else 8, b=8), showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
        hovermode="x unified" if unified_hover else "closest",
        bargap=0.45, bargroupgap=0.12,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    axis = dict(gridcolor=colors["grid"], gridwidth=1, griddash="solid", zerolinecolor=colors["axis"],
                zerolinewidth=1, linecolor=colors["axis"], title_text="", automargin=True)
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def bar_trace(name: str, x, y, color: str, *, horizontal: bool = False, money_values: bool = True) -> go.Bar:
    """A bar series: rounded data end, and a thin surface-coloured gap around it instead of a border."""
    value = "%{x" if horizontal else "%{y"
    value += ":$,.2f}" if money_values else ":,}"
    label = "%{y}" if horizontal else "%{x}"
    return go.Bar(
        name=name, x=x, y=y, orientation="h" if horizontal else "v",
        marker=dict(color=color, cornerradius=4, line=dict(width=2, color=palette()["surface"])),
        hovertemplate=f"{label}<br>{html.escape(name)}: {value}<extra></extra>",
    )


def line_trace(name: str, x, y, color: str, *, money_values: bool = True, width: int = 2) -> go.Scatter:
    """A line series: 2px line with 8px markers ringed in the surface colour."""
    value = "%{y:$,.2f}" if money_values else "%{y:,}"
    return go.Scatter(
        name=name, x=x, y=y, mode="lines+markers", line=dict(color=color, width=width),
        marker=dict(size=8, color=color, line=dict(width=2, color=palette()["surface"])),
        hovertemplate=f"{html.escape(name)}: {value}<extra></extra>",
    )


def show(fig: go.Figure, key: str) -> None:
    """Draw a Plotly figure with the toolbar hidden (hover still works)."""
    st.plotly_chart(fig, key=key, config={"displayModeBar": False})


# ------------------------------------------------- charts for Explore / saved views

CHART_TYPES = ["Bar", "Stacked bar", "Line", "Pie", "Table"]
_SPEND_MEASURES = {"Expense", "My share", "Avg"}     # negative numbers that read better as "spend"
_MASTER_KEYS = {"Person": "people", "Payment mode": "modes", "Category type": "types",
                "Category": "categories", "Subcategory": "subcategories", "Paid by": "people"}
MAX_BARS = 15


def result_table(result: pd.DataFrame, measure: str) -> pd.DataFrame:
    """An aggregate() result as a display table (dollars, except for Count)."""
    out = result.rename(columns={"value": measure})
    if measure != "Count":
        out[measure] = dollars(out[measure])
    return out


def result_figure(result: pd.DataFrame, measure: str, group_by: list[str], chart: str, d: dict) -> go.Figure | None:
    """Turn an aggregate() result into a Plotly figure (None when there is nothing to draw).

    Spending measures (Expense, My share, Avg) are drawn as positive amounts, so bars go up.
    """
    if result.empty or not group_by or chart == "Table":
        return None
    is_money = measure != "Count"
    flip = -1 if measure in _SPEND_MEASURES else 1
    frame = result.assign(value=result["value"] * flip / (100 if is_money else 1))
    first = group_by[0]
    second = group_by[1] if len(group_by) > 1 else None
    timeline = first in analytics.TIME_GROUPS
    frame[first] = frame[first].astype(str)

    if chart == "Pie":                                   # part-to-whole only: one level, a few slices
        parts = frame.groupby(first, sort=False, as_index=False)["value"].sum()
        parts = fold_small(parts[parts["value"] > 0], first, keep=5)
        if parts.empty:
            return None
        colors = series_colors(list(parts[first]), _master(d, first))
        fig = go.Figure(go.Pie(
            labels=parts[first], values=parts["value"], hole=0.55, sort=False,
            marker=dict(colors=[colors[n] for n in parts[first]], line=dict(color=palette()["surface"], width=2)),
            textinfo="label+percent", hovertemplate="%{label}: " + ("%{value:$,.2f}" if is_money else "%{value:,}") + "<extra></extra>"))
        return style_figure(fig, legend=False)

    if not timeline:                                     # category axis: biggest first, capped
        order = frame.groupby(first, sort=False)["value"].sum().abs().sort_values(ascending=False, kind="stable").index
        frame = frame[frame[first].isin(list(order[:MAX_BARS]))]
        order = list(order[:MAX_BARS])
    else:
        order = sorted(frame[first].unique())
    if first == "Month":                                 # "2026-06" -> "Jun 2026", keeping the time order
        frame[first] = frame[first].map(month_label)
        order = [month_label(m) for m in order]
    horizontal = not timeline and chart != "Line"
    fig = go.Figure()
    make = line_trace if chart == "Line" else bar_trace

    if second is None:
        values = frame.set_index(first)["value"].reindex(order)
        x, y = (values.values, values.index) if horizontal else (values.index, values.values)
        kwargs = {"money_values": is_money, **({"horizontal": True} if horizontal else {})}
        fig.add_trace(make(measure, x, y, palette()["series"][0], **kwargs))
    else:
        frame[second] = frame[second].astype(str)
        frame = fold_small(frame, second)
        names = list(dict.fromkeys(frame[second]))
        colors = series_colors(names, _master(d, second))
        for name in sorted(names, key=lambda n: (n == OTHER, list(colors).index(n) if n in colors else 99)):
            values = frame[frame[second] == name].groupby(first, sort=False)["value"].sum().reindex(order).fillna(0)
            x, y = (values.values, values.index) if horizontal else (values.index, values.values)
            kwargs = {"money_values": is_money, **({"horizontal": True} if horizontal else {})}
            fig.add_trace(make(name, x, y, colors[name], **kwargs))
        fig.update_layout(barmode="relative" if chart == "Stacked bar" else "group")

    biggest = float(frame["value"].abs().max()) if len(frame) else 0
    style_figure(fig, legend=second is not None, unified_hover=timeline,
                 height=max(340, 30 * len(order) + 80) if horizontal else 340)
    value_axis = money_axis(biggest) if is_money else {"tickformat": ",d"}
    if horizontal:
        fig.update_xaxes(**value_axis)
        fig.update_yaxes(autorange="reversed", showgrid=False, type="category")
    else:
        fig.update_yaxes(**value_axis)
        fig.update_xaxes(showgrid=False, type="category")
    return fig


def _master(d: dict, group: str) -> list[str] | None:
    """All possible names for a group-by, in master-list order (None when there is no master list)."""
    key = _MASTER_KEYS.get(group)
    return [str(item["name"]) for item in d[key]] if key else None
