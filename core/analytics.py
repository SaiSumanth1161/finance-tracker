"""Analytics: load the data frames, pick periods and views, aggregate() and the Insights tables.

Everything here is plain pandas on two frames (no Streamlit), so it can be tested:
- df_tx     one row per transaction            (from the SQL view v_transactions)
- df_shares one row per transaction x person   (from the SQL view v_shares)

Almost every function takes df_shares, because per-person numbers need it:
- View "Me"        = my shares only (my real spending)
- View "<name>"    = that roommate's shares
- View "Household" = everybody's shares, i.e. the full totals

Amounts stay integer cents. Signed amounts follow the direction: income +, expense −.
Savings and Not Reported rows never count as income or expense (SPEC section 5).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from core import money

INCOME, EXPENSE, SAVINGS, NOT_REPORTED = "Income", "Expense", "Savings", "Not Reported"
ME_VIEW, HOUSEHOLD_VIEW = "Me", "Household"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_INT_COLUMNS = ["total_cents", "signed_total_cents", "my_share_cents", "my_share_signed_cents",
                "share_cents", "share_signed_cents", "sign", "year", "is_me", "person_sort"]


# ---------------------------------------------------------------------- loading

def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Give a frame real dates plus the helper columns month_num (1-12) and day (YYYY-MM-DD)."""
    df["date"] = pd.to_datetime(df["date"])
    df["month_num"] = df["date"].dt.month.astype("int64")
    df["day"] = df["date"].dt.strftime("%Y-%m-%d")
    for column in _INT_COLUMNS:
        if column in df:
            df[column] = df[column].astype("int64")
    return df


