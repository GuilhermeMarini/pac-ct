# Idea 1 — Open questions

Decisions that are genuinely undecided, what each one turns on, and what
would settle it. Ordered by how much else depends on the answer.

---

## Q1. How much appetite is there for maintaining the private framework?

**This is the question the rest depend on.**

There are 3,050 lines of `mount.py` + `session.py` + `progress.py` +
`theme.py` + `themes/` that constitute a web framework with one maintainer and
no documentation as a framework.

- If the answer is *"it works, leave it"* → Phase 0 and Phase 2 are still
  worth doing, Phase 4 is not, and the roadmap is short.
- If the answer is *"not much"* → Phase 4 stops being optional polish and
  becomes the item that also unblocks the cleanest version of Idea 2.

**Turns on:** whether anyone other than the current maintainer is expected to
work on this code, and over what horizon.

---

## Q2. Do the three themes keep their separate markup?

Today Folha, Régua and Caderno emit **structurally different HTML**, not just
different CSS. That is why `inject_head` rewrites markup at all, and it is
1,586 lines of `themes/`.

- **Collapse to CSS-only** (Phase 3): removes the whole markup-injection
  mechanism, makes a static shell document possible, and is the assumption in
  `02-specification.md` §6.
- **Keep three structures**: they become three component variants. More work,
  and it carries the injection mechanism forward, but not incoherent.

**Turns on:** whether the structural differences are load-bearing to the design
or an artefact of how the three themes were built. That is a question about
intent, and only the author can answer it.

**What would settle it:** looking at the three home screens and asking whether
the same DOM with different CSS could produce all three. If yes, collapse.

---

## Q3. FastAPI or Flask — or neither?

See `01-stack.md` Part A for the full comparison. The decision reduces to one
trade:

**FastAPI** gives a generated OpenAPI contract — worth a lot, because both
roadmap items need a declared contract and this is the only option where it
maintains itself. It costs `pydantic-core`, a compiled dependency, in a bundle
that must install offline on Windows and Linux.

**Flask** costs seven small pure-Python wheels and no compiled code, maps
cleanly onto the existing mount model via blueprints, and matches the existing
threaded design exactly. It leaves the contract as a document someone has to
keep in step.

**Turns on:** how much the compiled-wheel risk is worth. `build_dist.py`
already has `windows_only_requirements()` for this class of problem, so the
machinery exists — the question is appetite, not capability.

**What would settle it:** build a `--windows` bundle with `pydantic` added to
`requirements.txt` and confirm it installs with `--no-index` and
`PIP_NO_CACHE_DIR=1`. That is a cheap, decisive experiment and it can be run
before anything else is committed to.

---

## Q4. What happens to the GLV?

It is 5,281 lines of Python, 4,308 of HTML, **3,160 of them inline
JavaScript in one 3,619-line file**, plus live relay sockets, poll threads,
`/events` and per-diagram state. Every other tool is forms and tables.

Three options:

1. **Migrate it last**, after everything else — the assumption throughout
   `03-phases.md`.
2. **Leave it on the old stack indefinitely**, with two frontends side by
   side. Perfectly workable, but it must be *chosen* and written down, or it
   becomes an accident.
3. **Migrate it first**, on the grounds that it is where a component model
   pays off most and the others are easy afterwards.

**Turns on:** whether the GLV is stable or still changing. Migrating a moving
target is the expensive case.

---

## Q5. What replaces "run `app.py --web` and look at it in three themes"?

That is currently the only visual gate, and the project conventions record
it as the documented procedure. Any frontend change makes it more important
and no easier.

- Under B0/B1 it stays manual. Acceptable, and unchanged from today.
- Under B3 a frontend test story becomes possible — and becomes a new
  dependency category (a test runner, possibly a browser driver) that has to
  justify itself against the same offline constraints as everything else.

**Turns on:** how often theme regressions actually occur. Nobody has measured
this; it may be a non-problem.

---

## Q6. Does `project_files` stay a tool?

It is imported by all seven other tools, by `mount.py` and by `session.py`,
and `/library` is served by the dispatcher rather than by the tool itself. It
is platform wearing a tool's clothes.

The likely answer — split it into a `library` platform module and a thin
`/files` screen — is recorded in `../00-baseline.md` §5.3 as a shared
prerequisite. It is listed here too because Idea 1 cannot draw the layering in
`02-specification.md` §1 cleanly until it is resolved.

**Turns on:** nothing external. This is a straightforward call that just has
not been made.

---

## Q7. Is any of this worth doing at all?

Stated plainly so it can be answered rather than assumed away.

The current design **works**, has 904 tests collected across 56 files, and
serves nine tools in production for real commissioning work. "Not conventional" is not
"broken", and no user has ever been affected by the architecture.

The case for acting is not aesthetic. It is:

- 6,829 lines of JavaScript that cannot be linted, tested, or imported
- 3,050 lines of framework with one maintainer and no documentation as such
- a visual gate that is one person looking at screens

The case against is that all three have been true for the life of the project
and have cost nothing measurable.

**Phase 0 is the answer that does not require settling this**, which is why it
is separated out and recommended unconditionally. Everything after Phase 2
does require settling it.
