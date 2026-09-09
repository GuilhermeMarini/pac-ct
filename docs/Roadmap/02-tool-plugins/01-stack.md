# Idea 2 — Stack

Three separable choices: how plugins are **discovered**, how they are
**distributed**, and how their **dependencies** arrive.

---

## Part A — Discovery

### A1. Setuptools entry points — **recommended**

Each plugin is a normal Python distribution declaring:

```toml
[project.entry-points."pacct.tools"]
ge-urs = "pacct_ge.tool:TOOL"
```

`dashboard.py` replaces its hardcoded list with
`importlib.metadata.entry_points(group="pacct.tools")`.

| For | Against |
|---|---|
| Standard, boring, zero new machinery | Requires the plugin to be pip-installed into the venv |
| Works identically for first-party and external plugins | An uninstall means `pip uninstall`, not deleting a folder |
| Version metadata and dependency resolution come free | Entry-point scanning cost at boot (negligible at this scale) |
| The tool's own `pyproject.toml` is the manifest | |

### A2. Folder drop — `plugins/<name>/`

A directory scanned at boot, each subfolder holding a plugin package.

| For | Against |
|---|---|
| No pip involved at all; install is unzip, uninstall is delete | Dependencies have to be solved separately |
| Fits `app.py`'s "pip only when an import fails" philosophy | Reinvents metadata, versioning and conflict handling |
| Trivially inspectable — an engineer can see what is installed | `sys.path` manipulation, which is its own class of bug |

### A3. Both

Entry points for the mechanism; a `plugins/` folder that is added to the path
and whose contents are installed into the venv on first boot.

**Recommendation: A1.** It is the standard mechanism, it carries version
metadata that §4 of the specification requires anyway, and the "install without
pip" advantage of A2 disappears once plugin bundles carry their own wheels
(Part C) — because at that point the install is a `pip install --no-index`
either way, and it works offline.

---

## Part B — Distribution

### The channel already exists

`src/pacct/update.py` is 803 lines and already does, correctly:

- reads a release manifest at
  `https://github.com/<repo>/releases/latest/download/manifest.json` —
  deliberately **not** `api.github.com`, because that allows 60 requests per
  hour **counted per source IP**, so a utility whose engineers sit behind one
  NAT shares one budget and gets `403` in answer to "is there a new version".
  Measured at the same instant from the same IP: the API answered 403 while
  the redirect answered 200.
- pins every asset URL to `releases/download/v<version>/`, never `latest/`, so
  the sha256 being verified belongs to the bytes being downloaded — a release
  published between the check and the download cannot substitute different
  bytes for the digest about to be checked.
- **verifies sha256 before unpacking a single byte**, from the manifest that
  named the version, not from a second fetch.
- swaps atomically with a backup (`_swap_in` / `_restore`) and rolls back
  (`rollback_portable`, `--reverter`).
- refuses a manifest with `release: false`, so a snapshot uploaded by mistake
  cannot install.
- carries no credential, deliberately: authenticating would lift the API limit
  to 5,000/hour and would require shipping a token inside a zip handed to
  substations, which is a credential in a public artefact.

**A plugin installer reuses essentially all of this.** The security-sensitive
parts are done, and the reasoning behind them is recorded.

### What has to be added

**One manifest becomes a catalogue.** Today one manifest describes one
application version. Plugins need an index: what exists, at what versions, and
compatible with which host.

| Option | Shape | Assessment |
|---|---|---|
| **B1** | Each plugin is its own GitHub repo with its own releases; one `plugins.json` index published as a release asset on `pac-ct` | **Recommended.** Reuses the existing fetch path exactly; each plugin gets independent releases, which was the point |
| B2 | All plugins published as assets on `pac-ct` releases | Simpler, but couples plugin releases back to app releases — defeats the motivation |
| B3 | PyPI | Standard, but a substation install cannot reach it, and it puts publishing outside the author's release workflow |

---

## Part C — Dependencies

**This is the hard part, and the code is not the hard part.**

If a plugin needs a package the host does not have — `gelib` for a GE tool,
`siemenslib` for a Siemens one, `openpyxl` for anything producing a
spreadsheet — then "install from the web" means running pip, which
reintroduces a network dependency at a *new* moment in the application's life.

### C1. Plugins are dependency-free

A plugin may import only the platform API and libraries already shipped.

Safe, and rules out `gelib` and `siemenslib` — which is most of the point.
**Rejected.**

### C2. The plugin bundle carries its own vendored wheels — **recommended**

A plugin ships as a zip containing its source and a `vendor/` of wheels for
everything it needs that the host does not already have. Install is:

```
unzip, then  pip install --no-index --find-links <plugin>/vendor/
```

This is exactly the shape `build_dist.py` already produces for the application
itself, and `split_requirements()` already splits requirements by *shape*
rather than by name, so the mechanism generalises.

**It has one elegant property: the online/offline distinction disappears.**
The plugin zip is the unit. It can arrive by download over hotel wifi, or on a
USB stick handed over at the substation gate. Same artefact, same install path,
same sha256 verification, same result.

### The trap `pin_direct_references()` already documents

`pip install --no-index` **does not disable a direct reference**. A
`name @ git+https://…` requirement still goes to git, and a substation has
neither network nor git. It passed on the build machine anyway, because pip had
the wheel sitting in its own HTTP cache — the kind of pass that means nothing.

A plugin build must inherit that same guard: refuse to build when a
requirement has no wheel in `vendor/`, and verify with `PIP_NO_CACHE_DIR=1`.

---

## Part D — The invariant that must be rewritten

Today the rule is:

> **The application never needs the network to run.**

It is why `app.py` calls pip only on an import failure, why a CDN is banned,
and why nine `.woff2` fonts ship in `static/`.

With online plugin installation that becomes:

> **The application never needs the network to run what is already installed.**

Still a good invariant — but it must be **written down explicitly**, because
the tempting mistake is a plugin that checks for its own updates at boot, or
fetches a resource lazily on first use. One such plugin turns "no internet"
back into "a tool does not come up", which is the exact failure mode `app.py`
was carefully designed to avoid and which the `py61850>=0.2.0.dev1` incident
already demonstrated once.

**This belongs in the plugin contract as a stated prohibition, and ideally as
a test** — boot the application with no network and assert every installed
plugin mounts.

---

## Recommendation, assembled

| | Choice |
|---|---|
| Discovery | **A1** — setuptools entry points, group `pacct.tools` |
| Distribution | **B1** — per-plugin repos and releases, one index on `pac-ct` |
| Dependencies | **C2** — plugin bundle carries vendored wheels |
| Install path | reuse `update.py`: manifest → sha256 verify → unpack → `pip install --no-index` |
| Trust | **first-party only** — see `04-open-questions.md` Q1 |
