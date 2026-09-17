"""The set of modules a tool may import from the rest of the application.

This is the machine-readable half of `docs/PLATFORM-API.md`. The document
carries the reasoning -- what each module is for, why the boundary sits where
it does, which two modules B13's draft listed that no tool actually imports --
and this carries the list, so `test_platform_api.py` can assert the two never
drift from the tree.

**It is test data, not a shipped artifact**, the same shape
`declared_routes.py` uses for the HTTP contract. Nothing under `src/` imports
it; imports still happen the ordinary way and nothing consults a registry. A
runtime allow-list with no runtime consumer would be a second source of truth,
which is the defect a declared boundary exists to remove rather than to add.

## What is declared, and at what granularity

An entry is a **permission**: a tool may import this module, and the test
fails on any `pacct.*` import that no entry covers. Two units exist, and which
one an entry gets is not a matter of taste -- it is read off the package's own
`__init__.py`:

- **`unit="package"`** -- the package curates a surface. `pacct/library/
  __init__.py` re-exports eighteen names and says in prose that it does so
  "*so a tool writes one import line*", and that `derived` and `client` are
  deliberately not among them. A package that has written down its own inside
  and outside is declared whole, and `pacct.library.model` is covered by it.
- **`unit="module"`** -- the package curates nothing. `pacct/core/__init__.py`
  is zero bytes: `pacct.core` exports no name a tool could import, so the
  module is the only honest unit and `pacct.core.relay_conn` is named on its
  own.

`test_platform_api.py` checks that this correspondence still holds, so filling
in an empty `__init__.py` reopens the question instead of silently answering
it.

## What is deliberately NOT declared here

- **The counts.** `docs/PLATFORM-API.md` carries a measured table -- statements,
  imported names, tools. Those move whenever somebody splits an import over two
  lines, and a number the suite does not guard is a number that drifts. The
  test asserts the **set of modules**; the counts are dated prose.
- **Which names are imported.** Same reason. `pacct.paths` hands out fourteen
  distinct names today; pinning that list would fail on every new constant and
  prove nothing about the boundary.
- **Third-party imports.** `sellib`, `py61850`, `openpyxl` and the rest are the
  dependency list, which `requirements.txt` and `pyproject.toml` already
  declare and `test_version.py` already guards. This contract is about the
  application reaching into itself.
"""

from __future__ import annotations

from typing import NamedTuple


class PlatformModule(NamedTuple):
    """One module a tool may import.

    `unit` is `"package"` (submodules are covered too) or `"module"` (this
    exact dotted name and nothing below it). See the granularity note above.
    """

    name: str
    unit: str
    summary: str


#: The eight tool packages under `src/pacct/web/`: a directory with a
#: `handler.py`. `dashboard.py` is the ninth mount but is not one of these --
#: it is the shell that composes the mount table, so it imports `mount.serve`
#: and every tool's handler factory by definition, and holding it to a tool's
#: boundary would only describe the composition root as a violation.
#:
#: The test discovers these by glob and uses this tuple only to prove the glob
#: found something, the way `test_tool_layering.py` names the split tools.
TOOLS: tuple[str, ...] = (
    "dnp_map",
    "files",
    "gle_exporter",
    "gle_tabs",
    "glv",
    "settings_compare",
    "vb_updater",
    "vlan_mapper",
)


#: Nine entries, covering the ten distinct module spellings measured in the
#: tree -- `pacct.library` is a package entry and covers `pacct.library.model`
#: as well as the bare `from pacct import library`.
PLATFORM: tuple[PlatformModule, ...] = (
    PlatformModule(
        "pacct.paths", "module",
        "The project's canonical paths. The only module all eight tools import, and the only one none of them could work without: each reads its own TEMPLATES_DIR, and the four that serve a /download read `is_within`, which is the containment check those routes are sandboxed by.",
    ),
    PlatformModule(
        "pacct.web.session", "module",
        "`SessionHandler` -- the base class every tool's handler factory builds on, and the widest surface here by far. It is also how a tool reaches `mount`, `themes`, `progress` and `library.client` WITHOUT importing them: `_send` runs the three injectors on any text/html body.",
    ),
    PlatformModule(
        "pacct.web.rdb_write", "module",
        "The one place a tool writes bytes back into an RDB. Four tools reach it; the three that write a new Compound File import the module, and `xml_text_escape` and `with_suffix_before_ext` are taken by name.",
    ),
    PlatformModule(
        "pacct.library", "package",
        "The visitor's project files. Seven tools spell it `from pacct import library as filelib` and use the eighteen re-exported names; `web/files/` spells it `from pacct.library import model` and uses only re-exported names too, so the two spellings are interchangeable today. Declared whole because the package's own `__init__` says that is the unit.",
    ),
    PlatformModule(
        "pacct.core.target_region", "module",
        "The TARGET region of the Fast Message database on SEL-400 relays. `glv` only, and shared with `cli/runner.py` rather than owned by either.",
    ),
    PlatformModule(
        "pacct.core.relay_conn", "module",
        "Relay connection tweaks and the seam with the vendored `selprotopy`. `glv` only, and shared with `cli/runner.py`. It is also what makes `pacct.compat` load-bearing -- see F2 in the document.",
    ),
    PlatformModule(
        "pacct.web.xlsx_names", "module",
        "`sanitize_sheet_name`, shared by the two tools that write spreadsheets. The narrowest module here: one function, two callers, and both alias it to `_sanitize_sheet_name` on import.",
    ),
    PlatformModule(
        "pacct.web.progress", "module",
        "`REGISTRY` and `JobReporter`. Reached directly by `glv` alone, because it is the only tool that starts work a request does not wait for; every other tool gets its reporter from `SessionHandler.job()` and the client runtime from `_send`.",
    ),
    PlatformModule(
        "pacct.compat", "module",
        "`ensure_telnetlib`, called at `glv/poll.py:32` immediately before `import selprotopy`. Declared because a tool imports it; recorded as a finding because what needs the ordering is `pacct.core.relay_conn`, which does not enforce it.",
    ),
)


def declared_names() -> set[str]:
    """The dotted name of every entry, whatever its unit."""
    return {module.name for module in PLATFORM}


def permits(dotted: str) -> bool:
    """Is this imported module covered by an entry?

    A `"module"` entry covers its exact name. A `"package"` entry covers the
    package and everything under it. A bare `pacct` is covered by neither, on
    purpose: it reaches every module in the application through attribute
    access and would make the boundary unobservable.
    """
    for module in PLATFORM:
        if dotted == module.name:
            return True
        if module.unit == "package" and dotted.startswith(f"{module.name}."):
            return True
    return False
