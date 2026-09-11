"""The comparison page: one row per VB, what the GLE says beside what the SCD says.

The only screen in this application built as a string in Python rather than
served as a template and filled over `/state`. It stays that way because it is
the one page opened with its whole subject in the query string -- relay, IED and
GLE -- and rendered once: `compare.js` filters and sorts the table that is
already there and posts the two apply buttons, and never fetches a row.

Its own module, and not `handler.py`, because it is neither a route nor an
output. It takes two `Path`s and returns a string; it touches no request, no
session and no session directory, and nothing it produces is ever published into
the project library. The suite already reads it that way --
`tests/test_vb_updater_message_quality.py` imports it and asserts on what it
returns, with no HTTP anywhere near it.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from pacct.web.vb_updater import load_template
from pacct.web.vb_updater.model import (
    MESSAGE_QUALITY_LABEL,
    extract_vb_instances_from_gle,
    extract_vb_map_from_scd_ied,
)

_EMPTY_LABEL = "&lt;Without description&gt;"

COMPARE_HTML_TEMPLATE = load_template("compare.html")


def render_compare_page(rdb_relay: str, ied_name: str, gle_name: str,
                         gle_path: Path, scd_path: Path) -> str:
    """Build the full HTML of the comparison page.

    One row per VB instance in the GLE. If a VB appears N times in the GLE,
    N rows are produced (all compared against the same SCD desc). If it does
    not appear in the GLE but exists in the SCD, it becomes 1 row with an
    empty GLE.
    """
    gle_map = extract_vb_instances_from_gle(gle_path)
    vb_map = extract_vb_map_from_scd_ied(scd_path, ied_name)
    scd_map = vb_map.descs

    all_vbs = sorted(
        set(gle_map.keys()) | set(scd_map.keys()),
        key=lambda k: int(k[2:]),
    )

    rows = []
    equal_count = 0
    diff_count = 0

    def _row(vb: str, gle_comment: str, scd_desc: str, loc: str) -> str:
        is_diff = (gle_comment != scd_desc)
        nonlocal equal_count, diff_count
        if is_diff:
            diff_count += 1
        else:
            equal_count += 1
        g_html = escape(gle_comment) if gle_comment else _EMPTY_LABEL
        s_html = escape(scd_desc) if scd_desc else _EMPTY_LABEL
        g_cls = "cell" + ("" if gle_comment else " empty")
        s_cls = "cell" + ("" if scd_desc else " empty")
        loc_html = f'<div class="loc">{escape(loc)}</div>' if loc else ""
        # The tag, not the description, is what says this VB carries the
        # subscription's health: the description belongs to the engineer and
        # both columns keep showing exactly what each file holds.
        tag_html = (
            f'<div class="tag">{escape(MESSAGE_QUALITY_LABEL)}</div>'
            if vb in vb_map.quality else ""
        )
        cls = ' class="diff"' if is_diff else ""
        return (
            f'<tr{cls}>'
            f'<td class="vb">{escape(vb)}{tag_html}{loc_html}</td>'
            f'<td class="{g_cls}">{g_html}</td>'
            f'<td class="{s_cls}">{s_html}</td>'
            f'</tr>'
        )

    for vb in all_vbs:
        instances = gle_map.get(vb, [])
        scd_desc = scd_map.get(vb, "")
        if not instances:
            # The VB exists in the SCD but not in the GLE.
            rows.append(_row(vb, "", scd_desc, "(ausente no GLE)"))
            continue
        for inst in instances:
            parts = [p for p in (inst.page, f"#{inst.element_id}" if inst.element_id else "") if p]
            loc = " / ".join(parts)
            rows.append(_row(vb, inst.comment, scd_desc, loc))

    total_rows = equal_count + diff_count
    quality_shown = sum(1 for vb in all_vbs if vb in vb_map.quality)
    quality_html = (
        f' &middot; {quality_shown} {escape(MESSAGE_QUALITY_LABEL)}'
        if quality_shown else ""
    )
    summary = (
        f'<span class="ok">{equal_count} iguais</span> &nbsp; '
        f'<span class="warn">{diff_count} divergentes</span> &nbsp; '
        f'<span class="muted">{total_rows} instancia(s) &middot; '
        f'{len(all_vbs)} VB(s) unico(s){quality_html}</span>'
    )

    body_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="3" class="muted" style="text-align:center;padding:24px">'
        'Nenhum VB encontrado nos dois lados.</td></tr>'
    )

    return COMPARE_HTML_TEMPLATE.format(
        rdb_relay=escape(rdb_relay),
        ied_name=escape(ied_name),
        gle_name=escape(gle_name),
        summary=summary,
        rows=body_rows,
    )
