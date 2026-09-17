# The platform API

The set of modules a tool may import from the rest of this application, why
each one is in it, and what the boundary does not cover. Written down in B13
because until then nothing declared the list: every import was equally
revocable and equally load-bearing, with no way to tell which.

**This describes what is true today.** Where the measured boundary looks odd,
the oddity is recorded rather than repaired — B13's own rule, and the reason
the description can be trusted. Three things found while writing it are
collected in [Findings](#findings) with nothing done about them. B13's own
draft table is one of the things that was found to be stale, and the
reconciliation is below rather than quietly corrected.

## How this document is kept honest

Prose drifts from code. So the list is duplicated, once, into a place a test
can read:

| | |
|---|---|
| `docs/PLATFORM-API.md` | this file — the reasoning, the measurements, the oddities |
| `tests/declared_platform_api.py` | the nine entries as data |
| `tests/test_platform_api.py` | asserts the tree and the declaration agree **in both directions** |

A tool that imports something undeclared fails the suite. A declared module
that no tool imports fails it too — the direction that keeps the list a
measurement rather than a wish, and the one that would have caught the two
modules B13's draft carried on habit.

The declaration is **test data and ships with nothing**. Nothing under `src/`
imports it; imports still happen the ordinary way and no code consults a
registry. A runtime allow-list with no runtime consumer would be a second
source of truth, which is the defect a declared boundary exists to remove
rather than to add. It is the shape `tests/declared_routes.py` already uses
for the HTTP contract, and `tests/test_version.py` before that.

What the test **cannot** see, and what therefore lives only here: the counts,
and which names each import takes. Both move whenever somebody splits an
import over two lines or adds a constant, and a number the suite does not
guard is a number that drifts. The test asserts the **set of modules**.

### This is not `test_tool_layering.py`, and does not replace it

The two are siblings and a change can break either without touching the other:

| | `test_tool_layering.py` | `test_platform_api.py` |
|---|---|---|
| Subject | direction **inside** a tool | the surface a tool reaches **outward** |
| Asserts | `model.py` never imports `mount`/`session`/a `handler`; `pacct.library` never imports `pacct.web` **at all** | every `pacct.*` import a tool makes is declared; no tool imports another tool |
| Scope | `web/*/model.py` and `library/*.py` | every `.py` in the eight tool packages |

## What counts as a tool

A **tool** is a directory under `src/pacct/web/` containing a `handler.py`.
Eight exist: `dnp_map`, `files`, `gle_exporter`, `gle_tabs`, `glv`,
`settings_compare`, `vb_updater`, `vlan_mapper`. The test discovers them by
glob, so a ninth is held to this boundary the day it lands rather than the day
somebody remembers to list it.

`dashboard.py` is the ninth **mount** and is deliberately not one of these. It
is the composition root: it imports `mount.serve`, every tool's handler
factory and the theme module by definition, because building the mount table
is what it is for. Holding it to a tool's boundary would only describe the
composition root as a violation.

`pacct/cli/` is outside this contract entirely. It is a second entry point, not
a tool, and it shares `pacct.core` with `glv` rather than borrowing it.

## The measured surface

Measured 2026-09-17 against `main` at `448da5e`, over the syntax tree of every
`.py` in the eight tool packages, cross-checked against a raw grep. A tool's
imports of its own package are not counted; nor are third-party imports.

| Module | statements | names | tools | B13's draft said |
|---|---:|---:|---:|---:|
| `pacct.paths` | 17 | 19 | 8 | 21 |
| `pacct.web.rdb_write` | 11 | 11 | 4 | 11 |
| `pacct.web.session` | 8 | 8 | 8 | 14 |
| `pacct.library` | 7 | 7 | 7 | *(new in B11)* |
| `pacct.core.target_region` | 2 | 4 | 1 | `pacct.core` 10 |
| `pacct.core.relay_conn` | 2 | 2 | 1 | — |
| `pacct.web.xlsx_names` | 2 | 2 | 2 | 8 |
| `pacct.web.progress` | 1 | 2 | 1 | 7 |
| `pacct.compat` | 1 | 1 | 1 | 7 |
| `pacct.library.model` | 1 | 1 | 1 | — |
| `pacct.web.mount` | 0 | 0 | 0 | 6 |
| `pacct.web.themes` | 0 | 0 | 0 | 6 |
| **total** | **52** | **57** | | |

**Two columns, because the draft counted one of them and a reader will grep
for the other.** They differ in exactly three rows, and in every case because
one statement imports more than one name: `pacct.paths` at `glv/notes.py:28`
(`CACHE_DIR, atomic_write_text`) and `glv/transport/telnet.py:27` (`CACHE_DIR,
PROJECT_ROOT`), `pacct.core.target_region` at `glv/poll.py:53` (three names),
and `pacct.web.progress` at `glv/diagram.py:35` (`REGISTRY, JobReporter`).
Everywhere else one statement is one name. B13's draft column was names.

**Ten distinct modules, nine declared entries.** `pacct.library` is declared at
package level and covers `pacct.library.model` as well as the bare
`from pacct import library`; the two bottom rows are declared not at all, for
the reason in [Two modules that left](#two-modules-that-left).

**None of these numbers is guarded by a test**, and they are not meant to be.
They are dated, and re-deriving them is one AST walk.

## The nine entries

### `pacct.paths` — 17 statements, 8 tools

The project's canonical paths, and the only module every tool imports. Two
uses, and they divide cleanly: each tool reads its own `<TOOL>_TEMPLATES_DIR`
in its package `__init__`, and the four tools that serve a `/download` read
`is_within` — the containment check those routes are sandboxed by, which makes
this the one platform module a security property depends on. `glv` also takes
`CACHE_DIR`, `PROJECT_ROOT`, `resolve_gle_path` and `atomic_write_text`;
`vb_updater` takes `atomic_write_bytes`.

That every filesystem path comes from here is a convention older than this
document — the project conventions have said "all filesystem paths come from
`pacct/paths.py` constants, never a local `Path(__file__).parent`" for a long
time. What B13 adds is that the convention is now the only way a tool is
allowed to reach the filesystem's layout at all.

### `pacct.web.session` — 8 statements, 8 tools

`SessionHandler`, imported once per tool by its `handler.py` and subclassed by
the type its `build_*_handler` factory returns. The widest surface here by a
long way: per-visitor state (`sess()`), per-visitor directories (`sdir()`), the
JSON body reader and its ceiling, `library_entry()`, `publish_output()`,
`job()`, and the response helpers.

It is also **how a tool reaches four other platform modules without importing
any of them**. `SessionHandler._send` (`session.py:429–435`) runs three
injectors over any `text/html` body:

```python
body = inject_head(body, self.mount_prefix, self.theme)   # mount + themes
body = inject_progress_runtime(body)                      # progress
body = inject_library_runtime(body)                       # library.client
```

So every tool's page gets the theme stylesheet, `data-theme` on `<html>`, the
nav and home markup resolved for the visitor's direction, the `fetch` prefix
shim, the progress runtime and the library picker — and names none of them.
This is the single most important fact about the shape of the boundary, and it
is what makes the next section short instead of a list of exceptions.

### `pacct.web.rdb_write` — 11 statements, 4 tools

The one place a tool writes bytes back into an RDB, and the narrowest kind of
platform module there is: a rule with an implementation attached. `write_streams`
chooses between `olefile.write_stream` and a full `cfbwrite.rebuild()` by
whether every replacement kept its size, and writes atomically. The rule it
enforces — no tool pads XML to fit, and no tool touches `olefile` directly — is
already asserted by `tests/test_rdb_write.py`; declaring the module says the
same thing from the import side.

`dnp_map` and `gle_tabs` import the module; `gle_exporter` and `vb_updater`
import it and also take `xml_text_escape`, `with_suffix_before_ext` and
`resolve_gle_stream_path` by name.

### `pacct.library` — 7 statements, 7 tools *(package)*

The visitor's project files: the picker every tool shows, and the library a
tool's output is adopted into. Seven tools write `from pacct import library as
filelib` and use the eighteen names the package re-exports. The eighth, `files`,
writes `from pacct.library import model as library` — see F1.

This is the one entry declared at **package** level, and
[Granularity](#granularity-what-an-entry-permits) is why.

It is worth saying what this entry is not. `pacct.library` is platform because
seven tools, `web/mount.py` and `web/session.py` all depend on it, and
`tests/test_tool_layering.py` asserts the edge never runs the other way.
`pacct.web.files` — the *screen* for the same library — is an ordinary tool
with no standing at all, imported by the mount table and by nothing else. The
split landed in PR #33 and is what made the no-tool-to-tool property below
cleanly true.

### `pacct.core.target_region` — 2 statements, 1 tool

The TARGET region of the Fast Message database on SEL-400 relays:
`AsciiTargetReader`, `TARGET_REGION_BYTES`, `target_bytes_from_stream`. Reached
by `glv` alone, in `poll.py` and `transport/telnet.py`.

### `pacct.core.relay_conn` — 2 statements, 1 tool

Relay connection tweaks and the one place that touches the vendored
`selprotopy`'s privates: `channel_for`, `drain_login_banner`. Reached by `glv`
alone, in the same two files.

**Neither `core` module is owned by `glv`.** `cli/runner.py` imports both, and
the CLI is a second entry point rather than a tool. That is what makes them
platform instead of code that happens to live outside the tool that uses it:
two independent callers, one of which is not under `web/` at all.

Neither is tested without hardware, and neither is reachable from the web
screens' test harnesses. A change to either still needs a bench relay to
verify, and this document does not change that.

### `pacct.web.xlsx_names` — 2 statements, 2 tools

`sanitize_sheet_name`, shared by the two tools that write spreadsheets. The
narrowest module declared: 33 lines, one function, two callers, and both alias
it to `_sanitize_sheet_name` on import. It is platform for the ordinary reason
— Excel's worksheet-name rules are not `gle_exporter`'s opinion — and it is
the cleanest example in the tree of a module that exists only because a second
caller appeared.

### `pacct.web.progress` — 1 statement, 1 tool

`REGISTRY` and `JobReporter`, imported by `glv/diagram.py` alone.

**One tool, and that is the measurement rather than an accident of taste.**
Every tool reports progress; only `glv` reaches the module. The others call
`SessionHandler.job()`, which hands back a `JobReporter` bound to the
request's `X-Job-Id` and is a no-op when none arrived, so a handler never
branches. `glv` needs more: `POST /connect` answers 202 and the work runs in a
thread that outlives the request, so it needs a reporter the request did not
create and the registry to put it in.

### `pacct.compat` — 1 statement, 1 tool

`ensure_telnetlib`, called at `glv/poll.py:32` immediately before
`import selprotopy`. `telnetlib` was removed from the standard library in 3.13,
the project floor **is** 3.13 since 1.12.0, and the vendored `selprotopy` does a
bare `import telnetlib` at `selprotopy/__init__.py:20` — so without the shim
`glv` does not import at all.

Declared, because a tool imports it and this document describes what is true.
Recorded as F2, because the module that actually needs the ordering is
`pacct.core.relay_conn` and it does not enforce it.

## Granularity: what an entry permits

An entry is a permission, so its **unit** decides what the test lets through
tomorrow. B13 did not pick one by taste. It read the unit off each package's
own `__init__.py`, and the two packages involved say different things:

- **`pacct/library/__init__.py`** is 66 lines, re-exports eighteen names, and
  states the intent in prose: *"The names below are re-exported so a tool
  writes one import line (`from pacct import library as filelib`) … `derived`
  and `client` are NOT re-exported: only `web/session.py` reaches those."* A
  package that has written down its own inside and outside is declared whole.
- **`pacct/core/__init__.py`** is **zero bytes**. `pacct.core` exports no name
  a tool could import; it is a prefix, not a surface. So the module is the only
  honest unit, and `pacct.core.relay_conn` is named on its own. B13's draft row
  said `pacct.core`, which named something no tool can import.

**The asymmetry this produces, stated rather than hidden.** A future
`pacct.library.newmodule` imported by a tool passes the test in silence, while
a future `pacct.core.newmodule` fails it. That is the measurement, not a
compromise — and it is bounded, because `pacct.library` is four files and a new
name has to be added to its `__init__` anyway to reach the seven tools'
spelling.

**The decision is tied to what it was read off.**
`test_platform_api.py::test_the_unit_of_each_entry_still_matches_its_package`
fails if `pacct/core/__init__.py` grows an `__all__`, or if
`pacct/library/__init__.py` loses one. Either event means the fact the unit was
chosen from has stopped being true, and the question is reopened here rather
than answered silently in the test data.

## Two modules that left

B13's draft listed `pacct.web.mount` and `pacct.web.themes` at six import
lines each. **Measured today: zero, for both, across all eight tools.** They
are not declared.

They did not drift out under a refactor. They were never a Python surface for
tools, and the mechanism that replaced them is `SessionHandler`:

- **`mount`.** The dispatcher is the framework a route lives inside, and a tool
  is written as if it owned the root — that is the whole design. The one thing
  a tool's page needs from `mount` is `inject_head`, and `session.py:49` imports
  it so that `_send` can apply it to every `text/html` body. `session.py` is the
  only importer of `inject_head` in the tree. No handler imports `mount`
  either, not just no `model.py` — which is F3.
- **`themes`.** Tools consume the theme as **CSS custom properties, not as
  Python**: **1,158 `var(--…)` references** across the eight tools' templates
  (`glv` 348, `dnp_map` 241, `settings_compare` 140, `vb_updater` 135,
  `gle_tabs` 129, `vlan_mapper` 92, `gle_exporter` 59, `files` 14) against
  **zero** imports. `mount.py:199–201` puts the stylesheet immediately after
  `<head>` and before the tool's own `<style>`, deliberately, *"so the tokens
  already exist when the tool uses them"*. The rule that a tool must not define
  its own colours, radii, font stacks or paddings is real and is still the
  rule; it is simply not a rule about imports, and a Python allow-list cannot
  hold it.

**Why pruned rather than declared at zero.** A row with nothing behind it in
the tree cannot carry the second direction of the drift test — there is no
import for "declared but gone" to point at — so declaring at zero would buy a
permission nobody uses at the price of the property that keeps the rest of the
list honest. Nothing is narrowed by leaving them out: what a tool may reach
through `SessionHandler` is unchanged, and the day one genuinely needs `themes`
in Python, the test fails and the row is added deliberately. That is the same
failure Stage 3 is meant to reach, working as intended.

## No tool imports another tool

**Eight tools, zero edges between them.** Verified twice — once by AST over
every `.py` in the eight packages, once by a raw grep of each package for every
other package's name.

B13's draft asserted two things that cannot both be true: *"there are no
tool-to-tool imports at all"* and *"every tool imports `project_files` once"*.
The second is dead. PR #33 (`split-project-files`) moved that dependency into
`pacct/library/`, where every tool reaches it as platform instead of reaching
into a sibling, so the property is now cleanly true with no exception to carve
out. The four `from pacct.web.<tool>.handler import …  # noqa: E402` lines in
the tree are each a tool's own `__init__.py` importing its own handler, and are
internal structure.

It is encoded as a rule of its own rather than left to the allow-list, because
the wrong repair would pass the allow-list: adding the sibling to `PLATFORM`
silences the undeclared-import failure and leaves the edge in place. The
separate test still fails.

What the property buys is the reason to keep it: a tool can be read, moved or
deleted without reading the other seven. What both tools need belongs in the
platform, not in whichever of them happened to grow it first.

## What is not in the platform API

- **Third-party imports.** `sellib`, `py61850`, `cfbwrite`, `openpyxl`,
  `olefile`, `selprotopy` — that is the dependency list, declared in
  `requirements.txt` and `pyproject.toml` and guarded by `test_version.py`.
  This contract is about the application reaching into itself. Both contracts
  matter; they are not the same contract.
- **The CSS token surface.** The single largest thing a tool consumes from the
  platform is `web/themes/`' token vocabulary, 1,158 references, and none of it
  is an import. It is governed by convention and by review, and a boundary test
  over Python imports says nothing about it.
- **The client runtime.** `SelLibrary`, `PacPage` and `SelProgress` are reached
  from JavaScript, injected by `session.py`, and no test in this suite runs
  JavaScript at all.
- **`pacct.scl_session`.** The document session arrives in Stage 3 and is
  deliberately absent. The test is written so its arrival fails loudly, with a
  message that says to add the module here and to the declared list on purpose.
- **`dashboard.py` and `pacct/cli/`.** Neither is a tool; see
  [What counts as a tool](#what-counts-as-a-tool).

## What is tested, and what is not

`tests/test_platform_api.py`, 14 tests:

| Test | What fails it |
|---|---|
| `test_every_module_a_tool_imports_is_declared` | a tool reaching outside the nine entries |
| `test_every_declared_module_is_imported_by_a_tool` | an entry nothing imports any more |
| `test_no_tool_imports_another_tool` | one edge between two tool packages |
| `test_the_extraction_is_not_vacuous` | the glob or the resolver finding nothing |
| `test_the_declared_tools_are_packages_under_web` | a tool package renamed or moved |
| `test_the_unit_of_each_entry_still_matches_its_package` ×9 | an `__init__` that stops saying what the unit was read off |

Each of the first three and the last was checked by mutation rather than
assumed: an undeclared import, a declared-but-unused entry, a tool-to-tool
import and an `__all__` added to `pacct/core/__init__.py` each produce the
intended failure and its intended message.

**The two non-vacuity guards exist because a boundary test that passes by
finding no files is worse than none** — the idiom comes from
`test_tool_layering.py`, which carries two of its own. They name what has to be
found: the eight tool packages, and `pacct.paths` and `pacct.web.session`
imported by all eight.

Named as untested, deliberately:

- **Dynamic imports.** The extraction is syntactic. A module reached through
  `importlib.import_module(name)` or `__import__` is invisible to it. Nothing
  in the eight tool packages does this today; nothing stops one starting.
- **Attribute reach through a permitted module.** A tool holding
  `pacct.library` can read anything the package re-exports, and a tool that
  imported `pacct` itself could reach the whole application — which is why a
  bare `import pacct` is covered by no entry and fails.
- **Whether an import is a good idea.** The test asks whether a module is
  declared, never whether the dependency should exist. That judgement is what
  the Findings below are for.

## Findings

Three things found while writing this, none of them acted on. B13 describes;
each of these is a phase with its own reason.

**F1 — the library has two spellings and one of them is the odd one out.**
Seven tools write `from pacct import library as filelib`; `web/files/handler.py:25`
writes `from pacct.library import model as library`. Measured: `files` uses
eleven names from it — `EXTENSIONS`, `LIBRARY_KEY`, `FileLibrary`, `kind_for`,
`max_bytes_for`, `KIND_RDB`, `KIND_SCD`, `display_name_for`, `FileEntry`,
`files_dir`, `scd_path_for` — and **every one of them is re-exported by the
package**, so the two spellings are interchangeable today and the odd one buys
nothing. It is defensible that the library's own screen reaches the model
directly; it is also the kind of difference that is copied by the next tool
somebody scaffolds from it. Changing it is one line and zero behaviour, which
is exactly why it should be a decision and not a drive-by edit.

**F2 — `pacct.compat` is `pacct.core.relay_conn`'s precondition, enforced by
its callers.** `core/relay_conn.py:51` does `from selprotopy.protocol import
commands` at module level, and `selprotopy/__init__.py:20` does a bare
`import telnetlib`, which does not exist on the project's own floor of 3.13.
So importing `pacct.core.relay_conn` requires the shim to have run, and the
module does not run it: `glv/poll.py:32` and `cli/runner.py:49` each do, ahead
of their own `import selprotopy`. `poll.py` says as much — it "is the only one
that guarantees the ORDER". The natural repair is for the module that needs the
ordering to enforce it, which would remove `pacct.compat` from this list
entirely; it also moves an import-time side effect, which is not a change to
make while describing. (Noted while measuring and not acted on either: on this
machine `telnetlib3` 5.0.0 installs a top-level `telnetlib.py`, so
`ensure_telnetlib()` takes its early-return path. The call is still
load-bearing for an install without that.)

**F3 — `pacct.web.mount` is imported by no tool at all, not merely by no
`model.py`.** `tests/test_tool_layering.py` forbids a tool's `model.py` from
importing the dispatcher. Measured, no tool's `handler.py` imports it either,
and the only importers in the tree are `dashboard.py` (the composition root)
and `session.py` (for `inject_head`). Promoting that measurement to a rule —
declaring `mount` forbidden to a whole tool package rather than absent from the
allow-list — is defensible and is **not** what B13 did, because it forbids
something nobody has asked to do. That is a boundary move, and B13's rule is to
describe the boundary and decide separately whether it should shift.
