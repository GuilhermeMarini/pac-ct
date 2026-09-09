# Idea 3 — The 32 plugins, triaged

`transpower-nz/open-scd` ships 32 plugins (17 menu, 15 editor), all active by
default, drawn from 41 submodules across six organisations. This triages them
and defines the functional-spec format that makes "rebuild to functional
fidelity" checkable.

Credit where it is due: OpenEnergyTools, `danyill`, `sprinteins`,
`meinberg-sync`, OMICRON and Transpower wrote the tools this is learning from.

---

## 1. Triage

### Skip — already solved here, or out of scope (8)

| Plugin | Why not |
|---|---|
| Open, Save, Save As | The project library and `derived.adopt()` already do this, better — content-addressed and deduplicated |
| Save Snapshot, Automatic Backup | The persisted edit journal covers both, and gives crash recovery besides |
| Start (landing) | pac-ct has a home screen and a menu |
| Subscribe – NR | NR relays are not in this project's scope |
| *(104 / IEC 60870-5-104, in the wider ecosystem)* | Different protocol |

### Port — the substance (17)

Ordered by build sequence, not by importance.

| # | Plugin | Becomes | Notes |
|---:|---|---|---|
| 1 | Substation | Substation browser/editor | Simplest real pane; establishes the pattern |
| 2 | Describe | `desc` editor | Trivially useful, touches every element type |
| 3 | Edit IED | IED editor | |
| 4 | Rename IEDs | Rename with fan-out | **High value** — the rename must follow every `ExtRef@iedName` |
| 5 | Remove IEDs | Remove with cleanup | Must also remove subscriptions and supervisions |
| 6 | Add IED / Add IEDs | Import ICD/IID/CID | Single and batch |
| 7 | Communicate + Edit Communication + Explore Communication | **One** Communication pane | Three plugins, one job here — see §3 |
| 8 | Publish | Control blocks + DataSets | Core: GSE/SMV/Report creation and update |
| 9 | Subscribe (Later Binding) | Subscription binder | Core; the pane that most benefits from live relay data |
| 10 | Supervise | LGOS/LSVS supervision | Follows directly from Subscribe |
| 11 | Address Multicast (TP) | MAC/APPID/VLAN allocation | Overlaps VLAN Mapper — see §3 |
| 12 | Network (TP) | Network configuration | Overlaps VLAN Mapper — see §3 |
| 13 | Validate | Schema + semantic validation | **Constrained by XSD licensing** — `04-open-questions.md` Q1 |
| 14 | Compare | Document diff | Distinct from Settings Compare — see §3 |
| 15 | Add Template IEDs | Template instantiation | Later |
| 16 | Extract IED | Export one IED as ICD | Later |
| 17 | Add Network Data | Bulk network import | Later |
| — | Design SLD | Single-line diagram editor | **Last.** See §4 |
| — | Stencil | SLD templates | After the SLD |

### To the vendor libraries (2)

| Plugin | Target |
|---|---|
| Subscribe – SEL | **`sellib`** — and directly relevant to this project's domain |
| Subscribe – Siemens | **`siemenslib`** |

### Not a plugin — a concept (1)

**Wizarding** (`scl-wizarding`) is OpenSCD's declarative dialog framework, not
a feature. Its idea — that editing an element is described as a form which
*emits edits* rather than mutating anything — is worth taking, and it belongs
in the frontend as shared machinery, not as a pane.

---

## 2. The functional-spec format

**Rebuilding to "functional fidelity" fails silently unless the functionality
is written down first.** That is the main risk this idea carries: a pane ships
at 60 %, and the missing 40 % is discovered in a substation.

So each pane gets **one page, written before its code**, in this shape:

```
# Pane: <name>

Replaces:      <the OpenSCD plugin(s) it learns from>
Document:      <read-only | edits>
Depends on:    <py61850 capabilities, other panes>

## Lists
   what the pane shows, and where each field comes from in the model

## Edits
   every edit it can emit, as one of the four primitives or a named compound
   — each with the history title the user will see

## Validates
   what it checks before emitting

## Refuses
   what it will not do, and what it says instead

## Cross-references            ← the part with no OpenSCD equivalent
   what it shows from the RDB, the GLE, or a live relay

## Done when
   the checklist that makes fidelity verifiable
```

