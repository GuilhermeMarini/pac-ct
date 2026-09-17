"""A tool may import the declared platform, and nothing else of this application.

`declared_platform_api.py` lists what a tool may reach; `docs/PLATFORM-API.md`
explains why the boundary sits there. Neither is load-bearing at runtime, so
nothing would notice if a tool grew an import into a corner of the application
nobody meant to expose -- which is the state B13 found, where every import was
equally revocable and equally load-bearing with no way to tell which.

So the guard is a test, and it runs in both directions, the way
`test_http_contract.py` guards the route table:

- **A tool importing something undeclared fails.** This is the direction Stage
  3 is expected to trip when the document session arrives, and its message
  says so.
- **A declared module no tool imports fails too.** That is what keeps the list
  a measurement rather than a wish. B13's draft declared `pacct.web.mount` and
  `pacct.web.themes` at six import lines each; measured, no tool imports
  either, and this direction is what would have caught it.

The extraction is syntactic, over the syntax tree, for the reason
`test_tool_layering.py` gives: a tool that reaches back does so by importing,
and it does it silently -- nothing else in the suite would fail. Importing the
handlers instead would need a `SessionManager`, a logger and a temporary
directory per mount, and would still only see what the import happened to
reach.

**This is the sibling of `test_tool_layering.py` and does not replace it.**
That one is about direction inside a tool -- `model.py` may not reach the
framework, `pacct.library` may not reach `pacct.web` at all. This one is about
the surface a whole tool package may reach outward. A change can break either
without touching the other.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.declared_platform_api import PLATFORM, TOOLS, PlatformModule, permits

SRC = Path(__file__).resolve().parent.parent / "src"
WEB = SRC / "pacct" / "web"


# -- reading the tree ---------------------------------------------------------

def _tool_files() -> dict[str, list[Path]]:
    """Tool package -> every `.py` in it.

    Discovered by glob, not from `TOOLS`: a tool added tomorrow is held to the
    boundary the day it lands, without anybody remembering to list it. `TOOLS`
    exists to prove the glob found something, not to drive it.
    """
    return {
        package.name: sorted(package.rglob("*.py"))
        for package in sorted(WEB.iterdir())
        if package.is_dir() and (package / "handler.py").exists()
    }


def _module_names() -> set[str]:
    """Every dotted module and package name that exists under `src/`.

    Needed to tell `from pacct import library` (a MODULE, and the spelling
    seven tools use) from `from pacct.paths import is_within` (a name). Without
    it the first reads as an import of `pacct` itself, which is covered by no
    entry and would fail for the wrong reason.
    """
    names = set()
    for path in SRC.rglob("*.py"):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return names


def _package_of(path: Path) -> str:
    """The package a file lives in -- `pacct.web.glv` for both `glv/poll.py`
    and `glv/__init__.py`."""
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    return ".".join(parts[:-1])


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """`from ..session import X` inside `pacct.web.glv` -> `pacct.web.session`.

    There are no relative imports in the tree today and the conventions forbid
    them, but spell one out rather than drop it: a relative reach into another
    tool has to fail this test, not slip past it by being unreadable.
    """
    parts = package.split(".")
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    return f"{base}.{module}" if module else base


def extract_imports() -> set[tuple[str, str]]:
    """`(tool, dotted module)` for every import a tool makes of `pacct`.

    Third-party imports are not collected. `sellib`, `py61850` and `openpyxl`
    are the dependency list, which `requirements.txt`, `pyproject.toml` and
    `test_version.py` already declare and guard between them; this contract is
    about the application reaching into itself.

    A tool's imports of its OWN package are not collected either. Those are
    internal structure -- the four `from pacct.web.<tool>.handler import ...`
    lines are each a package's `__init__` reaching its own handler -- and
    `test_tool_layering.py` is where the rule about them lives.
    """
    modules = _module_names()
    found: set[tuple[str, str]] = set()
    for tool, paths in _tool_files().items():
        own = f"pacct.web.{tool}"
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = (
                        _resolve_relative(_package_of(path), node.level, node.module)
                        if node.level
                        else (node.module or "")
                    )
                    # `from pkg import sub` names a module when `pkg.sub` is
                    # one; otherwise the names are attributes and the import
                    # is of `pkg` itself.
                    submodules = [
                        f"{base}.{alias.name}"
                        for alias in node.names
                        if f"{base}.{alias.name}" in modules
                    ]
                    targets = submodules or [base]
                else:
                    continue
                for dotted in targets:
                    if dotted != "pacct" and not dotted.startswith("pacct."):
                        continue
                    if dotted == own or dotted.startswith(f"{own}."):
                        continue
                    found.add((tool, dotted))
    return found


def _covered_by(entry: PlatformModule, dotted: str) -> bool:
    if dotted == entry.name:
        return True
    return entry.unit == "package" and dotted.startswith(f"{entry.name}.")


# -- the two directions -------------------------------------------------------

def test_every_module_a_tool_imports_is_declared():
    """A tool reaching outside the platform API fails here."""
    undeclared = sorted({(tool, name) for tool, name in extract_imports()
                         if not permits(name)})
    assert not undeclared, (
        "tools import modules that the platform API does not declare:\n  "
        + "\n  ".join(f"{tool} -> {name}" for tool, name in undeclared)
        + "\n\nThis is the failure Stage 3 is meant to reach when the document "
          "session arrives, and it is not a mistake to be silenced. Either the "
          "import belongs somewhere already declared, or the module is platform "
          "now -- in which case add it to PLATFORM in "
          "tests/declared_platform_api.py AND to docs/PLATFORM-API.md, with the "
          "reason, deliberately."
    )


def test_every_declared_module_is_imported_by_a_tool():
    """A declared module no tool reaches any more fails here.

    The direction that keeps the list honest. A permission with nothing behind
    it describes a boundary around empty space, and it cannot be checked in
    the other direction at all -- there is no import for a drift test to point
    at. B13's draft carried two such rows.
    """
    imported = {name for _, name in extract_imports()}
    unused = [entry.name for entry in PLATFORM
              if not any(_covered_by(entry, name) for name in imported)]
    assert not unused, (
        "declared platform modules that no tool imports:\n  "
        + "\n  ".join(unused)
        + "\n\nThe list is a measurement of the import graph, not a wish. Either "
          "the import went away and the entry should go with it -- say why in "
          "docs/PLATFORM-API.md, as the section on the two modules B13's draft "
          "listed does -- or something moved and the extraction can no longer "
          "see it."
    )


# -- the property that was a coincidence and is now a rule --------------------

def test_no_tool_imports_another_tool():
    """Eight tools, zero edges between them. Measured, and now encoded.

    It was true by accident until PR #33 moved the project-files dependency
    into `pacct.library`, where every tool reaches it as platform instead of
    reaching into a sibling. Worth keeping because it is what lets a tool be
    read, moved or deleted without reading the other seven -- and worth a test
    because one import line would undo it silently.

    Not redundant with the undeclared check above, which a tool-to-tool import
    also trips today: it stops the wrong repair. Adding the sibling to
    `PLATFORM` would silence that one and leave the edge in place, and this
    test would still fail.
    """
    packages = {f"pacct.web.{tool}" for tool in _tool_files()}
    offenders = sorted(
        (tool, name) for tool, name in extract_imports()
        if any(name == other or name.startswith(f"{other}.") for other in packages)
    )
    assert not offenders, (
        "a tool imports another tool:\n  "
        + "\n  ".join(f"{tool} -> {name}" for tool, name in offenders)
        + "\n\nWhat both tools need belongs in the platform, not in whichever of "
          "them happened to grow it first. See docs/PLATFORM-API.md."
    )


# -- the guards that stop the three above passing by finding nothing ----------

def test_the_extraction_is_not_vacuous():
    """A rename or a move that left no tool package behind, or an extractor
    that stopped resolving imports, would make every assertion above pass by
    measuring an empty set. So name what has to be found.

    `pacct.paths` and `pacct.web.session` are the two modules all eight tools
    import -- the first for its own `TEMPLATES_DIR`, the second for the
    `SessionHandler` its handler factory subclasses. A tool that had neither
    would not be a tool.
    """
    found = extract_imports()
    assert set(TOOLS) <= {tool for tool, _ in found}, (
        "the glob found no imports for some declared tool package"
    )
    for module in ("pacct.paths", "pacct.web.session"):
        importers = {tool for tool, name in found if name == module}
        assert set(TOOLS) <= importers, (
            f"{module} is imported by {sorted(importers)}, not by all eight "
            f"tools -- either a tool stopped needing it, which is a finding, or "
            f"the extraction stopped seeing it, which is a bug in this file."
        )


def test_the_declared_tools_are_packages_under_web():
    """`TOOLS` names what the glob has to find. A tool added later is held to
    the boundary by the glob without being listed here, which is why this is a
    subset check and not an equality -- the same idiom
    `test_tool_layering.py` uses for the split tools."""
    assert set(TOOLS) <= set(_tool_files())


# -- the granularity decision, kept tied to what it was read off --------------

def _defines_all(package: str) -> bool:
    """Does this package's `__init__.py` publish a curated surface?"""
    init = SRC / Path(*package.split(".")) / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                return True
    return False


