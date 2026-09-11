"""
VB Updater: cross-matches Virtual Bit descriptions between the RDB's GLE
(the output port comment of the VBxxx SYMBOL) and the SCD (`desc` attribute
of the <ExtRef intAddr="VBxxx"> under the matching IED).

Flow:
  1. The user uploads an RDB and an SCD.
  2. The app cross-matches RDB <-> SCD using `sellib.match`.
  3. For each matched pair it shows a GLE selector + a "Verify GLE comments"
     button.
  4. Clicking the button opens a dedicated page with the comparison table
     (VBxxx | GLE comment | SCD desc); empty cells show
     "<Without description>".

    model.py    a GLE and an SCD in, VB facts out -- and the new text back in,
                in bytes
    export.py   the two RDB writes, the two SCD writes and the xlsx both ways
    state.py    the RDB and SCD this visitor has open, and the /state payload
    render.py   the comparison page, the one page built as a string in Python
    handler.py  the routes
    templates/  landing.html and compare.html
"""

from __future__ import annotations

from pacct.paths import VB_UPDATER_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (VB_UPDATER_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# Re-exports, and they have to come last: `handler` and `render` import
# `load_template` back out of this module, so the name must already be bound
# when the lines below run. The same shape the other four split tools have.
#
# This tool's frozen surface is four times `gle_exporter`'s -- sixteen names,
# across four test files, all reached through the PACKAGE rather than through a
# module inside it. `tests/test_web_routes_vb_updater.py` wants
# `build_vb_updater_handler`, `LANDING_HTML` and `COMPARE_HTML_TEMPLATE`;
# `tests/test_rdb_write.py` wants the two RDB writers;
# `tests/test_gle_bytes.py` wants the two byte-level writers; and
# `tests/test_vb_updater_message_quality.py` wants the other nine.
#
# Six of them are spelled with a leading underscore AND now cross a module
# boundary, which the two rules in play answer in opposite directions: the
# convention says a definition crossing a boundary loses its underscore, and
# this phase says a route test is never edited to accommodate a split. Both are
# kept. The definition is public where it lives, because that is what the
# modules either side of it read; the package re-binds it to the private
# spelling the suite froze. Renaming a name at the import site to say what it is
# to the importer is this module's own habit already -- it is what the two
# `rdb_write` and `xlsx_names` imports in `export.py` and `handler.py` do, in
# the other direction.
#
# `_EXTREF_FIELDS` and `_format_extref_signal` are the two that stay underscored
# outright: they cross nothing, both being read only by `model.py` itself and by
# the test, so the convention has nothing to say about them.
from pacct.web.vb_updater.export import (  # noqa: E402
    build_vb_descriptions_xlsx,
    update_rdb_with_scd_descs,
    update_rdb_with_scd_descs_batch,
)
from pacct.web.vb_updater.handler import (  # noqa: E402
    LANDING_HTML,
    build_vb_updater_handler,
)
from pacct.web.vb_updater.model import (  # noqa: E402
    _EXTREF_FIELDS,
    _format_extref_signal,
    extract_vb_extref_rows_from_scd_ied,
    extract_vb_map_from_scd_ied,
)
from pacct.web.vb_updater.model import (  # noqa: E402
    MESSAGE_QUALITY_LABEL as _MESSAGE_QUALITY_LABEL,
)
from pacct.web.vb_updater.model import (  # noqa: E402
    RESERVA_LABEL as _RESERVA_LABEL,
)
from pacct.web.vb_updater.model import (  # noqa: E402
    new_comments_from_scd as _new_comments_from_scd,
)
from pacct.web.vb_updater.model import (  # noqa: E402
    substitute_vb_comments_in_gle_bytes as _substitute_vb_comments_in_gle_bytes,
)
from pacct.web.vb_updater.model import (  # noqa: E402
    update_scd_extrefs_for_ied as _update_scd_extrefs_for_ied,
)
from pacct.web.vb_updater.render import COMPARE_HTML_TEMPLATE  # noqa: E402
from pacct.web.vb_updater.render import (  # noqa: E402
    render_compare_page as _render_compare_page,
)

__all__ = [
    "COMPARE_HTML_TEMPLATE",
    "LANDING_HTML",
    "_EXTREF_FIELDS",
    "_MESSAGE_QUALITY_LABEL",
    "_RESERVA_LABEL",
    "_format_extref_signal",
    "_new_comments_from_scd",
    "_render_compare_page",
    "_substitute_vb_comments_in_gle_bytes",
    "_update_scd_extrefs_for_ied",
    "build_vb_descriptions_xlsx",
    "build_vb_updater_handler",
    "extract_vb_extref_rows_from_scd_ied",
    "extract_vb_map_from_scd_ied",
    "load_template",
    "update_rdb_with_scd_descs",
    "update_rdb_with_scd_descs_batch",
]
