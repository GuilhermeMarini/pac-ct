"""Organizador de Abas GLE: reorder and rename a GLE's pages.

Each `<page>` under `<pages>` in `Relays/<relay>/Misc/GL*.gle` is one tab in
AcSELerator QuickSet's Graphical Logic Editor. QuickSet offers no way to move
one, so the tabs of a real project sit in whatever order they were drawn in.

Reordering cannot change what the relay does: the compiled SELOGIC lives in
numbered slots (`SET02`, `LT03`) bound to each element's
`physical_instance_number`, never to a page's position, and a page name appears
in no other stream of the RDB.

Task 1 builds only `model.py` (byte spans); nothing else exists here yet. The
package's `load_template()` and `GLE_TABS_TEMPLATES_DIR` (`paths.py`) are
Task 5's, added together with the handler that calls them -- adding the
import here first would make this package fail to import on its own.
"""

from __future__ import annotations
