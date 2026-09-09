# Idea 3 — A live SCL editor, reimplemented from OpenSCD

**Status: architecture decided, scope not. Blocked on nothing; gated on Idea 1
Phase 0 and a frontend framework.**

---

## What was asked

> "There's an open source tool to edit SCL files that would fit perfectly in my
> commissioning toolkit […] I don't want to copy it or use it as it is. I want
> to use this software as a reference and reimplement its concepts,
> architecture and know-how within my own software, adapting everything to my
> software's architecture and requirements."

The tool is [`transpower-nz/open-scd`](https://github.com/transpower-nz/open-scd),
Apache-2.0.

---

## What that tool actually is

Not a fork — a **distribution**. It packages `open-scd-core`
(OpenEnergyTools) as an Electron application and pulls in **32 plugins as 41
git submodules** from six organisations: OpenEnergyTools, danyill, sprinteins,
meinberg-sync, OMICRON and Transpower itself. The upstream `openscd/open-scd`
was **archived on 2026-01-12** and moved to LF Energy CoMPAS, so the living
architecture is the core plus the plugin ecosystem, not the old monolith.

**The core is tiny.** It holds `XMLDocument`s and dispatches edits. Everything
else — every editor, every wizard, every validator — is a plugin.

Three things are worth taking, and they are separable:

1. **The edit model** — four declarative primitives, an invertible history,
   and plugins that never touch the document directly.
2. **The domain know-how** — `scl-lib`, ~85 modules of IEC 61850-6 rules about
   what may be created where and what an edit must drag along with it.
3. **The screens** — what each pane shows and what it lets an engineer do.

Only the third is a user interface. The first two are library concerns, and
in this project they belong to `py61850`.

---

## The finding that makes this cheap

`py61850.scl` is **1,695 lines and entirely read-only** — no write, no
serialise, no mutation anywhere in the package.

But its model is already the right shape. Every class holds `self.element = el`
over a live `ElementTree` node, declares `__slots__`, and reads lazily through
`el.get()`. `SclDocument.root` is the real ET root. It is a **view over a live
XML tree** — architecturally identical to OpenSCD's model over an
`XMLDocument`.

So this is not a foreign architecture being grafted on. It is **the missing
half of one that already exists**, and OpenSCD's four edit primitives map
essentially one-to-one onto `ElementTree` operations.

---

## Decisions already taken

Each of these was argued before it was chosen; the reasoning is in the file
named beside it.

| | Decision | Where the reasoning is |
|---|---|---|
| **Document location** | **Server-authoritative** — the XML tree lives in `py61850`, in the session. The browser sends edits and receives patches. | `01-stack.md` §1 |
| **Structure** | **A "Ferramentas SCL" menu group**, not a container plugin with sub-plugins. | `01-stack.md` §2 |
| **Scope** | **SCL/XML only.** RDB editing stays batch-diff exactly as it is today. | `02-specification.md` §6 |
| **Undo** | **Global per document**, not per tool. | `02-specification.md` §4 |
| **Durability** | **The edit journal is persisted to disk.** | `02-specification.md` §5 |
| **Round-trip** | **Hardened `ElementTree`** — zero new dependencies. | `01-stack.md` §3 |
| **Frontend** | **Rebuilt** against the existing themes, to functional fidelity. | `01-stack.md` §4 |
| **Legal posture** | **Clean reimplementation throughout.** No derivative work. | `01-stack.md` §5 |
| **Overlapping tools** | **Kept separate for now**; merge only on observed overlap. | `04-plugin-port-list.md` §3 |

### Why server-authoritative was not a close call

OpenSCD keeps the document in the browser because it is built to run as a
website, possibly against a remote server — client-side editing avoids a
network round trip per keystroke. **That constraint does not exist here.**
pac-ct runs on the engineer's own laptop, and the measured loopback round trip
is **0.96 ms**. The thing that justified their design costs nothing here.

What client-side *would* cost is a **second SCL implementation, in
JavaScript** — and then `sellib`'s `db:` sAddr grammar and `siemenslib`'s
`Private` handling would need JavaScript twins to be usable inside the editor.
That is precisely the duplication the workspace rules exist to prevent.

### Why no container plugin

The reason OpenSCD *needs* a container app with sub-plugins is that its
document lives in the browser page's memory: leave the page, lose the
document. Every editor is therefore forced to be a tab inside one page.

Server-side, that constraint evaporates. The working document lives in the
session and outlives any page, so **any tool in any browser tab can attach to
it**. There is nothing for a container to contain — the session document *is*
the shared context.

`themes/items.py` already defines `GROUPS`, `GROUP_ORDER` and a required
`grupo` on every `Tool`. A new menu group is a **data change**, not new
architecture.

---

## The advantage that argues against porting

OpenSCD only ever holds the SCD. In one pac-ct session there is the SCD, **the
RDB beside it**, the GLE diagrams, and **a live relay** over MMS or telnet.

A rebuilt Subscribe pane can show whether the `ExtRef` being bound is actually
toggling on the relay right now. A rebuilt Communication pane can check a
configured GOOSE address against what the relay is really publishing. Neither
is possible in OpenSCD, because there is no relay in its world.

That is not "their tool plus features". It is a different tool that happens to
share a data model — and porting their interface would obstruct it.

---

## What this is not

- **Not a fork, a vendoring, or a bundled copy** of OpenSCD.
- **Not a second SCL reader.** `py61850.scl` stays the only one.
- **Not an RDB editor.** RDB work is unchanged.
- **Not a general SCL editor for anyone.** It is a commissioning tool that
  edits SCL, which is why it is allowed to be opinionated where OpenSCD cannot.

---

## Read next

The first four files match Ideas 1 and 2 so the three can be read side by side.
The last two are specific to this idea.

- [`01-stack.md`](01-stack.md) — the technology decisions and their reasoning
- [`02-specification.md`](02-specification.md) — the target end state
- [`03-phases.md`](03-phases.md) — the ordered route
- [`04-open-questions.md`](04-open-questions.md) — what is genuinely undecided
- [`05-py61850-gap.md`](05-py61850-gap.md) — what `py61850` has, lacks, and gains
- [`06-plugin-port-list.md`](06-plugin-port-list.md) — the 32 plugins, triaged
