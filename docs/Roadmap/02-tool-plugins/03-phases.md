# Idea 2 — Phases

Each phase ships on its own and leaves the application working. Effort figures
are **rough estimates**, not measurements.

The shape of this route is unusual and worth noting up front: **the first four
phases deliver the whole internal benefit and never install anything from the
web.** Distribution is phase 5 onward. That means most of the value is
reachable without ever taking on the trust, catalogue or supply-chain work.

---

## Phase 0 — Prerequisites

Not part of this idea, but nothing here proceeds cleanly without them.

| | |
|---|---|
| Idea 1 Phase 0 | Extract inline JavaScript. **The ordering constraint** — see below |
| Idea 1 Phase 1 | Split the four single-file tools, so each is packageable |
| Baseline §5.3 | Split `project_files` into a platform `library` + a `/files` tool |

**Why Idea 1 Phase 0 comes first:** today a tool's HTML reaches the browser
through `mount.inject_head()`, which regex-injects the `fetch` shim, the theme
stylesheet, `data-theme`, the theme picker, the progress runtime and the
library runtime into `<head>`. If tools become plugins while that is still how
a page is assembled, then the class swap, the regex injection and the `fetch`
shim become the **public plugin contract** — the part external tools are
written against, and therefore the part that can no longer be changed.

Freezing a class-swapping dispatcher into a public contract is the one
genuinely irreversible mistake available in this roadmap.

---

## Phase 1 — Declare the platform API

Write down the module list in `02-specification.md` §2. No behaviour change.
Add a test asserting no tool imports outside it.

| | |
|---|---|
| Depends on | Phase 0 |
| Effort | small |
| Risk | very low |
| Delivers | the boundary every later phase depends on; also serves Idea 1 |

The measured graph says the tools already comply — no cross-tool imports
exist. This phase makes that a rule rather than a coincidence.

---

## Phase 2 — Introduce the `Tool` descriptor and discovery

Replace the hardcoded list at `dashboard.py:256-275` with
`importlib.metadata.entry_points(group="pacct.tools")`. Wrap every plugin
import and `build()` call so a failure logs, skips and continues.

**The nine existing tools are converted to use the same mechanism** — declared
as entry points in `pac-ct`'s own `pyproject.toml`. The host dogfoods its own
contract from the first day, which is what stops the contract drifting from
what the tools actually need.

| | |
|---|---|
| Depends on | Phase 1 |
| Effort | small to moderate |
| Risk | low, and this is where the boot-isolation tests get written |
| Delivers | the mechanism, proven against nine real tools, with nothing yet distributed |

---

## Phase 3 — Move the menu catalogue to the tools

`web/themes/items.py` holds the catalogue today — central, and owned by the
theme layer. Each tool contributes `label`, `description` and `order`; the
theme keeps ownership of the *markup*.

| | |
|---|---|
| Depends on | Phase 2 |
| Effort | small |
| Risk | low, but touches all three themes — visual check needed |
| Delivers | a plugin can appear in the menu without editing host code |

---

## Phase 4 — Extract one real tool out of the host

Take one existing tool — **`vlan_mapper` is the candidate**: 323 lines of
Python, 527 of HTML, one dependency edge (`project_files`), the smallest in the
tree — and move it to its own repository, released independently, installed
into the venv.

| | |
|---|---|
| Depends on | Phases 2, 3 |
| Effort | moderate; mostly repository and release plumbing |
| Risk | moderate — this is where the contract meets reality |
| Delivers | **proof the contract works from outside the host.** Every assumption in `02-specification.md` is tested here for the first time |

**This is the phase that decides whether Idea 2 is real.** If the platform API
turns out to be insufficient, or the release seam turns out to be intolerable,
it surfaces here at the cost of one small tool rather than nine.

The workspace convention already applies and gets harder: *a change to a
library is not finished until `pac-ct`'s suite passes against it*, and nothing
in CI checks that seam. With N plugins the seam is N-wide. Phase 4 is where
that cost becomes concrete rather than theoretical.

---

## Phase 5 — A plugin bundle format and an offline install

The zip format in `02-specification.md` §5, with vendored wheels. Install from
a local file — **no network involved at all**.

| | |
|---|---|
| Depends on | Phase 4 |
| Effort | moderate; `build_dist.py` is the template and much generalises |
| Risk | moderate — the `pin_direct_references()` trap applies |
| Delivers | plugins installable from a USB stick, which is the substation case |

Verify with `PIP_NO_CACHE_DIR=1` and no network, as the application's own
vendoring already is. A pass with a warm pip cache means nothing.

---

## Phase 6 — The catalogue and online install

A `plugins.json` index published as a release asset; fetch, verify, install
reusing `update.py`.

| | |
|---|---|
| Depends on | Phase 5 |
| Effort | moderate — most of it is `update.py` parameterised |
| Risk | low on mechanism, because the hard parts are already solved and proven |
| Delivers | the request as literally stated: install plugins from the web, like the app updates |

Carries the invariant rewrite from `01-stack.md` Part D: *the application never
needs the network to run what is already installed* — stated in the contract,
and asserted by a no-network boot test.

---

## Phase 7 — The "Ferramentas disponíveis" screen

Installed plugins with versions and uninstall; available plugins with install;
an honest offline state.

| | |
|---|---|
| Depends on | Phase 6 |
| Effort | moderate |
| Risk | low |
| Delivers | the point at which this becomes a user-visible feature |

---

## Phase 8 — `gelib` and `siemenslib` get plugins

A GE `.urs` tool and a Siemens DIGSI tool, each in its own repository, built
against the contract.

| | |
|---|---|
| Depends on | Phase 7 |
| Effort | genuinely new product work, unlike everything above |
| Risk | product risk, not architectural |
| Delivers | **the actual motivation.** Two libraries that exist, are correctly shaped, and today have no consumer at all, finally reach a user |

---

## Dependency graph

```
Idea 1 Phase 0 (extract JS)
Idea 1 Phase 1 (split 4 tools)      ──┐
Baseline §5.3  (split project_files) ─┤
                                      ▼
                              Phase 1 (declare API)
                                      ▼
                              Phase 2 (discovery)
                                      ▼
                              Phase 3 (menu)
                                      ▼
                              Phase 4 (extract vlan_mapper)   ◄── the real test
                                      ▼
                              Phase 5 (bundle, offline install)
                                      ▼
                              Phase 6 (catalogue, online install)
                                      ▼
                              Phase 7 (UI)  ──►  Phase 8 (gelib, siemenslib)
```

## Recommended stopping points

| Stop after | End state | Reasonable? |
|---|---|---|
| Phase 3 | Tools discovered dynamically, still all shipped together | **Yes** — most of the internal tidiness, none of the distribution cost |
| Phase 4 | One tool released independently | **Yes, and it is the honest decision point.** If this hurts, stop; the answer has been learned cheaply |
| Phase 5 | Offline plugin install from a file | Yes — arguably the right end state for substation work |
| Phase 8 | Full plugin ecosystem, GE and Siemens tools shipping | Yes, if Phase 4 went well |

**Phases 1–3 are worth doing even if the plugin idea is later abandoned.**
They are ordinary decoupling: a declared platform API, dynamic discovery, and
a catalogue owned by the tools rather than the theme layer. None of it is
wasted if distribution never happens.
