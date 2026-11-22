"""Built-in charts for the Insights page: one small file per chart (SPEC section 8).

To add an analysis, create a new file in this folder that defines:

    TITLE = "What the chart shows"
    ORDER = 60                       # position on the page (lower comes first)
    TAB = "Income & Expense"         # optional; use "Savings" for the Savings tab
    def build(df_tx, df_shares, filters): ...    # pure pandas, returns the data (easy to test)
    def render(df_tx, df_shares, filters): ...   # draws it with Streamlit

df_tx and df_shares are the full, unfiltered frames (see core/analytics.py);
`filters` carries the global period and view (analytics.Filters).
The Insights page finds the file by itself. Add a test next to the others in tests/test_analyses.py.
"""
from __future__ import annotations

import importlib
import pkgutil

DEFAULT_TAB = "Income & Expense"


def discover(tab: str = DEFAULT_TAB) -> list:
    """All analysis modules for one tab, sorted by ORDER."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        if all(hasattr(module, name) for name in ("TITLE", "ORDER", "render")) \
                and getattr(module, "TAB", DEFAULT_TAB) == tab:
            found.append(module)
    return sorted(found, key=lambda module: module.ORDER)
