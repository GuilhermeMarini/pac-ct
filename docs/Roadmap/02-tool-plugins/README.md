# Idea 2 — Tools as installable plugins

**Status: discussed, not decided. Cheaper than first assessed — see §3.**

---

## What was asked

> "I want to make the tools as plugins or module that can be installed or
> removed as the user wish, so the tools development is independent, and tools
> that the users don't want don't use disk space."

and, on distribution:

> "the user will not always be offline, so he could in theory install the
> plugins from the web, similar to how he updates the app."

Both readings are sound. One of the three stated motivations does not survive
measurement, and the strongest argument for the idea was not among the three.

---

## 1. The disk-space motivation does not hold

Measured 2026-09-08:

| | Size |
|---|---|
| All eight tools' source (Python + HTML) | **~1.8 MB** |
| `cache/` on this machine | **1.3 GB** |

The heavy things on disk are the content-addressed RDB cache (40–140 MB per
file) and the dependencies — and the dependencies mostly **do not** split with
the tools: `sellib`, `py61850` and `cfbwrite` are needed by nearly everything.

The single exception found: **`openpyxl` is imported by exactly two tools**,
`vb_updater` and `gle_exporter`. Nothing else in `src/pacct` touches it.

Worse, uninstalling cannot safely reclaim even that. Shared dependencies would
need reference counting, and getting it wrong means removing a package another
installed tool imports — which, on boot, means `app.py` tries to pip-install it
and exits when there is no network. The safe policy is that **uninstall removes
plugin code and never dependencies**, which puts the reclaimed space back at
roughly zero.

**Recommendation: drop disk space as a justification.** It is not a reason to
do this, and designing around it would produce a worse system.

---

## 2. The motivations that do hold

**Independent development and release cadence.** Stated in the request and
correct. Today everything moves under one `VERSION` (1.11.4) and one release.
A fix to the VLAN Mapper cannot ship without shipping the GLV.

**Not shipping half-finished tools to everyone.** A tool can exist, be
installable, and simply not be installed — rather than being either in the
bundle for all users or not written.

**And the one that was not in the request, which is the strongest:**

> **`gelib` and `siemenslib` already exist, are already correctly shaped, and
> have no consumer at all.**

`gelib` (~300 LOC, no dependencies, GE UR `.urs` settings export) and
`siemenslib` (~350 LOC, depends on `py61850`, the Siemens-private half of an
SCL file) are vendor siblings of `sellib` — web-free, socket-free readers of
one vendor's files. `pac-ct` imports neither. They are, structurally, two
plugins waiting for a host.

That is the case for Idea 2: **it finishes a split that has already been
made.** Not disk space.

---

## 3. The correction: the tools are already isolated

The first assessment of this idea reported heavy tool-to-tool coupling —
`glv.transport`, `glv.link`, `gle_tabs` and `dnp_map` being imported across
tool boundaries — and concluded that untangling it was the main work.

**That was wrong.** It came from a grep that counted a tool importing *itself*
(`glv/handler.py` importing `glv.transport` is an intra-package import, not a
cross-tool edge).

Measured properly, excluding intra-package imports and excluding `dashboard.py`
whose job is to import everything, the complete cross-tool graph is:

```
glv               -> project_files    (1 import)
dnp_map           -> project_files    (1 import)
gle_tabs          -> project_files    (1 import)
gle_exporter      -> project_files    (1 import)
vb_updater        -> project_files    (1 import)
vlan_mapper       -> project_files    (1 import)
settings_compare  -> project_files    (1 import)
```

**There are no other edges between tools.** Every tool imports `project_files`
exactly once and nothing else from another tool.

And `project_files` is not really a tool: `mount.py` imports its `library`,
`session.py` imports its `derived`, `library` and `client`, and `/library` is
served by the dispatcher. It is platform with a screen attached.

**So the graph the plugin model needs is already the right shape.** The
untangling work assumed to be the bulk of this idea does not exist. What
remains is packaging, discovery, distribution and the contract — all of which
are real, but none of which is architectural surgery.

---

## 4. What this actually requires

1. **Replace the hardcoded mount list** at `dashboard.py:256-275` with
   discovery. Small.
2. **Move the menu catalogue out of the theme layer.** It lives in
   `web/themes/items.py` today — central, and owned by themes. A plugin has to
   contribute its own entry.
3. **Resolve `project_files`** into a platform `library` module plus a thin
   `/files` tool. Already a shared prerequisite
   ([`../00-baseline.md` §5.3](../00-baseline.md)).
4. **Declare the platform API** — the set in `../00-baseline.md` §3 that tools
   already import. Nothing declares it today, so every import is equally
   revocable and equally load-bearing with no way to tell which.
5. **Enforce host/plugin version compatibility**, at install *and* at boot.
   Non-negotiable; see `02-specification.md` §4.
6. **Split the four single-file tools**, so each can be packaged as a unit.
7. **Distribution and trust** — see `01-stack.md`.

---

## 5. What this is not

- **Not a third-party plugin ecosystem.** A plugin is arbitrary Python
  imported into a process that holds live relay connections and can write bytes
  into a protection relay. First-party only — see `04-open-questions.md` Q1.
- **Not a way to reclaim disk space.** See §1.
- **Not a replacement for the update channel.** It reuses it — `update.py` is
  803 lines that already solve most of this.

---

## Read next

- [`01-stack.md`](01-stack.md) — discovery and distribution mechanisms
- [`02-specification.md`](02-specification.md) — the plugin contract
- [`03-phases.md`](03-phases.md) — the ordered route
- [`04-open-questions.md`](04-open-questions.md) — what is genuinely undecided
