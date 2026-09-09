# Implementation order

How the three ideas get built: in what order, on which tracks, and split into
units small enough to finish in one sitting.

This is the **how**. `00-baseline.md` is the *what is*; the three numbered
folders are the *what for*. Written 2026-09-09.

**Stages 0 and 1 are planned in detail because nothing blocks them. Stages 2
onward are deliberately coarse** — each is gated on questions that are still
open, and planning them now would be guessing. §8 says which question gates
which stage.

---

## 1. Three tracks, not three projects

The work divides by *kind*, not by which idea it came from:

| Track | Repo | Nature |
|---|---|---|
| **A — Library** | `py61850` | Pure Python. No UI, no sessions, exhaustively testable |
| **B — Foundation** | `pac-ct` | Decoupling that all three ideas need |
| **C — Product** | `pac-ct` | New user-visible capability |

Three consequences follow, and they are the whole reason this file exists.

**The longest pole is blocked by nothing.** Idea 3 Phases 1–2 — `py61850`
gaining SCL write and the edit layer, estimated 3,000–5,000 lines — is the
largest item in the roadmap and depends on no decision, no other repo and no
other phase. Worked idea-by-idea it would sit behind all of Idea 1 and all of
Idea 2, including Idea 2's distribution half, which is the least urgent work
here. **It starts first, and it runs alongside everything else.**

**The prerequisites belong to no idea.** Extracting the inline JavaScript,
splitting the four single-file tools and splitting `project_files` are filed
under Idea 1 because that is where they surfaced. All three ideas need them,
and they would be worth doing if all three were cancelled. They are foundation,
not Idea 1.

**One large item is load-bearing for nothing.** Idea 1's Phase 4 — the
`http.server` → FastAPI/Flask migration — is required by none of the rest.
Idea 3 needs JSON routes and SSE push, and **both already exist**: the GLV
serves JSON handlers and `/events` on `http.server` today. Idea 2 needs
entry-point discovery, which involves no framework. Idea 1's frontend half can
take a hand-written OpenAPI document. That is ~3,600 lines of rewrite nothing
depends on, and it is **deferred indefinitely** (§7).

---

## 2. The parallelism rule

**Across repos, yes. Within a repo, no.**

Working `py61850`'s edit layer while pac-ct's JavaScript extraction proceeds is
safe: separate files, separate tests, separate releases, and switching between
them costs nothing. Working pac-ct's frontend toolchain and its plugin
discovery at the same time is not — when two architectural changes land
together and something breaks, the cause is ambiguous, and there is one
maintainer.

**The two "meets reality" phases are never concurrent.** Idea 3's first panes
and Idea 2's `vlan_mapper` extraction are each the moment their architecture
first meets a real consumer. They are sequenced, not overlapped.

---

## 3. The release cadence is a real schedule constraint

Publishing order is forced by the dependency graph: `py61850` → `sellib` →
`pac-ct`. A pac-ct change that uses a new library feature **cannot be pushed
until that library version is on PyPI**, because CI installs the pin and not
the sibling working tree.

So Track A's output has to be *released*, not merely written, before Track C
can consume it. Concretely: the edit layer ships as a `py61850` release at the
end of Stage 1, and Stage 3 pins it. Plan for that gap rather than discovering
it.

---

## 4. What "one session" means here

A session-sized phase is one that:

1. has a single stated goal, in one repo;
2. touches a bounded, nameable set of files;
3. **ends with the repo's full gate passing** — for pac-ct the `/check`
   routine (ruff, mypy, pytest, source-only bundle build); for `py61850`
   `unittest` plus `compileall` plus `twine check`;
4. is independently committable and reviewable as one branch;
5. leaves the tree working if the next session never happens.

If a phase below cannot meet 3 and 5, it is too big and should be split
further at the time it is picked up.

---

## 5. Stage 0 — Foundation

**Two tracks, concurrent. Nothing here is blocked by any open question.**

### Track A — `py61850` writes SCL faithfully  (Idea 3, Phase 1)

