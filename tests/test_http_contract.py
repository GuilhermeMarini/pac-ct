"""The declared route table and the tree may not drift apart.

`declared_routes.py` lists every route; `docs/HTTP-CONTRACT.md` explains them.
Neither is load-bearing at runtime, so nothing would notice if a route were
added, renamed or deleted and the contract left behind -- which is exactly how
the previous state of affairs arose, where the contract existed only as the
union of nine `if path ==` chains and no one had said what it was.

So the guard is a test, and it runs in both directions. A new route that is not
declared fails; a declared route that no longer exists fails too. The second
direction is the one that matters in a year: deleting a route is easy to do and
easy to forget to write down.

The extraction is syntactic, over the syntax tree, deliberately. Importing the
handlers and introspecting them would need a `SessionManager`, a logger and a
temporary directory per mount, and would still only find routes the import
happened to reach. The `if path == "..."` chains are right there in the source.

**What this test cannot see**, and what the document therefore has to carry on
its own: parameters, response bodies and status codes. A declaration nobody
verifies is worse than no declaration, so those are prose and are not listed in
`declared_routes.py` as if they were checked.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.declared_routes import (
    INFRASTRUCTURE,
    LANDING_ALIASES,
    MOUNTS,
    declared_pairs,
)

WEB = Path(__file__).resolve().parent.parent / "src" / "pacct" / "web"

#: `do_GET` -> "GET". The two verbs the dispatcher forwards; there is no
#: `do_PUT` or `do_DELETE` anywhere in the tree, which is itself part of the
#: contract and is recorded in the document.
VERBS = {"do_GET": "GET", "do_POST": "POST"}


def _handler_files() -> dict[str, Path]:
    """Package name -> the module whose `do_GET`/`do_POST` hold its routes."""
    found = {name: WEB / name / "handler.py" for name in MOUNTS if name != "dashboard"}
    found["dashboard"] = WEB / "dashboard.py"
    return found


def _mentions_path(test: ast.expr) -> bool:
    """Does this `if` test branch on the request path?

    The handlers spell the local three ways -- `path`, `route` and, in the
    dispatcher, `tail` -- and one of them compares `self.path` directly. Any of
    those makes the branch a route; anything else (a body key, a file suffix)
    is not, and its string constants must not be collected.
    """
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and node.id in ("path", "route", "tail"):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "path":
            return True
    return False


def _paths_in(test: ast.expr) -> list[str]:
    """Every route spelling compared against in one `if` test.

    Covers the three forms in use: `path == "/x"`, `path in ("/x", "/y")` and
    `path.startswith("/pages/")`. The empty string is a landing alias, which is
    why it is collected alongside the paths that start with a slash.
    """
    out = []
    for node in ast.walk(test):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("/") or node.value == "":
                out.append(node.value)
    return out


def extract_routes() -> set[tuple[str, str, str]]:
    """`(mount, method, path)` for every route in the tree.

    Only `do_GET` and `do_POST` bodies are read. A route dispatched from a
    helper would be missed -- and would show up as a declared route the tree
    appears not to have, which is a failure and not a silence.
    """
    found: set[tuple[str, str, str]] = set()
    for mount, source in _handler_files().items():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in VERBS:
                continue
            method = VERBS[node.name]
            for branch in ast.walk(node):
                if not isinstance(branch, ast.If) or not _mentions_path(branch.test):
                    continue
                for raw in _paths_in(branch.test):
                    # The landing page answers to three spellings and the nine
                    # mounts do not agree on which they accept. They are one
                    # route, declared once, as "/".
                    path = "/" if raw in LANDING_ALIASES else raw
                    found.add((mount, method, path))
    return found


# -- the two directions -------------------------------------------------------

def test_every_route_in_the_tree_is_declared():
    """A new route that nobody wrote down fails here."""
    undeclared = extract_routes() - declared_pairs()
    assert not undeclared, (
        "routes exist in the tree but not in declared_routes.py:\n  "
        + "\n  ".join(f"{m} {v} {p}" for m, v, p in sorted(undeclared))
        + "\n\nAdd them to tests/declared_routes.py AND to docs/HTTP-CONTRACT.md."
    )


def test_every_declared_route_exists_in_the_tree():
    """A route deleted from the tree but left in the contract fails here."""
    missing = declared_pairs() - extract_routes()
    assert not missing, (
        "routes declared in declared_routes.py no longer exist in the tree:\n  "
        + "\n  ".join(f"{m} {v} {p}" for m, v, p in sorted(missing))
        + "\n\nRemove them from tests/declared_routes.py AND from docs/HTTP-CONTRACT.md."
    )


def test_the_declared_mounts_are_the_ones_the_dashboard_mounts():
    """`MOUNTS` is the prefix map, and `dashboard.py` is where it is real."""
    source = (WEB / "dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    prefixes = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "Mount"
                and node.args and isinstance(node.args[0], ast.Constant)):
            raw = node.args[0].value
            # `Mount.__init__` normalises "/" to the empty string: the root
            # mount is the fallback, not a prefix anything is stripped from.
            prefixes.add("" if raw == "/" else "/" + raw.strip("/"))
    assert prefixes == set(MOUNTS.values())


# -- the rule that is a rule and not an accident ------------------------------

def test_the_infrastructure_routes_are_answered_before_a_session_exists():
    """None of the five may sit below the line that mints a `selsid`.

    This is the property `mount.py` explains at length and that the contract
    records: a stylesheet, a font, a progress poll or a library read arriving
    without a cookie cannot invent a visitor. When they could, every such
    response handed out a fresh `selsid`, so concurrent requests traded the
    browser's identity between them and the project's file list appeared to
    erase itself.

    Asserted structurally rather than over a socket because the ordering IS
    the mechanism: a socket test would prove that today's code does not mint,
    while this proves it cannot without someone moving the branch.
    """
    tree = ast.parse((WEB / "mount.py").read_text(encoding="utf-8"))
    dispatch = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_dispatch")

    # The line the infrastructure routes have to stay above: `sessions.resolve`
    # is what creates a session and sets the `Set-Cookie`. It has to be matched
    # on the RECEIVER and not on the attribute name -- `themes.resolve(cookie,
    # ...)` is called twenty lines earlier, resolves a theme rather than an
    # identity, and matching `.resolve` alone silently measures against that
    # one instead, which puts the line above every route and passes for the
    # wrong reason.
    resolve_line = min(
        n.lineno for n in ast.walk(dispatch)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", "") == "resolve"
        and getattr(getattr(n.func, "value", None), "id", "") == "sessions"
    )

    for route in INFRASTRUCTURE:
        # `/static/` and `/library` are matched on the stripped tail, `/progress`
        # on the raw path; all that matters here is where the branch sits.
        needle = route.path.rstrip("/") or "/"
        lines = [n.lineno for n in ast.walk(dispatch)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value.rstrip("/") == needle]
        assert lines, f"{route.path} is no longer matched in _dispatch"
        assert max(lines) < resolve_line, (
            f"{route.method} {route.path} is now handled AFTER the session is "
            f"resolved (line {max(lines)} vs {resolve_line}). An infrastructure "
            f"route must never create a session -- see mount.py's _dispatch."
        )


@pytest.mark.parametrize("route", INFRASTRUCTURE, ids=lambda r: r.path)
def test_no_tool_claims_an_infrastructure_route(route):
    """A tool defining `/theme.css` of its own would never be reached.

    The dispatcher answers these before it matches any prefix, so a tool route
    with the same spelling is dead code that looks live. Worth a test because
    the failure is silent: the tool's branch simply never runs.
    """
    clashes = [(m, v, p) for (m, v, p) in extract_routes()
               if p.rstrip("/") == route.path.rstrip("/")]
    assert not clashes, (
        f"{route.path} is served by the dispatcher, so these are unreachable: "
        f"{clashes}"
    )
