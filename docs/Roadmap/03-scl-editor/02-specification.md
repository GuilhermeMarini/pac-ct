# Idea 3 — Specification of the end state

What the application looks like when this is done. Written so it can be built
against, and so partial completion is recognisable.

---

## 1. Layering

```
┌──────────────────────────────────────────────────────────────┐
│  SCL tool panes          one pac-ct tool each, in the         │
│                          "Ferramentas SCL" menu group         │
├──────────────────────────────────────────────────────────────┤
│  Document session        working documents, edit journal,     │
│  pacct.scl_session       undo/redo, push. Platform, not a tool │
├──────────────────────────────────────────────────────────────┤
│  py61850.scl             the model, the edit primitives,      │
│                          the 61850-6 rules, serialisation     │
├──────────────────────────────────────────────────────────────┤
│  sellib / siemenslib     the vendor halves, unchanged         │
└──────────────────────────────────────────────────────────────┘
```

**The rule that keeps it honest:** `py61850` knows nothing about sessions,
HTTP, journals or undo. It offers a document, a model over it, edit primitives
and the rules governing them. Everything stateful is pac-ct's.

---

## 2. The edit primitives, in `py61850`

Four operations, reimplemented from OpenSCD's published Edit API against
`ElementTree`. They are the only sanctioned way to change an SCL document.

| Primitive | Carries | Inverse |
|---|---|---|
| `Insert` | parent, node, reference (next sibling, or `None` to append) | `Remove(node)` |
| `Remove` | node | `Insert(parent, node, former next sibling)` |
| `SetAttributes` | element, mapping of name → value or `None` to delete | `SetAttributes` with the previous values |
| `SetTextContent` | element, text | `SetTextContent` with the previous text |

A list of edits is itself an edit, applied in order and inverted in reverse —
which is what makes a compound operation like "subscribe this ExtRef" a single
undo step.

**Every primitive is invertible before it is applied.** The inverse is computed
from the tree as it stands, and stored with the edit. That is what makes the
history exact rather than a re-derivation.

### Two things ElementTree does not give for free

1. **No parent pointers.** `Remove{node}` needs the parent, and `ET` has none.
   A parent map is built per document and maintained by the edit applier
   itself — it is the applier that changes the tree, so it is the only thing
   that has to keep the map in step.

2. **No schema-ordered insertion.** SCL element content models are XSD
   `sequence`s: a child inserted in the wrong position produces a document that
   fails validation in tools that check it. `py61850` therefore owns the
   ordering tables and resolves the correct insertion reference itself. This is
   the single most 61850-specific thing in the whole idea, and it is exactly
   the kind of knowledge the vendor-neutral library should hold.

---

## 3. Working documents and the content-addressing problem

**The collision.** `FileLibrary` is keyed by the **sha256 of content** — the
library's own docstring says *"two uploads of the same bytes are the same
file, so the key is the content"*, and files are stored at `<sha12>.scd`. A
live-edited document changes its hash on **every edit**. A working document
therefore cannot be a library entry as they exist today: it needs a stable
identity that is not its content.

**The model:**

```
uploaded SCD            immutable, keyed by sha256          ← unchanged
      │
      │  first edit — copy on write
      ▼
working document        stable id, mutable, edit journal,
                        baseline = the sha256 it forked from
      │
      │  "Exportar"
      ▼
new immutable file      sha256 → derived.adopt() → library  ← already built
```

The last step needs nothing new. `derived.adopt()` already implements "a tool's
output enters the library by the same rules an upload does", and it already
checks for duplicates *before* writing, so exporting an unchanged document
returns the original entry instead of creating a twin.

**Rules:**

1. A working document is created **lazily, on the first edit**. Selecting a
   file to look at creates nothing.
2. Its identity is a session-scoped id, never a content hash.
3. It records the `baseline` sha256 it forked from, so "what changed since
   upload" is always answerable.
4. Several working documents may exist at once, including several forked from
   the same baseline.
5. **Selection is explicit per tool.** There is no implicit "active document".
   This matches the existing picker and degrades sanely when two SCDs are open.
6. The picker shows both kinds, and a working document is visibly marked —
   name, baseline, and an edit count.

**Uploading another SCD** does nothing special: a new file, a new entry, its
own working document on first edit.

**Changing a tool's selection mid-flight** is safe by construction. The working
document lives in the session, not in the tool's view of it. The tool detaches;
the document is untouched. Nothing to save, nothing to prompt. This is the case
that would be painful client-side and is free server-side.

---

## 4. Undo and redo — global per document

**One history per working document, shared by every tool attached to it.**

If the VB Updater and Subscribe are both attached, an undo in one can revert an
edit made in the other. That is correct: there is one document. It is also
surprising, so the history entry carries a **title** and the interface always
names what it is about to undo ("Desfazer: assinar ExtRef") rather than
offering a bare arrow.

Per-tool undo was rejected. It feels safer and is wrong — it would let two
tools hold contradictory ideas of one document's history.

**Squash.** Consecutive edits of the same kind on the same element — dragging a
slider, typing into a field — merge into one history entry rather than
producing forty. The client marks an edit as squashable; the server decides.

---

## 5. The edit journal — persisted

**Every edit is appended to a journal on disk, under the session's own
directory.**

