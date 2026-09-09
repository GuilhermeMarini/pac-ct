# Idea 2 — Open questions

Ordered by how much else depends on the answer.

---

## Q1. First-party only, or third-party plugins eventually?

**The recommendation is first-party only, and the reason is specific rather
than cautious.**

A plugin is arbitrary Python imported into a process that holds live relay
connections and has `pacct.web.rdb_write` on its import path. Writing bytes
into a protection relay is the action this project reserves a MAJOR version
bump for. A third-party plugin has, by construction, the same power.

Supporting third parties properly needs signing, sandboxing, review, and a
security-response process. That is a different project, and it should not be
arrived at by accident.

**The design consequence, if first-party is chosen:** the loader must not
accidentally build the third-party case. Discovery is restricted to the
declared entry-point group from distributions the installer put there. There is
no "point it at a URL" mode, no arbitrary path scanning, and the index is the
author's own.

**Turns on:** whether anyone outside the project is ever expected to write a
tool. If the honest answer is "no, and probably never", the whole trust
question collapses and Idea 2 gets substantially simpler.

---

## Q2. Is the N-wide compatibility seam acceptable?

The workspace conventions already record that *a change to `sellib`,
`cfbwrite` or `py61850` is not finished until `pac-ct`'s suite passes against
it* — and that
**nothing in CI checks that seam**, because the repos build independently. The
one licensed exception was deliberately spent on `sellib` 3.0.0.

Plugins multiply that seam by the number of plugins. Every refactor of
`SessionHandler` or `paths` is now a change that N repositories must be checked
against, by hand, with no CI covering it.

Mitigations, none free:

- `requires_host` and boot-time skipping (already required by
  `02-specification.md` §4) make the failure *safe*, not *absent* — the tool
  still does not come up, it just does not take the others with it.
- A compatibility test suite the host publishes and each plugin runs.
- A monorepo of first-party plugins, released independently but tested
  together — which recovers most of the CI coverage while keeping the release
  independence that was the motivation.

**The monorepo option deserves serious weight.** It gives independent release
cadence, which is what was actually asked for, without giving up cross-seam
testing, which is what would actually break.

**Turns on:** tolerance for a class of breakage that only surfaces in a
substation.

---

## Q3. Does the GLV ever become a plugin?

It is 5,281 lines of Python and 4,308 of HTML — larger than the other seven
tools combined. It holds live relay sockets, poll threads, `/events`, and a
transport layer (`glv/transport/`) that `dashboard.py` imports directly for
`SCAN_MMS` / `SCAN_TELNET`.

- **As a plugin:** the contract has to accommodate long-lived sockets,
  background threads, and shutdown ordering — considerably more than
  "a handler class and some routes".
- **Staying in the host:** the honest option. It is arguably not a tool but the
  application's core, with the others as accessories around it.

**Turns on:** whether "plugin" means "any tool" or "any tool except the one
this application exists for". Either answer is defensible; leaving it
unanswered means the contract gets designed for a case that may never arrive.

---

## Q4. Restart or hot reload after install?

`02-specification.md` §5 specifies **restart**, on the grounds that
hot-swapping a handler while a relay poll thread is running invites failure
modes not worth the convenience.

The counter-argument: an engineer mid-commissioning with several GLV diagrams
open and a session's uploads in `cache/sessions/<sid>/` will not welcome a
restart. Sessions survive (they are on disk with an 8-hour TTL), but live relay
connections do not.

**Possible middle ground:** allow install at any time, apply at next start, and
say so plainly on screen.

**Turns on:** how often plugins are actually installed. Probably rarely, which
argues for the simple answer.

---

## Q5. How are host API versions numbered?

`VERSION` (1.11.4) is the application's version and its MAJOR is already
reserved for *a change to what gets written into a relay*. That is a different
axis from *a change to the platform API*.

Options: a separate `PLATFORM_API_VERSION`; or accept that MINOR bumps may
break plugins and rely on `requires_host` upper bounds.

**Turns on:** how stable the platform API turns out to be in its first year.
Unknowable now — which argues for a separate, conservative number and generous
upper bounds early on.

---

## Q6. Where do the plugin repositories live, and who releases them?

Each first-party plugin needs a repository, a `release.yml` checking the tag
against both version literals, trusted publishing, and a GitHub release — the
pattern all six workspace repos already follow.

That is real recurring overhead per plugin. With `gelib` and `siemenslib`
already existing as separate repos it is a pattern that is working, but they
are libraries released rarely; plugins would release more often.

**Turns on:** Q2. If the monorepo option is taken, this question mostly
disappears.

---

## Q7. Is Phase 4 worth committing to before the answer is known?

Phase 4 extracts `vlan_mapper` (323 lines of Python, the smallest tool) into
its own repository. It is where every assumption in the specification meets
reality for the first time.

**The argument for doing it early:** it is the cheapest possible test of the
whole idea. If the platform API is insufficient or the release seam is
intolerable, that is learned at the cost of one small tool rather than nine.

**The argument against:** it is not free, and Phases 1–3 already deliver the
internal decoupling with none of the distribution cost.

This is the decision point the whole route is built around, and it is worth
treating as one explicitly rather than sliding through it.

---

## Q8. Was the disk-space motivation the real one?

[`README.md` §1](README.md) shows it does not survive measurement: all eight
tools' source is ~1.8 MB against a 1.3 GB `cache/`, dependencies mostly do not
split with the tools, and uninstall cannot safely reclaim them anyway.

If disk space was genuinely the driver, then **the higher-value work is
elsewhere** — an RDB cache eviction policy, or a way to clear `cache/rdb/`,
would return three orders of magnitude more space than removing every tool in
the application.

If it was one reason among several, the others (independent release cadence,
and giving `gelib` and `siemenslib` a host) stand on their own and the roadmap
proceeds unchanged.

**Worth answering explicitly**, because it decides whether cache management
should be on this roadmap at all — it currently is not, and on the measured
numbers it has a stronger claim to a place than plugins do.
