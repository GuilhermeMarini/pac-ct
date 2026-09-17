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
import re

from pacct.paths import PROJECT_ROOT, STATIC_DIR

_FRONTEND = PROJECT_ROOT / "frontend"
_SOURCE = _FRONTEND / "src" / "vlan_mapper" / "landing.ts"
_BUILT = STATIC_DIR / "js" / "vlan_mapper" / "landing.js"
_CI = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


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
