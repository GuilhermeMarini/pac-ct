# Idea 1 — Phases

Each phase ships on its own, leaves the application working, and is a
defensible stopping point. Nothing here is scheduled; the order is a
dependency order, not a timetable.

Effort figures are **rough estimates**, not measurements, and are stated in
relative terms because absolute ones would be invented.

---

## Phase 0 — Extract the JavaScript

**Needs no decision. Reversible. No new dependency. Do this regardless of
whether any later phase happens.**

Move the 6,829 lines of inline `<script>` out of the templates into
`web/static/js/<tool>/*.js`, loaded with `<script src>` through the `/static`
route `mount.py` already serves.

| | |
|---|---|
| Depends on | nothing |
| Effort | moderate, and almost entirely mechanical |
| Risk | low — but see below |
| Delivers | a real frontend/backend seam, lintable and diffable JavaScript, browser caching, and the prerequisite for Idea 2 |

**The one real hazard:** some inline scripts read values the server
substituted into the document. The prefix shim sets `window.MOUNT_PREFIX`, and
the theme picker is generated with `json.dumps` of the theme list. Extracted
files cannot receive those by substitution. The fix is a single
`<script type="application/json" id="page-data">` block per page that the
external module reads — which is also better than what is there now, because
it stops mixing data into code.

Order within the phase: smallest first (`project_files`, 196 lines; then
`gle_exporter`, 241; `vlan_mapper`, 355) to establish the pattern, and the GLV
last (3,160 lines in one file).

**Stopping here is a legitimate end state.** It may deliver most of the
clarity the request was after.

---

## Phase 1 — Split the four single-file tools

`vb_updater` (67 KB), `gle_exporter` (39 KB), `settings_compare` (28 KB),
`vlan_mapper` (12 KB) each become a package with `handler.py`, a model module
and `templates/`.

| | |
|---|---|
| Depends on | nothing, but easier after Phase 0 |
| Effort | moderate |
| Risk | low — `tests/test_web_routes_*.py` makes a bad split fail loudly |
| Delivers | `BACKLOG.md` item 4 (S3) closed; a prerequisite for Phases 2 and 4, and for Idea 2 |

This is already on the backlog as "deliberately opportunistic". Both roadmap
items are the occasion it was waiting for.

---

## Phase 2 — Declare the HTTP contract

Write down every route, its parameters and its response shape. No behaviour
changes. Regularise the `{"error": …}` convention.

| | |
|---|---|
| Depends on | Phase 1 |
| Effort | small to moderate |
| Risk | very low — documentation plus tests |
| Delivers | the contract both Idea 1 and Idea 2 need; the input to a generated client later |

If FastAPI is chosen (Phase 4), this phase's output is what the Pydantic
models are written from, and much of it becomes generated rather than
maintained.

---

## Phase 3 — Collapse the three theme markup dialects

Make `web/themes/` CSS-only: one markup structure, three token sets. Retire
`_NAV_RE`, `_HOME_RE`, `nav_html()` and `home_html()`.

| | |
|---|---|
| Depends on | Phase 0 |
| Effort | moderate; mostly CSS and judgement |
| Risk | **the highest visual risk in this document** — every screen in three themes, and the only gate is manual |
| Delivers | removes the reason `inject_head` must rewrite markup at all; unblocks a static shell document |

**This phase is contested** — see `04-open-questions.md`. It can be skipped, at
the cost of carrying three markup dialects into whatever comes next.

---

## Phase 4 — Adopt a backend framework

`http.server` → FastAPI or Flask. Delete the class swap, the route chains and
the prefix shim.

| | |
|---|---|
| Depends on | Phases 1, 2 |
| Effort | **large** — the biggest item in this folder |
| Risk | moderate on the backend; the acceptance criteria in `02-specification.md` §5 are the guard |
| Delivers | ~3,000 lines of private framework deleted; a conventional, recognisable stack |

Sequencing inside the phase: migrate one tool at a time behind the existing
dispatcher if possible, GLV last. The GLV's poll threads, relay locks and
`/events` are the part that needs design attention, especially under an async
framework.

---

## Phase 5 — Adopt a frontend toolchain

Only if B3 is chosen. Vite, TypeScript, a component framework; built assets
vendored into the offline bundle.

| | |
|---|---|
| Depends on | Phases 0, 2; benefits from 3 and 4 |
| Effort | **large**, and it introduces a second supply chain |
| Risk | the offline bundle — see below |
| Delivers | a conventional frontend; type safety across the seam; the GLV becomes maintainable |

**The offline-bundle work is part of this phase, not an afterthought.**
`build_dist.py` must vendor built assets the way it vendors wheels, CI must
build them, and the "it passed on the build machine because the cache was
warm" failure has to be guarded against — `pin_direct_references()` is the
precedent for how that guard is written and why it stays even when dormant.

Node is a **build-machine** dependency and must never become a substation one.
That distinction should be asserted by a test that boots the bundle with no
network, as the vendoring already is.

---

## Dependency graph

```
Phase 0  (extract JS)  ────┬──────────────► Phase 3 (themes CSS-only)
                           │                     │
                           └──► Idea 2           │
                                                 ▼
Phase 1  (split 4 tools) ──► Phase 2 (contract) ──► Phase 4 (backend framework)
                                     │                     │
                                     └─────────────────────┴──► Phase 5 (frontend)
```

## Recommended stopping points

| Stop after | End state | Reasonable? |
|---|---|---|
| Phase 0 | Same architecture, frontend has a home | **Yes** — possibly the best value-for-effort in the whole roadmap |
| Phase 2 | Contract declared, tools split, still `http.server` | **Yes** — and it fully unblocks Idea 2 |
| Phase 4 | Conventional Python backend, vanilla frontend | Yes |
| Phase 5 | Conventional full stack | Yes, if the GLV justifies it |

The one combination to avoid is **Phase 4 without Phase 0** — rewriting 3,600
lines of plumbing while leaving all 6,829 lines of the actual problem exactly
where they are.
