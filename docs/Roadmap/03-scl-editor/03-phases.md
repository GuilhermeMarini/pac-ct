# Idea 3 — Phases

Each phase leaves the application working and is a defensible stopping point.
The order is a dependency order, not a timetable. Effort figures are **rough
estimates**, not measurements.

The shape worth noticing: **Phases 1 and 2 deliver value to `py61850` on their
own**, before pac-ct grows a single new screen. A library that can write SCL
with fidelity is useful to anyone, including this project's existing tools.

---

## Phase 0 — Prerequisites

Not part of this idea, but nothing here proceeds without them.

| From | What | Why |
|---|---|---|
| Idea 1 Phase 0 | Extract inline JavaScript | The ordering constraint in the roadmap index |
| Idea 1 Phase 1 | Split the four single-file tools | `vb_updater` and `vlan_mapper` are in the overlap area |
| Idea 1 stack | **Decide B2 or B3** | An SLD designer and a subscription binder cannot live in inline `<script>`. Idea 3 is what forces this choice |
| Baseline §5.3 | Split `project_files` | The document session sits beside `library` as platform |

---

## Phase 1 — `py61850` writes SCL, faithfully

Serialisation with the guarantees in `01-stack.md` §3: comments kept, prefixes
registered, unused declarations re-emitted from `_declared_namespaces()`.

| | |
|---|---|
| Depends on | nothing |
| Effort | small to moderate |
| Risk | low — it is a pure function with an exact test |
| Delivers | **the round-trip guarantee**, and it is independently useful |

**The whole phase is validated by one test:** load every SCD in the corpus,
export with **zero edits**, and compare. Only the documented cosmetic
differences may appear. That test is written first and is the phase.

**Stopping here is legitimate** — `py61850` becomes a read-write SCL library,
which is a real improvement regardless of whether an editor is ever built.

---

## Phase 2 — `py61850` gains the edit layer

The four primitives, each computing its inverse; the parent map; the
schema-ordering tables; the creation constraints; the generators.

| | |
|---|---|
| Depends on | Phase 1 |
| Effort | **large** — this is the `scl-lib` equivalent, estimated 3,000–5,000 lines |
| Risk | moderate; the ordering tables are where correctness is subtle |
| Delivers | everything in `05-py61850-gap.md` §3 |

Order inside the phase: primitives and inverses first, then the parent map,
then ordering, then per-element rules. The property test —
`undo(apply(e))` restores the tree — is written with the primitives, not after.

This phase can be split across several `py61850` releases; nothing forces it
to land at once.

---

## Phase 3 — The document session in pac-ct

`pacct.scl_session`: working documents, copy-on-write from a library entry, the
persisted journal, global undo/redo, `editCount`, push over `/events`, export
through `derived.adopt()`.

| | |
|---|---|
| Depends on | Phase 2 |
| Effort | moderate |
| Risk | moderate — the sweeper interaction is the sharp edge |
| Delivers | the shared context every pane will use; **no user-visible screen yet** |

The crash-recovery test belongs here: kill the process mid-session, restart,
and confirm the working document rebuilds from baseline plus journal.

---

## Phase 4 — The first two panes

**Substation** and **Describe**, plus the "Ferramentas SCL" menu group.

| | |
|---|---|
| Depends on | Phase 3, and Idea 1's frontend decision |
| Effort | moderate |
| Risk | **this is where the architecture meets reality** |
| Delivers | proof the whole stack works end to end |

**This is the decision point the route is built around.** If the edit API is
awkward to drive from a pane, if the push channel is fiddly, or if rebuilding
against the theme tokens is more expensive than estimated, it surfaces here at
the cost of two simple panes rather than seventeen.

Both panes get their functional spec written first, per `06-plugin-port-list.md` §2.

---

## Phase 5 — IED lifecycle

Edit IED, Rename IEDs, Remove IEDs, Add IED / Add IEDs.

