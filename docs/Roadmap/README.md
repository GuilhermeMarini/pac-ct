# PAC CT — Roadmap

This folder maps work that is **wanted but not committed to**. It is not a
backlog of defects (that is `docs/BACKLOG.md`) and not a record of what was
done (that is `docs/ENGINEERING-NOTES.md` and `docs/MIGRATION.md`). It holds
the two structural changes the application has been asked to consider, each
with its measured starting point, the stack it would need, a specification of
the end state, and a phased route from one to the other.

Written 2026-09-08 and extended 2026-09-09, against `pac-ct` 1.11.4,
`sellib` 3.0.1, `py61850` 0.3.0, `cfbwrite` 1.0. Every number in these
documents was measured against that tree; none is carried over from an
earlier document. Where a
number is an estimate rather than a measurement, it says so.

**Nothing here is decided.** These documents exist so the decision can be made
against facts instead of impressions.

---

## The three ideas

| | Idea | One line | Folder |
|---|---|---|---|
| **1** | Split frontend from backend | Stop maintaining a private web framework, and give 6,829 lines of JavaScript a home | [`01-frontend-backend-split/`](01-frontend-backend-split/) |
| **2** | Tools as installable plugins | Give `gelib` and `siemenslib` a host, and decouple tool releases from app releases | [`02-tool-plugins/`](02-tool-plugins/) |
| **3** | A live SCL editor | Reimplement OpenSCD's concepts on `py61850`, which is 1,695 lines of read-only and already the right shape | [`03-scl-editor/`](03-scl-editor/) |

[`00-baseline.md`](00-baseline.md) is the measured description of the tree as
it stands today. Both ideas depend on it, and it also records the shared
prerequisites — the work that has to happen regardless of which idea proceeds,
or whether either does.

---

## How each idea's folder is laid out

Identical in all three, so they can be read side by side:

| File | What it holds |
|---|---|
| `README.md` | What the idea is, why it was raised, what it is *not*, and its status |
| `01-stack.md` | Technology options with trade-offs, and a recommendation |
| `02-specification.md` | The target end state, precisely enough to build against |
| `03-phases.md` | An ordered route, each phase independently shippable |
| `04-open-questions.md` | Decisions that are genuinely open, with what each turns on |

Idea 3 adds two annexes of its own: `05-py61850-gap.md` (what `py61850` has,
lacks and gains, with the reference library mapped onto targets) and
`06-plugin-port-list.md` (the 32 upstream plugins triaged, and the
functional-spec format that makes a rebuild checkable).

---

## Status board

| Item | State | Blocked on |
|---|---|---|
| Idea 1 — frontend/backend split | **discussed, not decided** | the framework question in `01/04-open-questions.md` — though Idea 3 now forces B2 or B3 |
| Idea 1, Phase 0 (extract inline JS) | **ready, no decision needed** | nothing — it is reversible and independent |
| Idea 2 — tool plugins | **discussed, not decided** | Idea 1 Phase 0, and the trust boundary in `02/04-open-questions.md` |
| Idea 3 — SCL editor | **architecture decided, scope not** | Idea 1 Phase 0 and its frontend choice; the IEC data licensing question in `03/04-open-questions.md` Q1 |
| Idea 3, Phases 1–2 (`py61850` writes SCL) | **ready, and independently valuable** | nothing — no pac-ct work involved |
| Shared prerequisite — split the 4 single-file tools | **ready** | nothing; already `BACKLOG.md` item 4 (S3) |

---

## The one ordering constraint

**Idea 1's Phase 0 comes before Idea 2, and the reason is specific.**

Today a tool's HTML reaches the browser through `mount.inject_head()`, which
regex-injects the `fetch` shim, the theme stylesheet, `data-theme`, the theme
picker, the progress runtime and the library runtime into `<head>`. If tools
become plugins while that is still how a page is assembled, then the class
swap in `mount._dispatch()`, the regex injection and the `fetch` shim all
become the **public plugin contract** — the part a third-party tool is written
against, and therefore the part that can no longer be changed.

That is the trap. Extracting the JavaScript first (Idea 1 Phase 0) gives every
tool a `static assets + JSON routes` shape, which is a far better thing to
freeze into a contract than a class-swapping dispatcher.

Ideas 1 and 2 otherwise proceed independently, and neither requires the other
to complete.

**Idea 3 depends on both, and settles a question in Idea 1.** It needs Idea 1
Phase 0 for the same reason Idea 2 does, and it needs Idea 2's plugin contract
only in the sense that its panes are ordinary tools in a new menu group. What
it *decides* is Idea 1's stack: an SLD designer, a subscription binder and a
DataTypeTemplates browser cannot be built in inline `<script>` blocks, so
Idea 3 turns the B2/B3 choice from a preference into a requirement.

Its own Phases 1 and 2 are the exception to all of this — they are `py61850`
work, they need nothing from the other two ideas, and they leave that library
able to write SCL whether or not an editor is ever built on top.