**Why this is not optional.** `DEFAULT_TTL_SECONDS` is `8 * 3600`, the sweeper
runs every 900 s, and expiry `rmtree`s the session directory. Today that is
survivable: edits are per-tool diffs, re-derivable from a file that still
exists. With a live document **the edits are the work product**. An engineer
with six hours of subscription work who breaks for lunch would return to
nothing.

**What it buys, beyond not losing work:**

- **Crash recovery.** A working document is `baseline + journal`; both are on
  disk, so a restart replays rather than loses.
- **A memory bound.** A document whose tree is evicted can be rebuilt from
  baseline plus journal, which makes an LRU over parsed trees safe.
- **An audit trail.** *"What did I change in this SCD?"* is a question
  commissioning actually asks, and the journal answers it exactly.

The journal is small — four primitives with element paths and values — so this
costs disk that is already dominated by a 40–140 MB RDB cache.

**Expiry policy:** a session with unexported edits is not swept on the ordinary
TTL. The rule that replaces it, and where the ceiling sits, is
`04-open-questions.md` Q3.

---

## 6. Scope — SCL only

**Live documents are XML/SCL. RDB editing is unchanged.**

Measured: **six** modules write RDBs through `rdb_write` (`dnp_map/export.py`,
`gle_tabs/{model,handler,export}.py`, `gle_exporter/__init__.py`,
`vb_updater/__init__.py`), while **ten** modules read SCDs through `py61850`.

An RDB is a 40–140 MB Compound File under a `parse(b).serialize() == b`
contract. Holding one live per session is a different and much larger problem
than an XML tree, and nothing in this idea requires it.

The two coexist without interference: a tool may hold a live SCD **and** a
pending RDB diff at the same time, which is precisely what the VB Updater does.

---

## 7. What the platform gains

A new platform module, `pacct.scl_session`, joining the set in
`../00-baseline.md` §3. It is **platform, not a tool** — by the same test that
makes `project_files` platform: several tools depend on it and it owns no
screen of its own.

It provides: create/open a working document, apply an edit, undo, redo, the
current `editCount`, a change subscription, and export.

**Consequence for Idea 2:** the plugin contract in
`../02-tool-plugins/02-specification.md` §2 gains one row. Nothing else about
it changes — the SCL tools are ordinary tools that happen to share a service.

---

## 8. The HTTP contract

| Route | Purpose |
|---|---|
| `POST /scl/doc` | fork a working document from a library file |
| `GET /scl/doc/<id>` | metadata: baseline, name, `editCount`, dirty |
| `POST /scl/doc/<id>/edit` | apply an edit; body carries the client's known `editCount` |
| `POST /scl/doc/<id>/undo` \| `/redo` | move through the history |
| `GET /scl/doc/<id>/history` | the journal, titled, for display |
| `POST /scl/doc/<id>/export` | serialise → `derived.adopt()` → library |
| `GET /scl/doc/<id>/events` | push: `editCount` changed |

**Concurrency is optimistic.** The client sends the `editCount` it believes is
current; a stale value is refused with the current one, and the client
refreshes rather than silently overwriting. `Session.lock` (already an
`RLock`) serialises application.

**Push reuses what exists.** The GLV's `/events` already parks a thread on
`LiveState.version` via `wait_for_change` and writes a frame when the value
moves. `editCount` is the same pattern with a different counter, and the
existing rule holds: **infrastructure routes never create a session.**

---

## 9. What must survive

Acceptance criteria. Each exists because of something already recorded in
`docs/ENGINEERING-NOTES.md`.

1. **Round-trip fidelity.** Loading and exporting a document with **zero
   edits** produces a file semantically identical to the input: comments
   present, namespace declarations present including unused ones, prefixes
   unchanged, `sxy:` coordinates intact, untouched indentation preserved.
   This is the single most important test in the idea.
2. **`derived.adopt()`'s duplicate rule.** Exporting an unchanged document
   returns the existing library entry; it does not create a second one, and it
   never deletes the file the library points at.
3. **Explicit selection.** No tool acquires a document it was not pointed at.
4. **A failed edit changes nothing.** Rejected edits — schema-order violations,
   61850 constraint failures, stale `editCount` — leave the tree exactly as it
   was. Partial application is a bug, in the same way a half-applied RDB write
   is.
5. **No session is created by an infrastructure route.**
6. **A working document with unexported edits is not swept.**
7. **The GLV's single shared relay session is untouched** by any of this.

---

## 10. Recognising completion

- [ ] A zero-edit load-and-export is byte-comparable on the corpus SCDs, with
      only the documented cosmetic differences
- [ ] Every edit's inverse is computed before application, and a
      property-based test asserts `undo(apply(e)) == identity` over a
      generated edit sequence
- [ ] Schema-ordered insertion is table-driven in `py61850` and tested against
      the SCL content models
- [ ] Crash recovery: kill the process mid-session, restart, and the working
      document rebuilds from baseline plus journal
- [ ] Two tools attached to one document both observe an edit made in either
- [ ] A stale `editCount` is refused rather than applied
- [ ] `mypy` clean, and `py61850`'s gate (`unittest`, `compileall`,
      `twine check`) still passes with no new dependency in its metadata