The **Cross-references** section is the one that has no counterpart upstream,
and it is where this stops being a port. OpenSCD only ever holds the SCD; a
pac-ct session also holds the RDB, the GLE diagrams and a live relay.

### Exemplar — Pane: Subscribe

```
Replaces:   oscd-subscriber-later-binding
Document:   edits
Depends on: py61850 tExtRef capabilities; Publish pane for the source side

Lists       ExtRef inputs per IED/LD/LN, each showing bound/unbound state,
            the source FCDA when bound, and why it is ineligible when it is

Edits       subscribe(extRef, fcda)     "Assinar ExtRef"      — compound
            unsubscribe(extRef)         "Remover assinatura"  — compound
            Both are one history entry, not the six primitives they expand to

Validates   the FCDA meets the ExtRef's type restrictions; the source control
            block exists; the subscription does not already exist

Refuses     binding across incompatible CDCs, and says which two types clashed

Cross-refs  whether the bound bit is toggling ON THE RELAY RIGHT NOW, via the
            existing MMS/telnet transport — impossible in OpenSCD
            and, for SEL IEDs, the Relay Word bit behind the ExtRef, resolved
            through sellib's db: sAddr grammar

Done when   a corpus SCD round-trips with 20 subscriptions added and removed,
            and DIGSI accepts the result
```

### Exemplar — Pane: Substation

```
Replaces:   scl-substation-editor
Document:   edits
Depends on: py61850 tSubstation/tVoltageLevel/tBay capabilities

Lists       the Substation → VoltageLevel → Bay → ConductingEquipment tree,
            with LNode bindings per element

Edits       create/rename/remove at each level  — SetAttributes and Insert
            with schema-ordered insertion resolved by py61850

Validates   name uniqueness within the parent

Refuses     removing an element that LNodes still reference, naming the
            references rather than cascading silently

Cross-refs  which bays correspond to relays present in the project's RDB

Done when   the pane can build a two-bay substation from empty, and the
            result validates
```

Two exemplars are enough to fix the format. The remaining specs are written at
the head of the phase that builds them — writing all seventeen now would be
guessing at panes whose shape the first three will teach us.

---

## 3. Overlap with tools that already ship

**Decision: keep them separate for now.** Merge only on overlap observed in
use, not predicted on paper. Recorded here so the question is not
re-litigated, and so the eventual merge has a starting point.

| Existing tool | Overlapping plugins | The real relationship |
|---|---|---|
| **VLAN Mapper** (323 lines) | Address Multicast (TP), Network (TP) | Genuine overlap. VLAN Mapper *derives* GOOSE VLAN port maps from an SCD; the TP plugins *allocate and edit* multicast MAC/APPID/VLAN. Read versus write over the same `Communication` data. The most likely eventual merge |
| **VB Updater** (1,610 lines) | Subscribe (Later Binding), Subscribe – SEL | Adjacent, not identical. VB Updater syncs Virtual Bit *descriptions* between GLE and SCD; the subscribers manage `ExtRef` *bindings*. They meet at the same elements from different directions |
| **Settings Compare** (754 lines) | Compare (`oscd-diff`) | **Not the same problem.** Settings Compare does SELOGIC *equivalence* across up to seven relays of one family; `oscd-diff` compares SCL documents *structurally*. Both are worth having |

The thing to watch: once the SCL tools can edit `Communication`, two tools can
change the same addresses. The live-document model makes that safe — one
document, one history — which is a reason the merge is less urgent than it
would otherwise be.

---

## 4. Why the SLD comes last

`oscd-designer` is the most sophisticated plugin in the set: drag-and-drop
placement, routing, and `sxy:` coordinate management across a document that
several other panes are editing at the same time.

Three reasons it is last:

1. It is where a component framework matters most, and it should be built on a
   frontend pattern already proven by simpler panes.
2. Its edits are dense and continuous — dragging emits a stream — so it is the
   hardest test of edit squashing and of the push channel. Better to meet that
   with the transport already working.
3. `sxy:` coordinates are read by DIGSI and SEL Architect, so it is also the
   pane with the sharpest round-trip fidelity requirement. That requirement is
   verified by Phase 1 long before this pane exists.

**It is also the pane most likely to be cut.** Nothing else in this idea
depends on it, the commissioning value of the other sixteen does not rest on
it, and a single-line diagram editor is a large piece of work whose absence
leaves a perfectly useful SCL editor behind.