def load_frames(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read both views. Returns (df_tx, df_shares)."""
    tx = pd.read_sql_query("SELECT * FROM v_transactions ORDER BY date DESC, created_at DESC", conn)
    shares = pd.read_sql_query("SELECT * FROM v_shares ORDER BY date, transaction_id, person_sort", conn)
    return _prepare(tx), _prepare(shares)


def wide_transactions(tx: pd.DataFrame, shares: pd.DataFrame, people: list[str]) -> pd.DataFrame:
    """df_tx plus one column per person name holding their share in cents (0 if they have none)."""
    out = tx.copy()
    if shares.empty:
        for name in people:
            out[name] = 0
        return out
    pivot = shares.pivot_table(index="transaction_id", columns="person", values="share_cents",
                               aggfunc="sum", fill_value=0)
    for name in people:
        out[name] = out["id"].map(pivot[name]).fillna(0).astype("int64") if name in pivot else 0
    return out


# ---------------------------------------------------------------------- periods

@dataclass(frozen=True)
class Period:
    """Which dates count.

    Either Year/Month "slicers" (an empty tuple means "all"), or a custom date
    range (start and end, both included). When a range is set it wins.
    """
    years: tuple = ()
    months: tuple = ()
    start: date | None = None
    end: date | None = None

    @property
    def is_range(self) -> bool:
        return self.start is not None and self.end is not None

    def mask(self, df: pd.DataFrame) -> pd.Series:
        """True for the rows of df that fall inside this period."""
        if self.is_range:
            return (df["date"] >= pd.Timestamp(self.start)) & (df["date"] <= pd.Timestamp(self.end))
        keep = pd.Series(True, index=df.index)
        if self.years:
            keep &= df["year"].isin(list(self.years))
        if self.months:
            keep &= df["month_num"].isin(list(self.months))
        return keep

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        return df[self.mask(df)]

    def label(self) -> str:
        """Short text for page headers, e.g. "2026 · Jan, Mar"."""
        if self.is_range:
            return f"{self.start:%b %d, %Y} – {self.end:%b %d, %Y}"
        if not self.years and not self.months:
            return "All time"
        years = ", ".join(str(y) for y in sorted(self.years)) or "All years"
        months = ", ".join(MONTH_NAMES[m - 1] for m in sorted(self.months))
        return f"{years} · {months}" if months else years

    def month_count(self, df: pd.DataFrame | None = None, today: date | None = None) -> int:
        """How many calendar months the period covers, up to the current month
        (used to scale monthly budgets, so months that have not started do not add budget).

        With no year picked ("all years") it counts the months that have data in df.
        """
        today = today or date.today()
        now = today.year * 12 + today.month - 1                       # months counted from year 0
        if self.is_range:
            last = min(self.end.year * 12 + self.end.month - 1, now)
            return max(0, last - (self.start.year * 12 + self.start.month - 1) + 1)
        if self.years:
            return sum(y * 12 + m - 1 <= now for y in self.years for m in (self.months or range(1, 13)))
        if df is None or df.empty:
            return 0
        return int(self.apply(df)["month"].nunique())

    def previous(self) -> "Period | None":
        """The equal-length period just before this one (None for "all time").

        - date range: the same number of days right before it
        - an unbroken run of months (e.g. Feb-Apr 2026): the same number of months before it
        - anything else (e.g. Jan + Mar): the same months one year earlier
        """
        if self.is_range:
            days = (self.end - self.start).days + 1
            prev_end = self.start - timedelta(days=1)
            return Period(start=prev_end - timedelta(days=days - 1), end=prev_end)
        if not self.years:
            return None
        months = self.months or tuple(range(1, 13))
        index = sorted(y * 12 + (m - 1) for y in self.years for m in months)  # months counted from year 0
        if index[-1] - index[0] + 1 == len(index):
            first, last = index[0] - len(index), index[0] - 1
            last_year, last_month = last // 12, last % 12 + 1
            return Period(start=date(first // 12, first % 12 + 1, 1),
                          end=date(last_year, last_month, calendar.monthrange(last_year, last_month)[1]))
        return Period(years=tuple(y - 1 for y in self.years), months=self.months)


PERIOD_PRESETS = ["This month", "Last month", "Last 3 months", "Last 6 months", "Last 12 months", "YTD", "All time"]


def named_period(name: str, today: date | None = None) -> Period:
    """Turn a preset name into a Period, relative to today."""
    today = today or date.today()
    year, month = today.year, today.month
    if name == "This month":
        return Period(years=(year,), months=(month,))
    if name == "Last month":
        return Period(years=(year if month > 1 else year - 1,), months=(month - 1 if month > 1 else 12,))
    if name in ("Last 3 months", "Last 6 months", "Last 12 months"):
        count = int(name.split()[1])
        first = year * 12 + (month - 1) - (count - 1)
        return Period(start=date(first // 12, first % 12 + 1, 1),
                      end=date(year, month, calendar.monthrange(year, month)[1]))
    if name == "YTD":
        return Period(years=(year,), months=tuple(range(1, month + 1)))
    if name == "All time":
        return Period()
    raise ValueError(f"Unknown period '{name}'.")


@dataclass(frozen=True)
class Filters:
    """The global sidebar filters, passed to every analysis."""
    period: Period = Period()
    view: str = ME_VIEW


# ------------------------------------------------------------- views and filters

def view_rows(df: pd.DataFrame, view: str) -> pd.DataFrame:
    """Keep the share rows that belong to a view (Me, a roommate's name, or Household)."""
    if view == HOUSEHOLD_VIEW:
        return df
    if view == ME_VIEW:
        return df[df["is_me"] == 1]
    return df[df["person"] == view]


_FILTER_COLUMNS = {"categories": "category", "subcategories": "subcategory", "category_types": "category_type",
                   "payment_modes": "payment_mode", "paid_by": "paid_by", "people": "person"}


def apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    """Extra filters used by Explore. Keys: categories, subcategories, category_types,
    payment_modes, paid_by, people (lists; empty = any) and description_contains (text)."""
    filters = filters or {}
    for key, column in _FILTER_COLUMNS.items():
        values = filters.get(key)
        if values:
            df = df[df[column].isin(list(values))]
    text = (filters.get("description_contains") or "").strip()
    if text:
        df = df[df["description"].fillna("").str.contains(text, case=False, regex=False)]
    return df


# -------------------------------------------------------------------- aggregate

MEASURES = ["Expense", "Income", "My share", "Split", "Net", "Saved", "Count", "Avg"]
# Group-by label -> column in df_shares
GROUPS = {
    "Day": "day", "Week": "week", "Month": "month", "Year": "year",
    "Category type": "category_type", "Category": "category", "Subcategory": "subcategory",
    "Person": "person", "Payment mode": "payment_mode", "Description": "description", "Paid by": "paid_by",
}
TIME_GROUPS = {"Day", "Week", "Month", "Year"}
# Which category types each measure looks at.
_MEASURE_TYPES = {
    "Income": [INCOME], "Expense": [EXPENSE], "My share": [EXPENSE], "Split": [EXPENSE],
    "Net": [INCOME, EXPENSE], "Saved": [SAVINGS], "Count": [INCOME, EXPENSE, SAVINGS], "Avg": [EXPENSE],
}


def _measure_rows(df: pd.DataFrame, measure: str, view: str, by_person: bool) -> pd.DataFrame:
    """The share rows that count for a measure, with a signed 'value' column (cents)."""
    d = df[df["category_type"].isin(_MEASURE_TYPES[measure])]
    if measure == "My share":          # always my shares, whatever the view
        d = d[d["is_me"] == 1]
    elif measure == "Split":           # always the others' shares
        d = d[d["is_me"] != 1]
    elif not by_person:                # grouping by Person shows everyone, so the view is not applied
        d = view_rows(d, view)
    flip = -1 if measure in ("Split", "Saved") else 1   # Split is shown positive; saved = −signed
    return d.assign(value=d["share_signed_cents"] * flip)


def _round_div(total: int, count: int) -> int:
    """Average in whole cents (half rounds away from zero)."""
    if not count:
        return 0
    return int((Decimal(int(total)) / Decimal(int(count))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def aggregate(df: pd.DataFrame, measure: str, group_by=(), filters: dict | None = None,
              view: str = ME_VIEW, period: Period | None = None) -> pd.DataFrame:
    """The one generic function behind Explore and every chart.

    df        df_shares (long format)
    measure   one of MEASURES. Income, Expense, My share, Net are signed cents (expense is negative),
              Split and Saved are positive when money was split / saved, Count is a number of
              transactions and Avg is the average expense per transaction.
    group_by  0, 1 or 2 labels from GROUPS, e.g. ["Month", "Category"]
    filters   see apply_filters()
    view      "Me", a roommate's name or "Household"
    period    a Period (None = all time)

    Returns a frame with one column per group label plus 'value' (integers).
    Time groups are sorted by time; other groups by size, biggest first.
    """
    if measure not in MEASURES:
        raise ValueError(f"Unknown measure '{measure}'.")
    labels = list(group_by)
    columns = [GROUPS[label] for label in labels]
    d = df if period is None else period.apply(df)
    d = apply_filters(d, filters)
    d = _measure_rows(d, measure, view, by_person="person" in columns)

    if not columns:
        if measure == "Count":
            value = d["transaction_id"].nunique()
        elif measure == "Avg":
            value = _round_div(d["value"].sum(), d["transaction_id"].nunique())
        else:
            value = d["value"].sum()
        return pd.DataFrame({"value": [int(value)]})

    grouped = d.groupby(columns, sort=False)
    if measure == "Count":
        out = grouped["transaction_id"].nunique()
    elif measure == "Avg":
        parts = grouped.agg(total=("value", "sum"), count=("transaction_id", "nunique"))
        out = pd.Series([_round_div(t, n) for t, n in zip(parts["total"], parts["count"])], index=parts.index)
    else:
        out = grouped["value"].sum()
    out = out.rename("value").reset_index()
    out.columns = labels + ["value"]
    out["value"] = out["value"].astype("int64")
    if labels[0] in TIME_GROUPS:
        out = out.sort_values(labels)
    else:
        out = (out.assign(_size=out["value"].abs())
                  .sort_values(["_size"] + labels, ascending=[False] + [True] * len(labels))
                  .drop(columns="_size"))
    return out.reset_index(drop=True)


def resolve_view(config: dict, global_filters: Filters, today: date | None = None) -> tuple[str, Period]:
    """A saved view follows the global View / period unless it saved a fixed one."""
    view = config.get("view") or "Global"
    period = config.get("period") or "Global"
    return (global_filters.view if view == "Global" else view,
            global_filters.period if period == "Global" else named_period(period, today))


def run_view(config: dict, df: pd.DataFrame, global_filters: Filters, today: date | None = None) -> pd.DataFrame:
    """Run a saved Explore view (its config is the JSON stored in saved_views)."""
    view, period = resolve_view(config, global_filters, today)
    return aggregate(df, config["measure"], config.get("group_by") or [], config.get("filters"), view, period)


# ------------------------------------------------------------------------- KPIs

def kpis(df: pd.DataFrame, view: str = ME_VIEW, period: Period | None = None) -> dict:
    """The dashboard numbers for one view and period (all integer cents, savings_rate is a ratio)."""
    d = df if period is None else period.apply(df)
    v = view_rows(d, view)

    def total(frame, kind) -> int:
        return int(frame.loc[frame["category_type"] == kind, "share_signed_cents"].sum())

    income, expense = total(v, INCOME), total(v, EXPENSE)
    household_expense = total(d, EXPENSE)
    my_share = total(d[d["is_me"] == 1], EXPENSE)
    flows = -v.loc[v["category_type"] == SAVINGS, "share_signed_cents"]   # + deposit, − withdrawal
    saved, withdrawn = int(flows[flows > 0].sum()), int(-flows[flows < 0].sum())
    net, net_saved = money.net_cents(income, expense), saved - withdrawn
    return {
        "income": income, "expense": expense, "net": net,
        "my_share": my_share, "split": money.split_kpi_cents(household_expense, my_share),
        "savings_rate": money.savings_rate(net, income),
        "saved": saved, "withdrawn": withdrawn, "net_saved": net_saved,
        "unallocated": money.unallocated_cents(net, net_saved),
    }


# -------------------------------------------------------------- Insights tables

def person_table(df: pd.DataFrame, index: list[str], people: list[str], view: str = ME_VIEW,
                 category_type: str = EXPENSE) -> pd.DataFrame:
    """Signed totals per `index` (e.g. ["category", "subcategory"]) for one category type.

    Columns: the index columns, amount (the current view), my_share, one column per name
    in `people`, and household (everyone).
    """
    d = df[df["category_type"] == category_type]
    columns = index + ["amount", "my_share"] + people + ["household"]
    if d.empty:
        return pd.DataFrame(columns=columns)

    def sums(frame) -> pd.Series:
        return frame.groupby(index, sort=False)["share_signed_cents"].sum()

    out = sums(d).rename("household").to_frame()
    out["amount"] = sums(view_rows(d, view))
    out["my_share"] = sums(d[d["is_me"] == 1])
    for name in people:
        out[name] = sums(d[d["person"] == name])
    out = out.fillna(0).astype("int64").reset_index()
    return out[columns]


def monthly_table(df: pd.DataFrame, view: str = ME_VIEW) -> pd.DataFrame:
    """One row per year and month: income, expense, my_share, net (for the view) and saved."""
    columns = ["year", "month_num", "month_name", "income", "expense", "my_share", "net", "saved"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    keys = ["year", "month_num"]
    v = view_rows(df, view)

    def by_month(frame, kind, flip=1) -> pd.Series:
        return frame[frame["category_type"] == kind].groupby(keys)["share_signed_cents"].sum() * flip

    out = pd.DataFrame({
        "income": by_month(v, INCOME), "expense": by_month(v, EXPENSE),
        "my_share": by_month(df[df["is_me"] == 1], EXPENSE), "saved": by_month(v, SAVINGS, -1),
    }).fillna(0).astype("int64")
    if out.empty:
        return pd.DataFrame(columns=columns)
    out["net"] = out["income"] + out["expense"]
    out = out.reset_index().sort_values(keys)
    out["month_name"] = [MONTH_NAMES[m - 1] for m in out["month_num"]]
    return out[columns].reset_index(drop=True)


def savings_table(df: pd.DataFrame, index: list[str], view: str = ME_VIEW) -> pd.DataFrame:
    """Savings per `index`: saved (deposits), withdrawn and net_saved, all positive-is-good cents."""
    columns = index + ["saved", "withdrawn", "net_saved"]
    v = view_rows(df, view)
    v = v[v["category_type"] == SAVINGS]
    if v.empty:
        return pd.DataFrame(columns=columns)
    flow = -v["share_signed_cents"]
    v = v.assign(saved=flow.clip(lower=0), withdrawn=(-flow).clip(lower=0))
    out = v.groupby(index, sort=False)[["saved", "withdrawn"]].sum().astype("int64")
    out["net_saved"] = out["saved"] - out["withdrawn"]
    return out.reset_index()[columns]


def hierarchy(table: pd.DataFrame, parent: str, child: str | None = None, value_cols=(),
              pct_of: str | None = None, sort_by: str | None = None) -> pd.DataFrame:
    """Turn a tidy table into pivot-style rows: parent subtotals, their children, then a Grand Total.

    Output columns: level ("parent" | "child" | "total"), label, group (the parent's label),
    the value columns, and pct (share of the grand total of `pct_of`, in percent) when asked.
    sort_by orders parents and children by the size of that column (biggest first);
    without it the order of `table` is kept (use that for Year -> Month).
    """
    value_cols = list(value_cols)

    def ordered(frame, key) -> pd.DataFrame:
        sums = frame.groupby(key, sort=False)[value_cols].sum()
        if sort_by:
            sums = sums.loc[sums[sort_by].abs().sort_values(ascending=False, kind="stable").index]
        return sums

    rows = []
    for name, sums in ordered(table, parent).iterrows():
        rows.append({"level": "parent", "label": str(name), "group": str(name),
                     **{c: int(sums[c]) for c in value_cols}})
        if child:
            for kid, kid_sums in ordered(table[table[parent] == name], child).iterrows():
                rows.append({"level": "child", "label": str(kid), "group": str(name),
                             **{c: int(kid_sums[c]) for c in value_cols}})
    grand = {c: int(table[c].sum()) if len(table) else 0 for c in value_cols}
    rows.append({"level": "total", "label": "Grand Total", "group": "", **grand})
    out = pd.DataFrame(rows, columns=["level", "label", "group"] + value_cols)
    if pct_of:
        out["pct"] = [round(100 * value / grand[pct_of], 1) if grand[pct_of] else 0.0 for value in out[pct_of]]
    return out


def budget_table(df: pd.DataFrame, budgets: dict[str, int], months: int, view: str = ME_VIEW) -> pd.DataFrame:
    """Budget against actual spending per expense category (SPEC phase 5).

    df       share rows already limited to the period
    budgets  {category name: monthly budget in cents}
    months   how many months the period covers; the budget is multiplied by it
    Returns category, budget, spent (positive cents), remaining and over (True when spent > budget),
    for every category that has a budget, biggest overspend first.
    """
    columns = ["category", "budget", "spent", "remaining", "over"]
    if not budgets:
        return pd.DataFrame(columns=columns)
    v = view_rows(df, view)
    spent = -v[v["category_type"] == EXPENSE].groupby("category")["share_signed_cents"].sum()
    rows = []
    for category, monthly in budgets.items():
        budget = int(monthly) * months
        used = int(spent.get(category, 0))
        rows.append({"category": category, "budget": budget, "spent": used,
                     "remaining": budget - used, "over": used > budget})
    return pd.DataFrame(rows, columns=columns).sort_values("remaining", kind="stable").reset_index(drop=True)
