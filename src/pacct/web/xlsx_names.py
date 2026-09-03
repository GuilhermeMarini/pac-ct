"""Excel worksheet naming, shared by the two tools that write spreadsheets.

Excel refuses a sheet name longer than 31 characters or containing any of
``: \\ / ? * [ ]``, and refuses two sheets with the same name in one workbook.
Both the VB Updater and the GLE Exporter name a sheet after a relay or a GLE,
so both hit all three rules on real substation data -- relay names carry
slashes often enough.
"""

from __future__ import annotations

import re

# Excel sheet: at most 31 chars, none of `:\/?*[]`.
BAD_SHEET_CHARS_RE = re.compile(r"[:\\/?*\[\]]")
SHEET_NAME_MAX = 31


def sanitize_sheet_name(name: str, used: set[str]) -> str:
    """Truncate to 31 chars, replace invalid chars, disambiguate with _2/_3.

    ``used`` is both read and WRITTEN: the returned name is added to it, so
    callers pass one set through a whole workbook and never collide.
    """
    base = BAD_SHEET_CHARS_RE.sub("_", name)[:SHEET_NAME_MAX] or "Sheet"
    candidate = base
    n = 2
    while candidate in used:
        suffix = f"_{n}"
        candidate = base[: SHEET_NAME_MAX - len(suffix)] + suffix
        n += 1
    used.add(candidate)
    return candidate