| | Goal | Ends when |
|---|---|---|
| **A1** | Round-trip test harness | A failing test loads every corpus SCD, exports with zero edits and compares. Written first; it *is* the specification |
| **A2** | Comment-preserving parse | `XMLParser(target=TreeBuilder(insert_comments=True))` in `SclDocument.parse`; the existing model still reads correctly with comment nodes present in the tree |
| **A3** | Namespace fidelity | `register_namespace` for known URIs, plus unused declarations re-emitted on the root from `_declared_namespaces()` — the function exists and has no consumer yet |
| **A4** | Public write API + release | `SclDocument.write()` / `.to_bytes()`, docstrings, `VERSION` bump, `v0.4.0` tag |

A2 carries the one real risk in the stage: comment nodes become children, so
anything iterating children must tolerate a non-string `tag`. `iter_local` and
`children_local` already filter by local name, which is why this is small.

### Track B — pac-ct foundation

**B1–B6: extract the inline JavaScript** (Idea 1, Phase 0). Smallest first,
so the pattern is established on a cheap file.

| | Tool | Inline JS | Note |
|---|---|---:|---|
| **B1** | `project_files` | 196 | **Also lands the `<script type="application/json" id="page-data">` mechanism** that replaces server-side substitution into script bodies. This is the session that defines the pattern |
| **B2** | `gle_exporter` + `vlan_mapper` | 241 + 355 | |
| **B3** | `vb_updater` | 502 | |
| **B4** | `gle_tabs` + `settings_compare` | 581 + 626 | |
| **B5** | `dnp_map` | 1,168 | |
| **B6** | `glv` | 3,160 | Mechanical extraction only, no restructuring. **Split into two sessions if the page-data extraction is not clean** |

**B7–B10: split the four single-file tools** (Idea 1, Phase 1). One each,
smallest first. `tests/test_web_routes_*.py` makes a bad split fail loudly.

| | Tool | Size |
|---|---|---:|
| **B7** | `vlan_mapper` | 12 KB |
| **B8** | `settings_compare` | 28 KB |
| **B9** | `gle_exporter` | 39 KB |
| **B10** | `vb_updater` | 67 KB |

**B11: split `project_files`** (baseline §5.3) — a `library` platform module,
and `/files` left as an ordinary tool. It is imported by all seven other tools
plus `mount.py` and `session.py`, so this is the session that makes the
platform boundary real.

**Stage 0 is roughly 15 sessions**, and every one of them stands on its own
merits even if the roadmap stops here.

---

## 6. Stage 1 — Long pole and contracts

**Two tracks, concurrent.** Track A is the bulk of the roadmap's effort.

### Track A — the edit layer  (Idea 3, Phase 2)

| | Goal |
|---|---|
| **A5** | Parent map, and the four primitives — `Insert`, `Remove`, `SetAttributes`, `SetTextContent` — each computing its inverse before applying |
| **A6** | Property test: `undo(apply(e))` restores the tree, over a generated edit sequence. Written with A5, not after |
| **A7** | Schema-ordering tables — resolving a correct insertion reference from the SCL content models |
| **A8** | **`tExtRef` — the eight capabilities. THE CALIBRATION** (see below) |
| **A9** | `tControl` — `updateConfRev`, `updateDatSet`, control-block removal, subscription lookup |
| **A10** | `tDataSet` + `tFCDA` |
| **A11** | `tGSEControl`, `tSMV`, `tGSE`, `tAddress` |
| **A12** | `tReportControl` + `tSampledValueControl` |
| **A13** | `tIED` — insert, remove, and update including the rename fan-out across every `ExtRef@iedName` |
| **A14** | `tLN` supervision — LGOS/LSVS instantiation and removal |
| **A15** | `tDataTypeTemplates` |
| **A16** | `tSubstation`, `tVoltageLevel`, `tBay` |
| **A17** | Generators — MAC address, APPID, LN instance, `uniqueElementName` |
| **A18** | Release `v0.5.0` |

