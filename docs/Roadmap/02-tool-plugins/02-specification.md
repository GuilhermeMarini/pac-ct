# Idea 2 — Specification of the plugin contract

What a plugin is, what it may reach, how it is versioned, and how it is
installed. Written so a plugin can be built against it and so violations are
detectable.

---

## 1. What a plugin is

A Python distribution that declares one or more entry points in the group
`pacct.tools`, each resolving to a `Tool` descriptor:

```python
@dataclass(frozen=True)
class Tool:
    key: str            # stable id, e.g. "ge-urs"; also the cache/session key
    prefix: str         # mount prefix, e.g. "/ge-urs"
    label: str          # menu label, Portuguese, accents included
    description: str    # one line for the menu, Portuguese
    build: Callable     # (logger, sessions, ...) -> handler class
    requires_host: str  # PEP 440 specifier, e.g. ">=1.12,<2"
    order: int = 100    # menu position
```

`build` is the existing handler-factory pattern, unchanged — every current
tool already exposes one (`build_glv_handler`, `build_dnp_map_handler`, …).
**The nine tools in the tree today already satisfy this shape**, which is why
the discovery change is small.

---

## 2. The platform API

The set a plugin may import. It is the set tools already import today
(measured in [`../00-baseline.md` §3](../00-baseline.md)), promoted from habit
to contract.

| Module | Provides | Import lines from tools today |
|---|---|---:|
| `pacct.paths` | all filesystem path constants | 21 |
| `pacct.web.session` | `SessionHandler`, `Session`, `SessionManager` | 14 |
| `pacct.web.rdb_write` | the only sanctioned path that writes bytes into an RDB | 11 |
| `pacct.core` | live relay communication | 10 |
| `pacct.web.xlsx_names` | spreadsheet naming | 8 |
| `pacct.compat` | `ensure_telnetlib()` and friends | 7 |
| `pacct.web.progress` | `JobReporter`, stage/fraction reporting | 7 |
| `pacct.web.mount` | `Mount`, `inject_head` | 6 |
| `pacct.web.themes` | theme tokens | 6 |
| `pacct.library` (new) | the project file library, extracted from `project_files` | — |

**Rules:**

1. A plugin imports **only** from this list, plus the published libraries
   (`sellib`, `py61850`, `cfbwrite`) and its own dependencies.
2. A plugin **never** imports another plugin. The measured graph today has no
   such edge; the contract keeps it that way.
3. A plugin **never** imports `pacct.web.dashboard` — that is the host.
4. Anything not on this list is private to the host and may change without a
   MAJOR bump.

Rule 1 should be enforced by a test over installed plugins, not left to
discipline.

---

## 3. What a plugin owns and does not own

| Owns | Does not own |
|---|---|
| Its mount prefix and every route under it | The port, the server, the dispatcher |
| Its own `templates/` and `static/` assets | `/theme.css`, `/static/fonts/`, `/progress`, `/library` |
| Its per-session state object, keyed by `Tool.key` | The session cookie, the session directory layout, the TTL sweeper |
| Its menu entry (`label`, `description`, `order`) | The menu's markup, which is the theme's |
| Its own dependencies, vendored | Any host dependency's version |

---

## 4. Version compatibility — non-negotiable

**`requires_host` is a PEP 440 specifier, checked at install time and again at
every boot.**

This is not defensive over-engineering; it is the recorded failure mode of
this project. The `py61850>=0.2.0.dev1` incident: a specifier that resolved to
nothing meant pip failed, `app.py` exited, and **not one of nine tools came
up**. A plugin system multiplies that seam by the number of plugins.

The concrete scenario to prevent: an engineer installs a plugin at a hotel
with wifi, drives to a substation, the plugin fails to import against a
refactored `SessionHandler`, and there is no internet and no recourse.

**Required behaviour:**

| When | Behaviour |
|---|---|
| Install, incompatible | Refuse. Say which host version is needed and which is installed. |
| Boot, incompatible | **Log, skip that plugin, and continue.** Show it as unavailable in the menu with the reason. |
| Boot, plugin raises on import | Same — skip, log, continue. |

**One plugin must never be able to stop the application from booting.** The
host wraps every plugin import and every `build()` call. This is the single
most important rule in this document, because the alternative is a substation
with no working tools and no way to fix it.

The host also declares its own API version, bumped independently of `VERSION`
when the platform API in §2 changes incompatibly.

---

## 5. Distribution format

A plugin bundle is a zip:

```
pacct-plugin-<key>-<version>.zip
├── manifest.json          version, requires_host, artifacts, sha256 per file
├── src/                   the plugin package
└── vendor/                wheels for every dependency the host lacks
```

Install:

1. Fetch `manifest.json`, verify `requires_host` against the running host.
2. Download the artefact; **verify sha256 from that same manifest before
   unpacking anything**.
3. Unpack to a staging directory.
4. `pip install --no-index --find-links vendor/ ./src`
5. Atomic swap into place, keeping the previous version for rollback.
6. Rebuild the mount list — a restart is acceptable and simpler than hot
   reload; hot-swapping a handler while a relay poll thread is running is not
   worth the failure modes it invites.

Steps 1–3 and 5 are `update.py`'s existing logic, parameterised.

**Uninstall** removes the plugin's code and its entry point. It **does not**
remove dependencies — see the reference-counting hazard in
[`README.md` §1](README.md).

**Rollback** keeps the previous version's directory, as `_swap_in`/`_restore`
already do for the application. A plugin that cannot roll back is a worse
experience than the application it extends.

---

## 6. Trust boundary

**First-party only, for now.** The index lists plugins published by the project
author; artefacts are verified by sha256 from a manifest served over HTTPS from
the author's own releases.

The reason is not caution for its own sake. A plugin is arbitrary Python
imported into a process that holds live relay connections and has
`pacct.web.rdb_write` on its import path. Writing bytes into a protection relay
is the action this project reserves a MAJOR version bump for. Third-party
plugins would need signing, sandboxing and review — a genuinely different
project.

**The loader must not accidentally build the third-party case.** Discovery is
restricted to the declared entry-point group, from distributions installed by
the plugin installer. There is no "point it at a URL" mode.

---

## 7. What the user sees

A **"Ferramentas disponíveis"** screen beside the menu:

- installed plugins, with version and an uninstall action
- available plugins from the index, with an install action
- an offline state that says so plainly and lists what is installed, since
  the index is unreachable — never an error that looks like a fault

This is the part that makes Idea 2 a feature rather than packaging, and it is
where `gelib` and `siemenslib` finally reach a user.

---

## 8. Recognising completion

- [ ] `dashboard.py` has no hardcoded tool list
- [ ] The menu catalogue is contributed by tools, not held in
      `web/themes/items.py`
- [ ] `project_files` is split into a platform `library` and a `/files` tool
- [ ] All nine current tools are discovered through the same mechanism a
      plugin uses — **the host dogfoods its own contract**
- [ ] A test asserts no plugin imports outside the §2 list
- [ ] A test asserts a plugin that raises on import does not stop the boot
- [ ] A test asserts an incompatible `requires_host` is refused at install and
      skipped at boot
- [ ] A plugin bundle installs with `--no-index` and `PIP_NO_CACHE_DIR=1`,
      with no network
- [ ] The application boots with no network and every installed plugin mounts
- [ ] `gelib` and `siemenslib` each have a plugin, which is the proof the
      contract is usable from outside