| | |
|---|---|
| Depends on | Phase 4 |
| Effort | moderate |
| Risk | moderate — rename fan-out and removal cleanup are the real content |
| Delivers | the first genuinely laborious manual task the tool removes |

Rename is the one to get right: it must follow every `ExtRef@iedName`, not
just the `IED@name`.

---

## Phase 6 — Communication

One pane, from three upstream plugins (Communicate, Edit Communication,
Explore Communication).

| | |
|---|---|
| Depends on | Phase 5 |
| Effort | moderate |
| Risk | low |
| Delivers | SubNetwork / ConnectedAP / address editing, and the first place the RDB cross-reference appears |

---

## Phase 7 — Publish, Subscribe, Supervise

The core commissioning value: control blocks and DataSets, `ExtRef` binding,
LGOS/LSVS supervision.

| | |
|---|---|
| Depends on | Phase 6 |
| Effort | **large** |
| Risk | moderate |
| Delivers | the reason to do any of this |

**The live-relay cross-reference lands here** — showing whether a bound
`ExtRef` is toggling on the relay right now, through the existing MMS and
telnet transports. It is the capability OpenSCD structurally cannot have, and
it is worth treating as a first-class goal of the phase rather than a
follow-up.

---

## Phase 8 — The vendor subscribers

SEL later-binding into `sellib`; Siemens into `siemenslib`.

| | |
|---|---|
| Depends on | Phase 7 |
| Effort | moderate |
| Risk | low — the vendor split already works this way |
| Delivers | the SEL-specific behaviour, which is directly this project's domain |

---

## Phase 9 — Validate and Compare

| | |
|---|---|
| Depends on | Phase 4 (Compare); Phase 7 (Validate is most useful once editing is real) |
| Effort | moderate |
| Risk | **Validate is constrained by IEC copyright on the XSD and NSD files** — see `04-open-questions.md` Q1 |
| Delivers | schema and semantic checking, and document diff |

---

## Phase 10 — SLD designer, and Stencil

| | |
|---|---|
| Depends on | everything |
| Effort | **large** |
| Risk | high |
| Delivers | single-line diagram editing |

**The most likely thing to be cut**, and nothing else depends on it. See
`06-plugin-port-list.md` §4.

---

## Dependency graph

```
Idea 1 Phase 0 + Phase 1 + frontend decision (B2/B3)
Baseline §5.3 (split project_files)
        │
        ▼
Phase 1  py61850 writes SCL faithfully   ◄── independently valuable
        ▼
Phase 2  py61850 edit layer  (the big one)
        ▼
Phase 3  pacct.scl_session
        ▼
Phase 4  Substation + Describe           ◄── the real test
        ▼
Phase 5  IED lifecycle
        ▼
Phase 6  Communication
        ▼
Phase 7  Publish / Subscribe / Supervise ◄── the point of the exercise
        ▼
Phase 8  sellib + siemenslib subscribers
        ▼
Phase 9  Validate + Compare
        ▼
Phase 10 SLD + Stencil                   ◄── most likely to be cut
```

## Recommended stopping points

| Stop after | End state | Reasonable? |
|---|---|---|
| Phase 1 | `py61850` is a read-write SCL library | **Yes** — useful on its own, and the smallest honest increment |
| Phase 2 | The 61850-6 edit know-how exists in Python | **Yes** — a real contribution to the toolkit, with no pac-ct work |
| Phase 4 | Two panes, whole stack proven | **Yes, and it is the honest decision point.** If it hurts, stop here having learned cheaply |
| Phase 7 | A working commissioning SCL editor | **Yes** — this is the target |
| Phase 9 | Feature-complete but for the SLD | Yes |
| Phase 10 | Everything | Only if Phase 7 landed well |

**Phases 1 and 2 are worth doing even if the editor is abandoned.** They make
`py61850` a complete library rather than half of one, and every existing tool
that reads an SCD gains the ability to write one back.
