"""The frontend build, and the contract its output has to fit into.

`vlan_mapper` is the pilot: since B14 its script is produced by Vite out of
`frontend/src/vlan_mapper/`, instead of being written by hand at the path the
page loads, and since B16 that source is TypeScript. Nothing about the served
page changed in either phase, and that is what this file asserts -- the build
is configured to match the tree, not the other way round.

**B16 ended one assertion that B14 could make and this file no longer can.**
B14 checked that the built script contained the source verbatim, which was the
whole promise of a phase that moved 367 lines without touching them. Esbuild
strips the types, and measured it strips much more than that: of the source's
65 comment lines the output keeps 1, quotes are normalised to double and
redundant parentheses are dropped. The output is no longer the source in any
sense, so what stands in its place is a landmark test -- the output still has
to contain the calls that make the tool a tool, in order.

**And Vite does not type-check.** The esbuild transform deletes annotations
without reading them, so a green build says nothing about type correctness.
`tsc --noEmit` is what says it, and the last test here is the one that keeps it
wired into CI rather than trusting a comment.

The five rules the served contract keeps are in `docs/ENGINEERING-NOTES.md`,
section "Where the JavaScript lives". Four of them are decided by the build's
configuration, and each of those four is a Vite default pointing the other
way:

- the tag is a **classic script**, because `type="module"` is deferred by
  definition and `inject_progress_runtime` splices `SelProgress` in before
  `</body>` *after* the page's own script;
- the output name is **fixed and unhashed**, at the absolute path the template
  hard-codes and `mount.py` answers under all nine prefixes;
- **no source map is emitted**, because `.map` is not a type `mount.py` knows
  and a browser with `nosniff` refuses what comes back as
  `application/octet-stream`;
- the output is **not minified**, because it is a tracked file that has to stay
  readable in a diff.

`mount.py` is read through `ast` rather than imported: its handler class lives
inside a factory closure, so there is nothing to import. That is the shape
`test_http_contract.py` and `test_tool_layering.py` already use.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

from pacct.paths import PROJECT_ROOT, STATIC_DIR

_FRONTEND = PROJECT_ROOT / "frontend"
_SOURCE = _FRONTEND / "src" / "vlan_mapper" / "landing.ts"
_BUILT = STATIC_DIR / "js" / "vlan_mapper" / "landing.js"
_CI = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
_BUNDLES = _FRONTEND / "bundles.json"


def _bundles() -> list[dict]:
    """The build's own inventory, read as data.

    `frontend/build.js` loops over this and calls Vite once per row, because
    Vite refuses more than one entry when the format is `iife` -- and `iife` is
    the served contract, not a preference. The table is JSON rather than a
    literal inside `build.js` so that THIS FILE can read it: the suite has no
    node and must never need one, which is exactly what
    `test_bundle_without_node_or_network.py` exists to keep true.
    """
    return json.loads(_BUNDLES.read_text(encoding="utf-8"))


def _static_types() -> dict[str, str]:
    """`_STATIC_TYPES` out of `mount.py`, which cannot be imported."""
    tree = ast.parse((PROJECT_ROOT / "src" / "pacct" / "web" / "mount.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_STATIC_TYPES":
                    return ast.literal_eval(node.value)
    raise AssertionError("_STATIC_TYPES is no longer an assignment in mount.py")


# -- the source, and what the build does to it -------------------------------

def test_the_pilots_source_lives_in_the_frontend_package():
    assert _SOURCE.is_file()


def test_the_pilots_source_is_typescript():
    # One source, and no `.js` left beside it: two files at the same path with
    # different extensions is how a build quietly keeps compiling the old one.
    assert _SOURCE.suffix == ".ts"
    assert not _SOURCE.with_suffix(".js").exists()


def test_the_built_script_still_does_what_the_tool_does():
    # What replaces B14's verbatim comparison, which transpilation ended.
    # These are the calls that make the screen a screen: the picker that
    # chooses the SCD, the POST that reads it, the two display preferences,
    # the clipboard copy and the boot fetch. In this order, because the order
    # is the page's lifecycle -- and in the output's own spelling, since
    # esbuild normalises quotes.
    built = _BUILT.read_text(encoding="utf-8")
    landmarks = [
        'SelLibrary.picker("pick-scd", {',
        "SelProgress.post(",
        'localStorage.getItem("vlan-mapper-fmt")',
        'localStorage.getItem("vlan-mapper-mode")',
        "navigator.clipboard.writeText(csv)",
        'fetch("/state", { cache: "no-store" })',
    ]
    at = -1
    for mark in landmarks:
        found = built.find(mark, at + 1)
        assert found > at, f"the built script no longer contains, in order: {mark}"
        at = found


def test_the_types_do_not_reach_the_browser():
    # The other half of the same fact: what esbuild strips has to be gone.
    # A type name in the served file means the transform did not run, which is
    # the shape a misconfigured `entry` would take.
    built = _BUILT.read_text(encoding="utf-8")
    assert "interface " not in built
    assert "VlanMapperRow" not in built
    assert "VlanMapperState" not in built


def test_the_built_script_names_where_its_source_is():
    # Whoever opens the served file has to be able to find the file to edit.
    first = _BUILT.read_text(encoding="utf-8").splitlines()[0]
    assert "frontend/src/vlan_mapper/landing.ts" in first


# -- the four traps ----------------------------------------------------------

def test_the_built_script_is_a_classic_script():
    # Checked as the IIFE envelope rather than as an absence of `import`, and
    # the difference was measured: this file imports and exports nothing, so a
    # build switched to `format: 'es'` produces output with no ESM syntax in it
    # at all and an absence test passes while the contract is broken. The
    # envelope is what `format: 'iife'` actually leaves behind, so it is what
    # fails when someone takes it away.
    lines = [line for line in _BUILT.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[1].startswith("(function"), "the built script is no longer wrapped as a classic script"
    assert lines[-1] == "})();"
    # And no ESM syntax smuggled inside the envelope, which is how this would
    # break once the pilot grows a second file to import.
    text = _BUILT.read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s", text, re.MULTILINE)
    assert not re.search(r"^\s*export[\s{]", text, re.MULTILINE)


def test_the_build_emits_one_file_at_the_served_path():
    # No hash in the name and no `assets/` beside it: the template hard-codes
    # the path and `mount.py` serves it under every mount prefix.
    assert sorted(p.name for p in _BUILT.parent.iterdir()) == ["landing.js"]


def test_no_static_file_has_a_type_mount_cannot_serve():
    # A source map would land here as `.map`, be served as
    # `application/octet-stream` and be refused by any browser with `nosniff`.
    known = set(_static_types())
    unknown = sorted({p.suffix for p in STATIC_DIR.rglob("*") if p.is_file()} - known)
    assert unknown == []


def test_the_landing_loads_the_script_the_way_the_contract_requires():
    from pacct.web import vlan_mapper

    html = vlan_mapper.LANDING_HTML
    tag = '<script src="/static/js/vlan_mapper/landing.js"></script>'
    assert tag in html
    assert " defer" not in html
    assert " async" not in html
    assert 'type="module"' not in html
    # Immediately before `</body>`: `inject_progress_runtime` splices
    # `SelProgress` in at the same point, after this tag, and the order is what
    # lets the page's own script leave `SelProgress` alone at the top level.
    after = html.split(tag, 1)[1]
    assert after.split("</body>", 1)[0].strip() == ""


# -- the toolchain is pinned -------------------------------------------------

def test_node_is_pinned_to_an_exact_version():
    # B15 asserts the committed output is byte-identical to a fresh build, so a
    # floating node version would surface there as an intermittent CI failure
    # with no code change behind it.
    assert re.fullmatch(r"\d+\.\d+\.\d+", (_FRONTEND / ".nvmrc").read_text(encoding="utf-8").strip())


def test_the_build_dependencies_are_not_committed():
    assert "node_modules/" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not (_FRONTEND / "package-lock.json").read_text(encoding="utf-8").strip() == ""


# -- the gate Vite cannot be ------------------------------------------------

def test_typecheck_is_wired_and_runs_before_the_build():
    """`tsc --noEmit` is the phase's gate, and CI is where it binds.

    Vite transpiles TypeScript with esbuild, which deletes annotations without
    reading them: a green `npm run build` proves nothing about types. The check
    therefore has to be its own command, and it has to be in the workflow --
    a `typecheck` script nobody runs is a comment with a JSON syntax.

    It runs BEFORE the build so a type error fails on its own terms, instead of
    surfacing afterwards as a stale-output byte diff that names the wrong
    problem.
    """
    pkg = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert pkg["scripts"]["typecheck"] == "tsc --noEmit"
    assert "typescript" in pkg["devDependencies"]

    ci = _CI.read_text(encoding="utf-8")
    assert "npm run typecheck" in ci
    assert ci.index("npm run typecheck") < ci.index("npm run build"), (
        "the type check has to run before the build, not after it"
    )


# -- the build table is the inventory, and the suite reads it ----------------

def test_every_row_of_the_build_table_has_a_source_and_a_served_output():
    rows = _bundles()
    assert rows, "the build table is empty"
    known = set(_static_types())
    for row in rows:
        assert set(row) == {"entry", "out", "global"}, f"unexpected keys in {row}"
        source = _FRONTEND / row["entry"]
        built = STATIC_DIR / row["out"]
        assert source.is_file(), f"{row['entry']} is in the build table and does not exist"
        assert built.is_file(), f"{row['out']} is in the build table and was never built"
        # The output has to be servable at the absolute path the template
        # hard-codes: a suffix `mount.py` does not know comes back as
        # `application/octet-stream` and is refused under `nosniff`.
        assert built.suffix in known, f"{row['out']} has a type mount.py cannot serve"


def test_every_built_file_says_which_row_produced_it():
    # The banner is how whoever opens the served file finds the file to edit,
    # and with more than one bundle it also has to name the RIGHT source.
    for row in _bundles():
        first = (STATIC_DIR / row["out"]).read_text(encoding="utf-8").splitlines()[0]
        assert f"frontend/{row['entry']}" in first, (
            f"{row['out']} does not name {row['entry']} as its source"
        )


def _reachable_from_the_build_table() -> set[str]:
    """Every source a build actually reads, followed through its imports.

    Written as a walk rather than as "every source is an entry", which is what
    this started as and was wrong the moment a bundle had more than one module:
    `lib/file-picker.ts` is the entry and `lib/page.ts` and `lib/library.ts`
    are what it pulls in. Only relative specifiers are followed -- there is
    nothing else to follow, since nothing here imports a package.
    """
    seen: set[str] = set()
    queue = [row["entry"] for row in _bundles()]
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        source = _FRONTEND / rel
        if not source.is_file():
            continue
        here = pathlib.PurePosixPath(rel).parent
        for spec in re.findall(r"""(?:from|import)\s+['"](\.[^'"]+)['"]""",
                               source.read_text(encoding="utf-8")):
            target = (here / spec).as_posix()
            # `import './page'` means `./page.ts` on disk.
            for candidate in (target, target + ".ts"):
                if (_FRONTEND / candidate).is_file():
                    queue.append(candidate)
                    break
    return seen


def test_no_typescript_source_is_built_into_nothing():
    """The half-migration guard, and the reason the table is data.

    A `.ts` under `frontend/src/` that no build reaches is dead in a way
    nothing else here can see: the served file stays whatever was last
    committed, edits to the source vanish, and CI's staleness check passes
    because a file nobody builds cannot go stale. The served file is still
    there, still valid, still answering 200.

    Declaration files are excluded because they are types and produce no output
    by construction.
    """
    sources = {
        p.relative_to(_FRONTEND).as_posix()
        for p in (_FRONTEND / "src").rglob("*.ts")
        if not p.name.endswith(".d.ts")
    }
    orphans = sorted(sources - _reachable_from_the_build_table())
    assert orphans == [], (
        f"no build reaches these sources: {orphans}"
    )


# -- the load order, on every screen that has one ----------------------------

def _screens() -> list[pathlib.Path]:
    """Every template that loads a tool's script.

    Discovered rather than listed, so a screen added later is covered without
    anyone remembering to add it here. Thirteen today -- which is four more
    than the nine mounts, and that gap is the point: the four the mounts miss
    include the GLV dashboard, where 2 of the application's 3 `PacPage` call
    sites live.
    """
    root = PROJECT_ROOT / "src" / "pacct" / "web"
    found = [p for p in sorted(root.glob("*/templates/*.html"))
             if "/static/js/" in p.read_text(encoding="utf-8")]
    assert len(found) >= 13, f"only {len(found)} screens found; did templates move?"
    return found


def test_every_screen_loads_the_runtimes_in_the_order_that_makes_them_work():
    """The invariant that has cost this project a blank screen, as a test.

    It was enforced by two Python functions and verified by someone
    remembering to look in a browser. What it says:

    - `SelLibrary` is injected at the END of `<head>`, so it is defined before
      the tool's own script is parsed. Every tool calls `SelLibrary.picker(...)`
      at the TOP LEVEL, and a `ReferenceError` there aborts the whole block --
      the tool renders blank and still answers 200.
    - the tool's own tag sits immediately before `</body>`, classic, with no
      `defer` and no `async`, either of which would move it past the runtime it
      depends on.
    - `SelProgress` is spliced in AFTER the tool's tag, which is why nothing
      may touch it at the top level and why it may not drift upward. Since
      B17b it is a `<script src>` like the other two rather than 219 inline
      lines, so this test names its URL instead of a variable inside it.

    Driven through the real injectors in the real order `SessionHandler._send`
    uses them, not through a reproduction of it.
    """
    from pacct.library.client import CLIENT_JS_URL, inject_library_runtime
    from pacct.web.mount import inject_head
    from pacct.web.progress import PROGRESS_JS_URL, inject_progress_runtime

    for template in _screens():
        html = template.read_text(encoding="utf-8")
        page = inject_library_runtime(inject_progress_runtime(
            inject_head(html, "", "caderno")))
        where = f"{template.parent.parent.name}/{template.name}"

        head_end = page.lower().index("</head>")
        library = page.index(CLIENT_JS_URL)
        tool = page.index('<script src="/static/js/', head_end)
        progress = page.index(PROGRESS_JS_URL, tool)
        body_end = page.rindex("</body>")

        assert library < head_end, f"{where}: the library runtime left <head>"
        assert library < tool, f"{where}: the tool's script is parsed before its runtime"
        assert tool < progress < body_end, f"{where}: SelProgress is no longer last"

        # Nothing may slip in between the tool's script and the progress
        # runtime, and nothing after it either. Measured against the progress
        # block's own opening tag rather than against `</body>`, because that
        # block IS a `<script>` and would otherwise match itself.
        tag_end = page.index("</script>", tool) + len("</script>")
        progress_tag = page.rindex("<script", tool, progress)
        assert "<script" not in page[tag_end:progress_tag], (
            f"{where}: something now loads between the tool's script and the bar"
        )
        after = page[page.index("</script>", progress) + len("</script>"):body_end]
        assert "<script" not in after, (
            f"{where}: something now loads after the progress runtime"
        )
        # The bar's tag has to keep the same shape as the tool's: a classic
        # script executed where it sits. `defer` would let the page's own
        # script run first and find `SelProgress` missing -- which is exactly
        # the ordering the inline block used to guarantee by construction.
        bar_tag = page[progress_tag:page.index("</script>", progress) + len("</script>")]
        assert " defer" not in bar_tag and " async" not in bar_tag, (
            f"{where}: the progress tag grew defer/async"
        )
        assert 'type="module"' not in bar_tag, f"{where}: the progress tag became a module"
        tool_tag = page[tool:tag_end]
        assert " defer" not in tool_tag and " async" not in tool_tag, (
            f"{where}: the tool's tag grew defer/async"
        )
        assert 'type="module"' not in tool_tag, f"{where}: the tag became a module"


# -- client code inside Python, and the three that are left ------------------

# The inline `<script>` blocks that Python still emits, each with the reason it
# has not moved. B17b took the largest one out (`progress.py`, 219 lines /
# 8,546 bytes); these are what remained, and they are listed rather than
# tolerated so that a NEW one fails this test instead of joining them quietly.
# Same shape as `KNOWN_ASYMMETRIES` in `test_relay_models.py`.
INLINE_SCRIPTS_STILL_IN_PYTHON = {
    "web/mount.py": (
        "_PREFIX_SHIM takes the mount prefix as a per-request argument and "
        "rewrites fetch/XHR -- it is what makes every OTHER script's requests "
        "work, so it cannot itself arrive by the mechanism it installs. "
        "_THEME_PICKER is home-only and doubly substituted (the theme list and "
        "the active theme)."
    ),
    "web/dashboard.py": (
        "The home's update check, home-only. A candidate for B18's measurement."
    ),
}



def _emits_an_inline_script(path: pathlib.Path) -> bool:
    """True when this module EMITS `<script>` markup, not merely mentions it.

    Read through `ast` and with docstrings excluded, because prose about a
    `<script>` is not a `<script>`: `library/client.py` discusses one in its
    module docstring and emits only a `<script src>` TAG, and a naive substring
    search calls that an offender. The distinction this test is about is
    inline code versus a tag, so the search is for the opening tag with no
    attributes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings:
                continue
            if "<script>" in node.value:
                return True
    return False