**A8 is deliberately early and is the point of the ordering.** `tExtRef` is
self-contained, it is what Stage 4's Subscribe pane most needs, and its
finished size is the multiplier that turns the 3,000–5,000 line estimate into
a measurement. **Stop and re-estimate after A8.** If it comes in at three times
the guess, the shape of everything after Stage 1 changes and it is far better
to know then.

A9–A16 are independent of each other and may be reordered freely by whatever
Stage 3 turns out to need first.

### Track B — declare the contracts

| | Goal |
|---|---|
| **B12** | The HTTP contract (Idea 1, Phase 2): every route, its parameters, its response shape. No behaviour change; regularise the `{"error": …}` convention |
| **B13** | The platform API (Idea 2, Phase 1): the module list from baseline §3, promoted from habit to contract, with a test asserting no tool imports outside it |

These are the same kind of work — writing down what is already true, one
external and one internal — and are best done back to back.

---

## 7. Stages 2 to 5 — coarse, and gated

Deliberately not broken into sessions yet. Each is gated on a question in §8,
and the session split should be made when the stage is picked up, against what
is true then.

| Stage | Content | Rough size |
|---|---|---|
| **2** | Frontend toolchain (Idea 1, Phase 5), piloted on **one simple existing tool** — proving the offline-bundle and vendored-asset story before anything depends on it | 4–6 sessions |
| **3** | `pacct.scl_session` then the first two panes (Idea 3, Phases 3–4); **then** plugin discovery and the `vlan_mapper` extraction (Idea 2, Phases 2–4). **Sequential** | 10–14 sessions |
| **4** | The panes: IED lifecycle, Communication, Publish/Subscribe/Supervise, vendor subscribers (Idea 3, Phases 5–8) | large |
| **5** | Distribution: bundle format, catalogue, online install, the "Ferramentas disponíveis" screen (Idea 2, Phases 5–8) | 8–12 sessions |

**Stage 3's internal order is a genuine choice.** Idea 3 first is recommended:
its panes are ordinary tools that do not need the plugin system to exist, and
Idea 2's value rises with the number of installable tools, which Idea 3
multiplies by about seventeen. The counter-argument is real — extracting
`vlan_mapper` early is cheap and would surface contract problems before
seventeen panes are written against it. Invert the two if de-risking matters
more than building value.

### Deferred, possibly never

| Item | Why |
|---|---|
| **Idea 1 Phase 4** — backend framework | Nothing requires it. §1 |
| **Idea 1 Phase 3** — collapse the theme markup dialects | Only if it actually blocks a pane |
| **Idea 3 Phase 10** — SLD designer | Cuttable by design; nothing depends on it |

---

## 8. Which question gates which stage

Nothing below blocks Stages 0 or 1. That is why they are planned and the rest
are not.

| Stage | Must be answered first | Where |
|---|---|---|
| 0 | — nothing | |
| 1 | Should `py61850` adopt ruff/mypy before gaining 3–5k lines? *Soft — decide a few sessions in, not now* | `03/04` Q7 |
| 2 | Do the three themes keep separate markup? Building components against three dialects is different work from one | `01/04` Q2 |
| 3 | The sweep rule for a document with unexported edits; the resident-document limit; host API versioning; the plugin trust boundary | `03/04` Q2, Q3; `02/04` Q1, Q5 |
| 4 | Does the live-relay cross-reference land with Subscribe or after it? Needs a bench relay either way | `03/04` Q4; `BACKLOG.md` item 8 |
| 5 | First-party plugins only? Monorepo or N repos? | `02/04` Q1, Q2, Q6 |
| — | **IEC XSD/NSD licensing** — the only question needing an answer from outside this project | `03/04` Q1 |

---

## 9. If only three things are ever done

1. **A1–A4** — `py61850` round-trips SCL. Small, blocked by nothing, and it
   makes the library complete rather than half of one.
2. **B1–B6** — the JavaScript gets a home. Reversible, no decisions, and it
   unblocks everything else in the roadmap.
3. **A8** — `tExtRef`. It calibrates the largest estimate here and is exactly
   what the Subscribe pane will need.

Everything else is optional in a way those three are not.