@pytest.mark.parametrize("entry", PLATFORM, ids=lambda e: e.name)
def test_the_unit_of_each_entry_still_matches_its_package(entry: PlatformModule):
    """Package-level iff the package curates a surface; module-level iff it does not.

    B13 did not choose this granularity by taste -- it read it off the two
    `__init__.py` files. `pacct/library/__init__.py` re-exports eighteen names
    and says in prose that a tool should write one import line;
    `pacct/core/__init__.py` is zero bytes and exports nothing a tool could
    import. Those two facts are what the units mean, so this test fails when
    one of them stops being true, and the question is reopened rather than
    quietly answered the old way.
    """
    if entry.unit == "package":
        assert _defines_all(entry.name), (
            f"{entry.name} is declared at package level because its __init__ "
            f"curates a surface, and it no longer defines __all__. A package "
            f"entry covers every submodule; if there is nothing curating what "
            f"that means, the honest unit is the module. Revisit the "
            f"granularity section of docs/PLATFORM-API.md."
        )
    else:
        parent = entry.name.rsplit(".", 1)[0]
        assert not _defines_all(parent), (
            f"{entry.name} is declared at module level because {parent} curates "
            f"nothing, and {parent}/__init__.py now defines __all__. That is a "
            f"new surface, and whether the entry should become a package entry "
            f"is a decision -- make it in docs/PLATFORM-API.md rather than here."
        )
