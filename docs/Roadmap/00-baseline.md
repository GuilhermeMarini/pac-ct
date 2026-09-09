# Baseline — the tree as it stands

Measured 2026-09-08 against `pac-ct` 1.11.4. All three roadmap items depend
on these facts; if any of them changes, the documents that rest on it need
re-reading.

---

## 1. Runtime shape

`app.py` bootstraps a virtualenv and hands off to `pacct.web.dashboard:main()`,
which builds **one** `ThreadingHTTPServer` on port 8765. Nine mounts are
registered in a hardcoded list at `dashboard.py:256-275`:

```
/                    Home (menu)
/files               Arquivos do Projeto
/glv                 Graphical Logic Viewer
/vb-updater          VB Updater
/vlan-mapper         VLAN Mapper
/gle-exporter        Exportador de Comentários GLE
/settings-compare    Settings Compare
/dnp-map             DNP Map
/gle-tabs            GLE tab organiser
```

There is no web framework. The HTTP layer is `http.server`'s
`BaseHTTPRequestHandler` and `ThreadingHTTPServer` from the standard library.
Routing is hand-written `if path == "/state": ... elif ...` per tool.

`mount._dispatch()` strips the mount prefix from `self.path` and assigns
`self.__class__` to the tool's handler class, so each tool's routes are written
as though it owned the root. `mount.inject_head()` then regex-injects into the
served HTML: a `fetch`/`XMLHttpRequest` shim that re-prefixes absolute paths,
the theme stylesheet, the `data-theme` attribute, the theme picker (home only),
the progress runtime and the library runtime.

---

## 2. Code inventory

### The hand-rolled framework

| Module | Lines | Responsibility |
|---|---:|---|
| `web/mount.py` | 530 | dispatch, prefix stripping, class swap, head injection, `/library` `/progress` `/theme.css` `/static` |
| `web/session.py` | 534 | cookie `selsid`, per-visitor state and directory, TTL sweeper |
| `web/progress.py` | 368 | job reporting, `/progress?job=` |
| `web/theme.py` | 32 | theme resolution |
| `web/themes/` | 1,586 | three themes that emit **different markup**, not only different CSS |
| **Total** | **3,050** | |

This is a web framework. It is competent — the comments in `session.py` and
`mount.py` document real bugs it exists to prevent — but it is private, it is
undocumented as a framework, and it has exactly one maintainer.

### Per tool

| Tool | Python | HTML | of which inline JS | Python modules |
|---|---:|---:|---:|---:|
| `glv` | 5,281 | 4,308 | 3,160 | 12 |
| `vb_updater` | 1,610 | 822 | 502 | **1** |
| `dnp_map` | 1,166 | 1,831 | 1,168 | 4 |
| `project_files` | 1,002 | 288 | 196 | 5 |
| `gle_exporter` | 942 | 390 | 241 | **1** |
| `gle_tabs` | 822 | 901 | 581 | 5 |
| `settings_compare` | 754 | 980 | 626 | **1** |
| `vlan_mapper` | 323 | 527 | 355 | **1** |

**6,829 lines of JavaScript live inside HTML files inside a Python package.**
They have no build, no module system, no linting and no tests. This is the
application's front end, and it is the single largest untooled body of code in
the tree.

Four tools are still one file (`__init__.py`): `vb_updater` (67 KB),
`gle_exporter` (39 KB), `settings_compare` (28 KB), `vlan_mapper` (12 KB).
That is `BACKLOG.md` item 4 (S3), open and described there as "deliberately
opportunistic".

### Layers, by fate in any rewrite

| Layer | Lines | In a framework migration |
|---|---:|---|
| Hand-rolled framework | 3,050 | mostly **deleted** |
| HTTP handlers, split tools (`*/handler.py`) | 2,139 | **rewritten** mechanically |
| HTTP handlers, buried in the 4 single-file tools | ~1,500 (estimate) | **rewritten**, after those tools are split |
| Domain logic inside `web/` (`model.py`, `diagram.py`, `link.py`, `poll.py`, `library.py`, …) | 4,389 | **does not move** |

The genuinely valuable logic — RDB parsing, SCL reading, MMS, the Compound
File writer — is already outside this repository in `sellib`, `py61850` and
`cfbwrite`, and is untouched by anything in this folder.

---

## 3. The dependency graph between tools

Measured by grepping each tool's package for imports of every other tool's
package, excluding intra-package imports and excluding `dashboard.py` (whose
job is to import all of them):

