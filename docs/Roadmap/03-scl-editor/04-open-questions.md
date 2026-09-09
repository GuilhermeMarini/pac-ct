# Idea 3 — Open questions

The architecture is decided (`README.md`). These are what remain, ordered by
how much else depends on the answer.

---

## Q1. Can the IEC schema and namespace data ship at all?

**This is a licensing question, not a technical one, and it is the largest
open item in the idea.**

OpenSCD's validation and template features rest on two bodies of IEC data:

- the **SCL XSD schemas** (IEC 61850-6), against which a document is checked;
- the **NSD / NSDoc files** (IEC 61850-7-3, 7-4) describing Logical Node
  classes and Common Data Classes. `scl-lib` carries **200 generated JSON
  fixtures** derived from them.

**These are IEC copyright, not Apache-2.0.** OpenSCD itself carries a CC-EULA
disclaimer on some files, which is the tell. Being permitted to reimplement
their *code* says nothing about being permitted to redistribute IEC's *data*.

Three shapes, and the answer changes what Phase 9 can be:

1. **Ship nothing; the user supplies the files.** A configuration pointing at
   an XSD/NSD set the engineer already licenses. Safe, and there is precedent
   in this project — "Importar perfil DNP" already takes user-supplied
   reference data into the packaged `wordbits/`.
2. **Ship only what is demonstrably free.** Some 61850 material is published
   openly; the boundary has to be established rather than assumed.
3. **Skip schema validation; do semantic checks only.** Constraints expressible
   in code — name uniqueness, reference integrity, subscription type
   compatibility — need no IEC data at all, and are arguably the more useful
   half for commissioning.

**Recommendation: 3 as the default, with 1 available.** Semantic validation is
what catches the errors that matter on site, and it carries no legal question.

**This needs an actual answer before Phase 9**, and it is worth getting one
from someone qualified rather than inferring it from what other projects do.

---

## Q2. What is the sweep rule for a document with unexported edits?

`02-specification.md` §5 says such a session is not swept on the ordinary
8-hour TTL. That states the intent, not the rule.

The tension: an engineer must not lose six hours of subscription work to a
lunch break, and the sweeper must not become a thing that never sweeps —
`cache/` already reached 1.3 GB on the development machine.

Candidates:

- A longer TTL when a working document is dirty, with the journal on disk as
  the safety net either way.
- Never expire a dirty document, but expire its *parsed tree* — the journal is
  what makes that safe, and it is already required.
- Warn in the interface at the point where the session would otherwise be
  swept, since the engineer is usually still at the machine.

**Turns on:** how long a real editing session lasts, which nobody has
measured. The second option is probably right because it costs memory rather
than data.

---

## Q3. How many working documents may be resident at once?

A 22 MB SCD parses in **682 ms** (measured) and occupies several times its
file size as an `ElementTree`. Multiply by working documents per session and
sessions per process.

The journal makes an LRU over parsed trees safe — evict, reload from
`baseline + journal` on next touch. What is undecided is the ceiling and
whether it is per session or per process. `[web]` config already carries
`glv_max_links` and `glv_max_diagrams` as precedent for exactly this kind of
limit.

**Turns on:** measurement that has not been taken. Worth taking during
Phase 3.

---

## Q4. Does the live-relay cross-reference land in Phase 7, or later?

Showing whether a bound `ExtRef` is toggling on the relay right now is the
capability OpenSCD structurally cannot have, and `01-stack.md` §4 uses it as a
reason to rebuild rather than port.

But it couples the Subscribe pane to the GLV's transport layer, `LinkPool` and
relay lifecycle — the most delicate code in pac-ct, and the part that **cannot
be tested without a bench relay**.

- **In Phase 7:** the pane is designed around it from the start, and the layout
  has somewhere to put it.
- **After Phase 7:** Subscribe ships sooner and the coupling is added once the
  pane is proven.

**Turns on:** bench relay availability, which is already a recorded constraint
(`BACKLOG.md` item 8, Gate 7).

---

## Q5. Is the SLD designer in scope at all?

`06-plugin-port-list.md` §4 puts it last and calls it the most likely
casualty. That is a recommendation, not a decision.

**For:** it is the pane that makes the tool feel complete, and `sxy:`
coordinates are already in the standard and therefore already `py61850`'s
business.

**Against:** it is large, nothing depends on it, and sixteen useful panes exist
without it. A commissioning engineer needs subscriptions correct far more than
they need to draw a diagram.

**Turns on:** whether anyone actually authors single-line diagrams here, or
only reads ones that DIGSI and SEL Architect produced.

---

## Q6. When do VLAN Mapper and the TP plugins merge?

`06-plugin-port-list.md` §3 records "keep separate for now" as the decision.
The merge question returns once Address Multicast and Network land in Phase 5's
neighbourhood, because at that point two tools edit the same `Communication`
data.

The live-document model makes concurrent editing *safe* — one document, one
history — which lowers the urgency but does not answer it. Two tools that both
allocate APPIDs with different policies is a correctness question, not a
concurrency one.

**Turns on:** whether VLAN Mapper's derivation and the TP plugins' allocation
turn out to be the same operation viewed from two ends, or genuinely different
jobs.

---

## Q7. Does `py61850` need `ruff` and `mypy` once it triples in size?

The project conventions record `py61850` as the deliberate exception: no ruff
configuration, no mypy, its CI gate being `unittest` plus `compileall` plus
`twine check`. That is a reasonable choice for a 9k-line library.

Phase 2 plausibly adds 3,000–5,000 lines of intricate, rule-heavy code — the
kind where a type checker earns its keep, and where `pac-ct`'s experience
(`mypy` clean across all of `src/pacct`, with exactly two `type: ignore`) is
the counter-example.

**Turns on:** how the Phase 2 code actually reads once some of it exists.
Worth revisiting at the midpoint of Phase 2 rather than deciding now in either
direction.

---

## Q8. Is the estimate for Phase 2 anywhere near right?

3,000–5,000 lines is stated in `05-py61850-gap.md` §5 as an estimate and it is
the largest number in this roadmap. It is derived from `scl-lib`'s ~85
TypeScript modules, adjusted downward for Python being denser and for not
porting the NSD JSON.

It could be badly wrong in either direction. The ordering tables might be
mostly data; the referential-repair rules might be far more intricate than they
look.

**What would settle it:** implement `tExtRef`'s eight capabilities first — they
are self-contained, they are the ones Phase 7 most needs, and their size is a
usable multiplier for the rest. That is a cheap calibration and it should
happen early in Phase 2 rather than at the end.
