"""The one structural rule a split tool has to keep: `model.py` never reaches
back into the web layer.

A tool under `src/pacct/web/<tool>/` is routing plus domain logic, and the
split is only real if the domain half can be read, tested and moved without
the framework coming with it. The direction that matters is one way:
`handler.py` imports `model.py` freely, `model.py` imports neither `handler`
nor the two framework modules a route lives inside -- `pacct.web.mount` (the
dispatcher) and `pacct.web.session` (the cookie, the per-visitor state and the
per-visitor directories).

The check is on the import graph and not on a naming convention because a
model that reaches back does so by importing, and it does it silently: nothing
else in the suite would fail. `dnp_map`, `gle_tabs` and `vlan_mapper` pass it
today; the tools still to be split pick it up the moment they grow a
`model.py`, which is the point of discovering them with a glob rather than
listing them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pacct.paths import PACKAGE_DIR

# The framework a route lives inside. A model that imports either of these has
# stopped being domain logic.
_FORBIDDEN_MODULES = ("pacct.web.mount", "pacct.web.session")


def _model_files() -> list[Path]:
    return sorted((PACKAGE_DIR / "web").glob("*/model.py"))


def _imported_names(path: Path) -> list[str]:
    """Every dotted name the file imports, in both spellings.

    `from pacct.web.dnp_map import model` names a module (`pacct.web.dnp_map`)
    AND an attribute of it that is itself a module, so both the module and
    `module.name` are reported -- otherwise `from pacct.web.vlan_mapper import
    handler` would slip through as an import of the package.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports are forbidden repo-wide, but spell one out
            # rather than drop it: `from .handler import x` must still fail
            # this test and not pass it by being unreadable.
            prefix = "." * node.level + (node.module or "")
            names.append(prefix)
            names.extend(f"{prefix}.{alias.name}" for alias in node.names)
    return names


def _offends(name: str) -> bool:
    if name in _FORBIDDEN_MODULES:
        return True
    if any(name.startswith(f"{module}.") for module in _FORBIDDEN_MODULES):
        return True
    return name.rsplit(".", 1)[-1] == "handler"


def test_a_model_never_imports_the_web_layer():
    offenders: list[str] = []
    for path in _model_files():
        for name in _imported_names(path):
            if _offends(name):
                offenders.append(f"{path.parent.name}/model.py imports {name}")
    assert offenders == []


def test_the_check_is_not_vacuous():
    """A rename or a move that leaves no `model.py` behind would make the test
    above pass by finding nothing, so name the tools that are split today."""
    found = {path.parent.name for path in _model_files()}
    assert {"dnp_map", "gle_exporter", "gle_tabs", "settings_compare",
            "vb_updater", "vlan_mapper"} <= found
