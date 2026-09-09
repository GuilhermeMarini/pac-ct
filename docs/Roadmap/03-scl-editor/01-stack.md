# Idea 3 — Stack

Five decisions, each already taken. This file records what was chosen, what it
was chosen over, and the measurement or constraint that settled it — so a
future reader can tell which are load-bearing and which could be revisited.

---

## 1. Where the document lives — **server-authoritative**

The XML tree lives in `py61850`, held in the pac-ct session. The browser
dispatches edits over HTTP and receives patches back.

| Option | Verdict |
|---|---|
| **Server-authoritative** | **Chosen** |
| Client-authoritative (OpenSCD's model) | Rejected |
| Hybrid — browser edits, server validates | Rejected |

**What settled it.** OpenSCD keeps the document client-side because it is
designed to run as a website, potentially against a remote server; a network
round trip per keystroke would be intolerable. pac-ct runs on the engineer's
own laptop. The measured loopback round trip in this application is **0.96 ms**,
and `/events` already pushes server state to the browser for the GLV. The
constraint that shaped OpenSCD's architecture is simply absent here.

**What client-side would have cost.** A second SCL implementation in
JavaScript. `sellib`'s `db:` sAddr grammar (178,406 DAIs carry one) and
`siemenslib`'s `Private` handling would each need a JavaScript twin before the
editor could show what they know. The workspace conventions record the end of
the `_xmlsafe.py` duplication for exactly this reason; re-creating it one layer
up would be worse, not better.

**What it costs.** Every OpenSCD plugin is written against a local synchronous
`XMLDocument`. Porting their interaction code means rewriting it to dispatch
and await. That makes this a reimplementation rather than a translation —
which is what was wanted anyway.

**The hybrid is the worst of the three** and is recorded here so it is not
rediscovered: two implementations *and* a synchronisation protocol between
them.

---

## 2. How it is structured — **a menu group, no container plugin**

The SCL tools become an ordinary group in the existing menu, beside
vendor-neutral, SEL, GE and Siemens.

**What settled it.** OpenSCD needs a container app because its document lives
in the browser page's memory — leave the page, lose the document, so every
editor must be a tab in one page. Server-side, the working document lives in
the session and outlives any page. Any tool in any tab can attach to it. **The
session document is the shared context**, so there is nothing for a container
to contain.

**The machinery already exists.** `themes/items.py` defines `GROUPS`,
`GROUP_ORDER`, and every `Tool` declares a required `grupo`. Adding a group is
a data change.

**Consequence for Idea 2:** each SCL tool stays separately installable. A user
can install Subscribe without the SLD designer. The plugin contract in
`../02-tool-plugins/02-specification.md` needs no change — it only gains a
document-session service in the platform API.

---

## 3. Round-trip fidelity — **hardened `ElementTree`**

`ET` is kept, with three hardening measures and no new dependency.

**Why this matters.** The SCD goes back to DIGSI and SEL Architect. If
`py61850` reformats the 99 % of the file it did not touch, every save produces
a whole-file diff and the tool becomes hostile to the workflow it is meant to
serve. `sellib` already holds itself to `parse(b).serialize() == b` for
`SET_D`; this is the same standard applied to XML.

**Measured on Python 3.12, on a synthetic SCL fragment:**

| Property | Plain `ET` | Hardened |
|---|---|---|
| XML comments | dropped | **kept** — `XMLParser(target=TreeBuilder(insert_comments=True))` |
| Indentation of untouched regions | kept | kept — it lives in `.text` / `.tail` |
| Attribute order | kept | kept — dicts are ordered on 3.7+ |
| Namespace prefixes | rewritten `ns0:` | **kept** — `ET.register_namespace()` |
| `sxy:x="3"` on a used prefix | kept | kept |
| **Unused** `xmlns:` declaration | **dropped** | **kept** — see below |
| Attribute quote style | `'` → `"` | cosmetic, unchanged |
| Empty-element spacing | `<X/>` → `<X />` | cosmetic, unchanged |

**The one real gap and its fix.** A namespace declared at the root but used
nowhere is dropped on serialisation. That is not academic: SEL's private
namespace is exactly that shape, and losing an `sxy:` declaration would
endanger SLD coordinates that both DIGSI and SEL Architect read. It is closed
by re-emitting the declaration as a literal attribute on the root
(`root.set("xmlns:sel", uri)`), driven by the list that
**`_declared_namespaces()` in `document.py` already collects** — the function
exists, it simply has no consumer yet.

Verified: with that set, `xmlns:sel` is present in the output and `sxy:x="3"`
survives intact.

**What was rejected.**

- **`lxml`** — preserves everything faithfully and provides real parent
  pointers, which `ElementTree` lacks entirely and which `Remove{node}` needs.
  Rejected because it is a compiled dependency, and `py61850` having **no
  dependencies at all** is part of its identity and of the offline-bundle
  story. The parent-pointer problem is solved instead by a parent map built
  per document (§ `02-specification.md`).
- **A full byte-preserving serialiser** — keep original source text for every
  untouched subtree. Strictly better fidelity; by far the most work, and the
  fiddliest code in the roadmap. Not ruled out forever: if the cosmetic
  differences turn out to matter to DIGSI or SEL Architect in practice, this is
  the escalation path.
- **Accepting reformatting** — rejected outright once the round trip to vendor
  tools was confirmed.

---

## 4. The frontend — **rebuilt, to functional fidelity**

OpenSCD's interface is Lit plus Material Web Components. It is **not** ported.
Its screens are used as a functional specification; the interface is rebuilt
against this project's own themes.

**What settled it.** Three reasons, in order of weight.

1. **The theme system makes porting more expensive than rebuilding.** The three
   directions emit *different markup*, and the project conventions forbid a
   tool defining its own colours, radii, font stacks or paddings. Material Web
   Components arrive with all of that, behind shadow DOM. Porting would mean
   either abandoning the theme system for the SCL tools — a fourth visual
   language in one application — or fighting mwc's styling at every component.

2. **The cross-referencing advantage argues against their layout.** OpenSCD
   only holds the SCD. Here the session also holds the RDB, the GLE diagrams
   and a live relay. A Subscribe pane that shows whether the `ExtRef` being
   bound is toggling *on the relay right now* has no equivalent in their
   design, and their pane layout has no place to put it.

3. **It removes the derivative-work question entirely.** See §5.

**The risk this creates, and the mitigation.** "Rebuild but keep the
functionality" is the kind of goal that ships at 60 % with the missing 40 %
discovered in a substation. The mitigation is a **one-page functional spec per
pane, written before any code** — what it lists, what it edits, which edit
primitives it emits, what it validates, what it refuses. Those specs are
`06-plugin-port-list.md`, and they are the deliverable that makes "fidelity"
checkable instead of aspirational.

**This settles an open question in Idea 1.** `../01-frontend-backend-split/04-open-questions.md`
Q1 asked how much appetite there is for maintaining the private 3,050-line
framework. An SLD designer with drag-and-drop, a subscription binder and a
DataTypeTemplates tree browser cannot reasonably be built in inline `<script>`
blocks — the GLV's 3,160 lines are the existing warning. **Idea 3 requires
Idea 1's B3, or B2 at the absolute minimum.** That converts a matter of taste
into a requirement with a reason attached.

---

## 5. Legal posture — **clean reimplementation throughout**

Nothing is copied. The architecture, the API shapes and the domain knowledge
are used as reference; all code is written fresh.

**What this means in practice.** Apache-2.0 is one-way compatible with
AGPL-3.0-or-later, so a derivative port *would* have been permitted — it would
simply have obliged this project to carry Apache-2.0 notices and ship a NOTICE
entry. Rebuilding the interface (§4) removed the last component that would
have been a translation rather than a reimplementation, so **no derivative work
remains anywhere** and no attribution obligation attaches.

**Crediting OpenSCD, OpenEnergyTools and the plugin authors in the
documentation remains the right thing to do.** It is now a courtesy rather than
a licence term, and this roadmap does it by name.

**One practical rule follows.** Reimplementing from concepts means being
deliberate about not transcribing. Working from the *published behaviour* — the
Edit API shapes, the documented plugin list, what each screen does — is the
intended path; lifting function bodies is not, even though the licence would
allow it, because the posture chosen here is stricter than the licence.

**A constraint that survives all of this:** the IEC NSD and XSD files are
under IEC copyright, not Apache-2.0, and OpenSCD itself carries a CC-EULA
disclaimer on some files. That limits what schema and namespace data can ship,
regardless of how the code is written. See `04-open-questions.md` Q1.
