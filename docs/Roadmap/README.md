# PAC CT — Roadmap

This folder maps work that is **wanted but not committed to**. It is not a
backlog of defects (that is `docs/BACKLOG.md`) and not a record of what was
done (that is `docs/ENGINEERING-NOTES.md` and `docs/MIGRATION.md`). It holds
the two structural changes the application has been asked to consider, each
with its measured starting point, the stack it would need, a specification of
the end state, and a phased route from one to the other.

Written 2026-09-08 against `pac-ct` 1.11.4, `sellib` 3.0.1, `py61850` 0.3.0,
`cfbwrite` 1.0. Every number in these documents was measured on that date
against that tree; none is carried over from an earlier document. Where a
number is an estimate rather than a measurement, it says so.

**Nothing here is decided.** These documents exist so the decision can be made
against facts instead of impressions.

---

## The two ideas

| | Idea | One line | Folder |
|---|---|---|---|
| **1** | Split frontend from backend | Stop maintaining a private web framework, and give 6,829 lines of JavaScript a home | [`01-frontend-backend-split/`](01-frontend-backend-split/) |
| **2** | Tools as installable plugins | Give `gelib` and `siemenslib` a host, and decouple tool releases from app releases | [`02-tool-plugins/`](02-tool-plugins/) |

[`00-baseline.md`](00-baseline.md) is the measured description of the tree as
it stands today. Both ideas depend on it, and it also records the shared
prerequisites — the work that has to happen regardless of which idea proceeds,
or whether either does.

---

## How each idea's folder is laid out

Identical in both, so they can be read side by side:

| File | What it holds |
|---|---|
| `README.md` | What the idea is, why it was raised, what it is *not*, and its status |
| `01-stack.md` | Technology options with trade-offs, and a recommendation |
| `02-specification.md` | The target end state, precisely enough to build against |
| `03-phases.md` | An ordered route, each phase independently shippable |
| `04-open-questions.md` | Decisions that are genuinely open, with what each turns on |

---

## Status board

| Item | State | Blocked on |
|---|---|---|
| Idea 1 — frontend/backend split | **discussed, not decided** | the framework question in `01/04-open-questions.md` |
| Idea 1, Phase 0 (extract inline JS) | **ready, no decision needed** | nothing — it is reversible and independent |
| Idea 2 — tool plugins | **discussed, not decided** | Idea 1 Phase 0, and the trust boundary in `02/04-open-questions.md` |
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

Both ideas otherwise proceed independently, and neither requires the other to
complete.