def test_no_new_inline_script_appears_in_python():
    """Client code lives in `static/js/`, and the exceptions are named.

    `docs/ENGINEERING-NOTES.md` says client code goes in `static/js/`, "never
    inside a `.html` and never inside a `.py`". The `.html` half has been true
    since the six extractions; the `.py` half was not, and B17b is what made it
    nearly true -- `progress.py` held 219 lines of it, more than the rest put
    together.

    What is left is deliberately listed above rather than silently allowed. A
    new module growing an inline `<script>` fails here.
    """
    root = PROJECT_ROOT / "src" / "pacct"
    offenders = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if _emits_an_inline_script(p)
    )
    assert offenders == sorted(INLINE_SCRIPTS_STILL_IN_PYTHON), (
        "a Python module gained or lost an inline <script>; if it gained one, "
        "either move it to frontend/src/ or write down why it cannot move"
    )


def test_the_progress_runtime_is_served_rather_than_inlined():
    """The phase's own subject, asserted where it can be seen.

    `inject_progress_runtime` emits a tag now. The check is not that the tag
    exists -- the ordering test covers that on all thirteen screens -- but that
    the RUNTIME is not coming along inside it, which is what a half-finished
    revert would look like.
    """
    from pacct.web import progress

    assert "<script>" not in progress.PROGRESS_TAG
    assert progress.PROGRESS_TAG.strip() == (
        f'<script src="{progress.PROGRESS_JS_URL}"></script>'
    )
    built = STATIC_DIR / "js" / "lib" / "progress.js"
    assert built.is_file(), "the progress runtime is not built"
    # The bar's own API, in the file the tag points at.
    text = built.read_text(encoding="utf-8")
    for member in ("begin:", "done:", "fail:", "hide:", "track:", "upload:", "post:"):
        assert member in text, f"the built runtime is missing {member}"
    assert "if (!window.SelProgress) window.SelProgress =" in text