```
glv               -> project_files    (1 import)
dnp_map           -> project_files    (1 import)
gle_tabs          -> project_files    (1 import)
gle_exporter      -> project_files    (1 import)
vb_updater        -> project_files    (1 import)
vlan_mapper       -> project_files    (1 import)
settings_compare  -> project_files    (1 import)
```

**That is the complete list. There are no other tool-to-tool edges.**

`project_files` is additionally imported by `mount.py` (`library as filelib`)
and by `session.py` (`derived`, `library as filelib`,
`client.inject_library_runtime`). A module the dispatcher and the session layer
both depend on is not a tool; it is platform that happens to also have a
screen.

This is the most important measurement in this folder, and it was got wrong on
the first attempt. A naive grep for `from pacct.web.<tool>` counts a tool
importing *itself* — `glv/handler.py` importing `glv.transport` — and produces
a picture of heavy tool-to-tool coupling that does not exist. The tools are
already isolated from one another.

### What tools import from the platform

| Platform module | Import lines from tools |
|---|---:|
| `pacct.paths` | 21 |
| `pacct.web.session` | 14 |
| `pacct.web.rdb_write` | 11 |
| `pacct.core` | 10 |
| `pacct.web.xlsx_names` | 8 |
| `pacct.compat` | 7 |
| `pacct.web.progress` | 7 |
| `pacct.web.mount` | 6 |
| `pacct.web.themes` | 6 |

This set, plus `project_files`, is the de facto platform API. It has never been
written down as one.

---

## 4. Constraints that shape every option

These are not preferences. They come from where the application runs.

1. **A substation usually has no internet.** `app.py` runs pip only when an
   import fails, never a version check on boot, because a pip failure exits the
   application and takes all nine tools down with it. The offline bundle
   carries `vendor/` wheels and installs with `--no-index --find-links`.
   A CDN is banned; the nine `.woff2` fonts ship in `web/static/fonts/`.

2. **One port.** `tools/windows/wsl-portproxy-setup.ps1` and the firewall
   guidance assume 8765 and nothing else. Splitting into two listening
   processes breaks that, and buys nothing on a laptop.

3. **A dependency that fails to resolve takes the whole application down.**
   The `py61850>=0.2.0.dev1` incident is the recorded case: PEP 440 sorts a
   `.devN` before its release, PyPI carried no `0.2.0`, pip failed, `app.py`
   exited, and not one tool came up.

4. **Writing bytes into a protection relay is the highest-consequence action
   in the application.** `VERSION`'s MAJOR is reserved for a change to what
   gets written into a relay. Anything that widens who can execute code in this
   process inherits that consequence.

5. **904 tests collected across 56 files.** `tests/web_harness.py` drives
   handlers in-process with no socket. It does **not** cover the dispatcher (prefix
   stripping, class swap, `Set-Cookie`, `/library`, `/progress`) or anything
   visual, and nothing relay-dependent is tested or simulated.

---

## 5. Shared prerequisites

Work all three ideas need, and which stands on its own merits regardless.
`IMPLEMENTATION-ORDER.md` schedules these as Stage 0, on the Foundation track.

### 5.1 Split the four single-file tools

`vb_updater`, `gle_exporter`, `settings_compare` and `vlan_mapper` each have
routing, domain logic and HTML generation in one `__init__.py`. Neither idea
can proceed cleanly through them: Idea 1 cannot separate their handlers from
their models, Idea 2 cannot package them as units with a declared surface, and
Idea 3 cannot give them a live document.

Already `BACKLOG.md` item 4. The route tests in `tests/test_web_routes_*.py`
mean a split that breaks something fails loudly, which is why the backlog
describes the risk as low.

### 5.2 Write down the platform API

Section 3 lists what tools actually import. Nothing declares that list as a
contract, so every one of those imports is equally revocable and equally
load-bearing, with no way to tell which. All three ideas need the distinction:
Idea 1 to know what survives a framework change, Idea 2 to know what a plugin
is allowed to reach, Idea 3 to know where the document session sits.

This is a documentation task first — describe what is already true — and only
then a question of whether the boundary should move.

### 5.3 Decide what `project_files` is

It is imported by all seven other tools, by `mount.py` and by `session.py`,
and `/library` is served by the dispatcher rather than by the tool. It is
platform wearing a tool's clothes. All three ideas need this resolved, and the
answer is probably to split it: a `library` platform module, and a thin
`/files` screen that is a tool like any other.
