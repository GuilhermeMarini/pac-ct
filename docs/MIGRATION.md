# Migration plan — private `Sel_comissioning` → public `pac-ct` (+ libraries)

Source: `/home/guilh/py_projects/pac-ct`, branch `dnp-map-copiar` (`f975396`).
Target: `/home/guilh/py_projects/comissioning-project/`, one folder per repo.

Decisions already taken (2026-09-03):

| | |
|---|---|
| Licence | **AGPL-3.0-or-later** — py61850, which `glv/transport/mms.py` imports directly, is AGPL-3.0-or-later. Every extracted library inherits it. |
| History | **Fresh start.** The private repo stays as the archive; it carries 102 MB of SEL manuals that cannot be published. |
| Repos | App repo now (**done**: https://github.com/GuilhermeMarini/pac-ct, public, 1 commit), library repos after this review. |
| Scope | Full modernisation: `src/` layout, `pyproject.toml` per package, ruff + mypy, CI, typed public APIs. |
| Folder | `comissioning-project` with a hyphen — `app.py` already carries a scar from a path with spaces (`os.execv` and `"06 - Ferramentas"` on Windows). |

Everything below is ordered so that **the 828 passing tests stay passing at
every step**. That is the migration's only real safety net, and no phase ends
with it red.

---

## Status — 2026-09-03

| Phase | State |
|---|---|
| 0.1 samples | **Done** — anonymised, see below |
| 0.2 library split | **Decided** — `cfbwrite` + `selfiles`, both repos created |
| 1 skeleton | **Done** — `src/` layout, `pyproject.toml`, ruff, mypy, CI |
| 2 what moves | **Done** — 319 files; the vendor documents stayed behind |
| 3 defect fixes | **Done** — D1..D5, each with a test that fails without it |
| 4 library extraction | **Done** — `cfbwrite` and `selfiles`, both consumed by the app |
| 5 dist + auto-update | **Done** — bundle, versioned install, updater; see below |

Verification as it stands, across the three repositories: **881 tests pass**
(the 828 that came across, plus 9 Phase 3/4 regressions and 44 for Phase 5) —
639 in `pac-ct`, 225 in `selfiles`, 17 in `cfbwrite`, conserved exactly by the
split. `ruff` and `mypy` are clean in all three, and the app boots and serves
all eleven screens with both libraries behind it.

Phase 4 as built, with three departures from the plan above worth naming:

- **The corpus stayed in one place.** The plan implied tests move with their
  code; the ones that need the 63 MB sample corpus stayed in `pac-ct`, where
  the corpus lives, rather than duplicating it into a second public repo. So
  `selfiles`'s own suite is self-contained (it builds its fixtures with
  `cfbwrite`), and the corpus-driven tests exercise the library from the app.
- **`selfiles.gle` was not split into `parse.py` + `render.py`.** The two
  halves share the geometry constants and always travel together, and the
  refactor that actually earns a split is REVIEW.md S1 (drive the renderer
  from the model registry, which is where the black-counter bug came from).
  Splitting first would mean moving the same code twice.
- **No compatibility shim was left behind.** The plan proposed re-export
  modules; instead all 45 call sites were rewritten in the same change, since
  the tests could verify it immediately. A shim nothing imports is dead code.

One bug the migration itself surfaced: the rewrite missed a multi-name import
in `dnp_map/handler.py`, and **no test caught it** — the route handlers live
inside a factory closure and nothing can import them (REVIEW.md S2). It failed
at boot instead. That is the cost of S2, priced.

**The samples were anonymised, not dropped.** The demo RDB was rebuilt through
this project's own `ole_rebuild` — 6 relay storages renamed, 147 of 1008
streams rewritten, the other 861 byte-for-byte identical — and the SCD and GLE
samples rewritten in place. `SID`/`TID` no longer name a substation and every
address is RFC 5737. The scrub reached further than the two files: a second
substation name, a utility, a city, four source modules, the MMS test fixtures
and six design documents. A scan of the whole tree, binaries included, finds
nothing left. The mapping script was deliberately **not** committed — publishing
it would undo the anonymisation.

Two decisions taken while implementing Phase 1, both departures worth naming:

- **`paths.py` now separates three roots.** `PACKAGE_DIR` for what travels
  inside the package (templates, fonts), `PROJECT_ROOT` for the project
  (found by marker, or `PACCT_ROOT`), and `DATA_ROOT` for mutable state
  (`config.ini`, `cache/`, `rdbs/`, overridable with `PACCT_DATA_DIR`). The
  `src/` layout forced the first split; Phase 5.3's versioned install needs
  the second, so it was done once.
- **ruff does not select `G`, and mypy does not yet check `web/` or `cli/`.**
  Both are written down where they are configured, with the reason. `G` would
  rewrite 62 measured log messages in code that needs a bench relay; the mypy
  backlog is ~110 findings in the web layer, most of them two root causes
  (REVIEW.md S2/S8). `core/`, `parsers/` and `matchers/` — the code Phase 4
  extracts — are clean and must stay clean.

---

## Phase 0 — Two decisions that block the first code commit

### 0.1 The sample files *(REVIEW.md G1)*

`samples/substation_demo.rdb` and `.scd` carry a real substation's bay names
and 30 real control-network IPs. Nothing is committed to the public repo until
one of these is chosen:

- **(a) Anonymise** — rewrite names and IPs to RFC 5737 (`192.0.2.x`) with a
  one-off script built on `parsers/ole_rebuild.py` (it already rebuilds an RDB
  at any size and verifies its own output). Then update the tests that assert
  on names, in the same commit. Best outcome: the public repo keeps a full,
  runnable suite. Cost: roughly a day, and the golden SVG must be regenerated
  and its diff read.
- **(b) Keep them private** — the public repo ships without `samples/*.rdb`
  and `*.scd`; the 8 test modules that need them skip when absent, exactly as
  `tests/test_mms_tables.py` already does for the ICD corpus. Cost: the public
  suite is smaller and CI cannot cover the RDB/SCD paths.
- **(c) Publish as-is** — only if that data is yours to publish.

### 0.2 The library split *(REVIEW.md §4)*

Recommended: extract **two** now, defer the third with the reason written down.

| repo | from | why now |
|---|---|---|
| `cfbwrite` | `parsers/ole_rebuild.py` | Zero domain knowledge, one dependency, 326 lines of tests, and an audience outside this project |
| `selfiles` | `parsers/*` + `core/*` + `matchers/*` | The whole domain layer is already web-free; `selfiles.scl` holds the 61850 half so `sclread` can leave later without moving code twice |

Names must be checked on PyPI before the repos are created. If either is taken:
`cfbwrite` → `olecfb-write`, `selfiles` → `selquickset`.

---

## Phase 1 — Skeleton (no behaviour change)

```
comissioning-project/
├── pac-ct/                     ← github.com/GuilhermeMarini/pac-ct  (exists)
│   ├── src/pacct/
│   ├── tests/
│   ├── data/                   relay_models/ wordbits/ mms_map/
│   ├── config/config.ini.example
│   ├── tools/
│   ├── app.py
│   ├── pyproject.toml   ruff.toml   mypy.ini
│   ├── LICENSE  NOTICE.md  README.md  ENGINEERING-NOTES.md  VERSION
│   └── .github/workflows/ci.yml
├── cfbwrite/                   ← new public repo
└── selfiles/                   ← new public repo
```

1. `git mv pacct src/pacct` (src layout: the installed package is what the
   tests import, so a missing `data/` file fails in CI instead of resolving
   against the working tree).
2. `pyproject.toml` — `requires-python = ">=3.10"`, dependencies exactly as
   `requirements.txt` including `py61850>=0.2.0.dev1` (the pre-release mention
   is load-bearing; `tests/test_requirements.py` pins it), `[project.scripts]`
   `pac-ct = "pacct.cli.entry:main"`, and `version = {file = "VERSION"}`.
   `requirements.txt` stays — `app.py` bootstraps from it and that is the
   substation install path.
3. `ruff.toml` — start with `E,F,W,I,UP,B,G` and `line-length = 88`. Expect
   ~49 `G004` (f-string logging) and a wave of `UP` (quoted annotations,
   `Optional`). Fix `I` (import order) and `G` first; take `UP` file by file.
4. `mypy.ini` — `python_version = 3.10`, non-strict at first, then
   `disallow_untyped_defs` per package as S8 is worked through. `py.typed` in
   each library, not in the app.
5. `.github/workflows/ci.yml` — 3.10 / 3.11 / 3.12 matrix, `ruff check`,
   `mypy`, `pytest`. Under decision 0.1(b) the RDB/SCD tests skip on CI, and
   the workflow should say so out loud rather than appear to cover them.
6. `NOTICE.md` — AGPL for PAC CT, MIT for vendored `selprotopy` (keep
   `docs/LICENSE-selprotopy`), OFL for the nine `.woff2` (keep `licencas/`),
   AGPL for py61850 as a dependency.

**Gate:** `pytest` green, `ruff check` green, `mypy` runs (findings allowed).

---

## Phase 2 — What moves, what changes, what stays behind

338 tracked files today. 318 after dropping the vendor documents; 74 MB, of
which 63 MB is the two samples from decision 0.1.

| Path | Disposition |
|---|---|
| `pacct/**` (95 files) | **Copy** → `src/pacct/`. Import paths unchanged. |
| `tests/**` (46) | **Copy** unchanged. Then decision 0.1 touches 8 of them. |
| `data/relay_models`, `data/wordbits`, `data/mms_map` (24) | **Copy.** Ours, derived, not vendor documents. |
| `selprotopy/**` (49) | **Copy verbatim.** Vendored, MIT, hook-protected. Keep `docs/LICENSE-selprotopy` beside it. |
| `app.py`, `requirements*.txt`, `config/config.ini.example` | **Copy**, with the S7 string fixes. |
| `tools/**` (16) | **Copy.** `tools/wordbits_from_dnp_profile.py` documents that its input zips are not in the repo. |
| `README.md`, `ENGINEERING-NOTES.md` | **Copy and edit** — paths become `src/pacct/`, and ENGINEERING-NOTES.md gains the new repo/library layout. Its measured gotchas are the most valuable file here; do not summarise them away. |
| `mockups/**` (45) | **Copy.** They are the design reference the three themes are built from. |
| `docs/superpowers/**` (13) | **Copy.** Record of how decisions were reached. |
| `docs/*.pdf` (5, 102 MB) | **Leave behind.** SEL copyright. |
| `docs/*.zip` (16) | **Leave behind.** SEL DNP3 device profiles. The derived `data/wordbits/*.json` is what ships. |
| `samples/*.rdb`, `*.scd` (63 MB) | **Decision 0.1.** |
| `samples/*.gle.xml`, `*.stream*` (10) | Review each for names before copying — same question as 0.1, much smaller files. |
| `cache/highlights_QPC1_TR1_UPC1.json` | **Leave behind.** One relay's annotations, named after a real bay; it is runtime state that was committed by accident. |
| `fixtures/gle_sem_61850.txt`, `model_missing.txt` | **Copy only after scrubbing** — they contain the substation's relay names. |
| `corrections_plan.md` | **Leave behind** or move to the archive; it predates the rename and describes work already done. |
| `copiar-regua.png`, `glv-conectores.png` | **Leave behind.** `.gitignore` already calls loose root PNGs scratch. |

`.gitignore` carries over with two corrections: the `samples/*.scd` negation
comment must stop claiming the file is anonymised (or become true, under
0.1(a)), and `dist/` is added (Phase 5).

---

## Phase 3 — Fixes applied during the move

Applied in the new tree only, each with the test that pins it. Ordered by
consequence, and each is independently revertable.

| # | Fix | Test to add |
|---|---|---|
| D1 | Add the missing `.element-counter` rule to `gle.CSS`, colours consistent with `element-timer`/`element-latch` | Render a page containing `COUNTER`; assert every `class="element-*"` emitted has a rule in `CSS` — which would have caught this class of bug generically |
| D2 | `mms_tables._load()`: per-file try/except with a warning; `_LOADED` flag instead of `if _CACHE:` | A malformed table beside two good ones leaves both good ones loadable |
| D3 | `cli/runner.py`: `time.time()` → `time.monotonic()` at ~20 duration sites | The fake-clock-jump shim from `tests/test_mms_transport.py`, pointed at the runner's deadline helpers |
| D4 | `rdb_cache`: drop the lock when the entry is swept | `sweep()` leaves `_LOCKS` empty |
| D5 | One `read_json_body(max_bytes=…)` helper; adopt it in the six routes | A `Content-Length` over the ceiling is refused, not buffered |
| S7 | Stale strings: `SELProtoPy` ×3, `examples/relay_models/` ×4, `__version__`, package docstring | — |

Fixes explicitly **not** bundled with the move (they change behaviour, and
should land as their own reviewed change afterwards): S1 (registry-driven
renderer), S2 (routes out of the closures), S3 (split the three monoliths),
S5 (shared registry base), E1 (`ScdDocument`).

---

## Phase 4 — Library extraction

Only after Phase 3 is green. Each library is created, published, and *then*
consumed — never extracted and rewired in one commit.

### 4.1 `cfbwrite` → `/home/guilh/py_projects/comissioning-project/cfbwrite/`

```
cfbwrite/
├── src/cfbwrite/__init__.py     ← parsers/ole_rebuild.py, unchanged logic
│                                   public API: rebuild(), write_ole(),
│                                   Entry, CfbWriteError
├── tests/test_rebuild.py        ← tests/test_ole_rebuild.py (326 lines)
├── pyproject.toml               ← deps: olefile>=0.47 ; python >=3.10
└── LICENSE (AGPL-3.0-or-later)  README.md  py.typed
```

Changes on the way in: `OleRebuildError` → `CfbWriteError` (alias kept for one
release), the five Portuguese error messages become English (it is a library
for strangers now), and the module docstring keeps the RDB story as the
motivating example. **No logic changes** — this code writes protection relay
settings files, and its self-verification is the reason it may.

Then in `pac-ct`: `pacct/parsers/ole_rebuild.py` becomes a 3-line re-export
shim so `pacct.web.rdb_write` and `tests/test_rdb_write.py` keep working, and
`cfbwrite` joins `requirements.txt`.

### 4.2 `selfiles` → `/home/guilh/py_projects/comissioning-project/selfiles/`

```
selfiles/
├── src/selfiles/
│   ├── rdb.py  rdb_cache.py          ← parsers/rdb*.py
│   ├── settings.py  dnp_map.py       ← parsers/sel_settings.py, set_dnp.py
│   ├── gle/  (parse.py, render.py)   ← parsers/gle.py, split at the seam
│   ├── dnp_profile.py                ← parsers/dnp_profile.py
│   ├── selogic/ (parser.py, compare.py, model.py, catalog.py)
│   │                                 ← core/selogic_parser, logic_compare,
│   │                                   settings_model, settings_catalog
│   ├── models/ (relay_models.py, wordbits.py)   ← core/*, data dir injected
│   ├── scl/ (read.py, mms_tables.py) ← parsers/scd.py, core/mms_tables.py
│   ├── match.py                      ← matchers/relay_scd.py
│   └── data/                         ← data/{relay_models,wordbits,mms_map}
└── pyproject.toml   (deps: olefile, openpyxl, cfbwrite)
```

The one structural change required (**S6**): every registry takes its directory
instead of importing `pacct.paths`.

```python
# selfiles
def load_relay_models(models_dir: Path | None = None) -> dict[str, RelayModel]:
    ...   # None -> the packaged selfiles/data/relay_models

# pac-ct, at boot
selfiles.configure(data_dir=paths.DATA_DIR)   # user-supplied profiles win
```

This matters beyond tidiness: the DNP map editor's "Importar perfil DNP" writes
into `data/wordbits/` at runtime, so the app must be able to point the library
at a writable directory that is *not* inside the installed package —
particularly under the versioned install layout of Phase 5.

Then in `pac-ct`: `pacct/parsers/` and `pacct/core/` become re-export shims,
`pacct/web/**` imports are rewritten module by module, and the shims are deleted
once nothing references them. Tests move with their code, except the ones that
test app behaviour.

**Gate per library:** the moved tests pass in the library repo, and the full
828 still pass in `pac-ct` against the published package.

### 4.3 `sclread` — deferred, deliberately

`selfiles.scl` is kept as a self-contained subpackage with no imports from the
rest of `selfiles`, so the later split is a `git mv`. The reason to wait is in
REVIEW.md §4/L3: the `db:` `sAddr` grammar is SEL's convention living inside a
vendor-neutral format, and that seam should be designed before it is published
as a vendor-neutral library.

---

## Phase 5 — Versioning, distribution, auto-update

This is the original request, and the move to a public repo simplifies it: a
public release asset needs no token on the target machine, so the update
channel is GitHub Releases and nothing has to be embedded in the bundle.

### 5.1 Versioning rules

`VERSION` at the repo root is the single source; `pyproject.toml` reads it and
`pacct.__version__` re-exports it. Semantic versioning, with the boundaries
written for *this* project:

- **MAJOR** — a change to what gets written into a relay: the SET_D
  round-trip contract, the RDB write path, or a settings format. Also removing
  a tool or a supported relay family.
- **MINOR** — a new tool, a new relay model, a new theme, a new route; anything
  additive.
- **PATCH** — fixes, docs, dependency bumps.

First public release: **1.4.0**, continuing the `v1.1`/`v1.2`/`v1.3` line
already in the private repo. Say so in the release notes so the jump is not a
mystery.

Snapshots between releases are `1.4.0-dev+g<sha>` — never offered as an update.

### 5.2 What a distributable contains

```
pac-ct-1.4.0.zip
├── app.py  src/pacct/  selprotopy/  data/  tools/
├── config/config.ini.example
├── requirements.txt  VERSION  LICENSE  NOTICE.md
├── vendor/*.whl        ← pyserial, telnetlib3, olefile, openpyxl,
│                         py61850, cfbwrite, selfiles
└── INSTALAR.txt
```

`vendor/` is the point: a substation has no internet, which is the same fact
that already keeps `install_requirements` from running pip on every boot. Built
with `pip download -r requirements.txt --pre -d vendor/`, plus
`--platform win_amd64 --only-binary=:all:` for the Windows bundle (buildable
from Linux). `app.py` gains `--offline`, which prepends
`--no-index --find-links vendor/`.

### 5.3 Install layout, so an update cannot eat the engineer's data

```
PAC-CT/
├── pac-ct.cmd / pac-ct.sh      launcher → current/
├── versions/1.4.0/  1.5.0/     one folder per version, own .venv
├── current -> versions/1.5.0   junction on Windows
└── userdata/                   NEVER touched by an update
    ├── config.ini              the ACC/2AC passwords
    ├── cache/                  rdb/<sha256>, sessions/
    └── rdbs/
```

Requires one contained change in `paths.py`: a data root from
`PACCT_DATA_DIR`, defaulting to `PROJECT_ROOT` so a development clone behaves
exactly as today. Rollback is repointing `current`. This also settles where a
user-imported DNP profile is written (§4.2).

### 5.4 The build, and when it runs

`tools/build_dist.py` produces `dist/pac-ct-<version>.zip`, `manifest.json`
(version, sha256, size, commit, min Python) and `SHA256SUMS`.

- `pre-push` hook → builds the snapshot for what is being pushed.
- pushing a `v*` tag → builds the release and `gh release create`s it with the
  zip attached.

`dist/` is gitignored except `manifest.json`. Hooks are not versioned, so they
live in `tools/hooks/` with `tools/install_hooks.sh`, and CI is the backstop
that builds a release even if nobody installed the hook.

### 5.5 Auto-update

`pacct/update.py`, checking
`https://api.github.com/repos/GuilhermeMarini/pac-ct/releases/latest` —
anonymous, 60 requests/hour, no token anywhere.

Rules, all of which follow from facts already established in this project:

1. **Never on the boot path.** A check is `python3 app.py --atualizar` or a
   button on the home. Offline must be silent, not a failed start — the same
   rule as `install_requirements`.
2. **Verify before swapping**: sha256 from the manifest, checked against the
   downloaded zip, before anything is unpacked.
3. **Stage, then swap, then restart.** Unpack into `versions/<new>`, build its
   venv, repoint `current`, restart via the launcher. A running process cannot
   reliably replace its own loaded `.pyd`s on Windows, so the versioned layout
   is what makes this safe rather than clever.
4. **Only releases.** A `-dev+g<sha>` snapshot is never offered.
5. `userdata/` is never read, moved or migrated by the updater.

---

## Phase 5 as built — 2026-09-03

Everything in §5.1–5.5 landed as decided. Six things are worth naming, because
they are either a departure or a fact the plan did not have.

**Both libraries were published on 2026-09-03**, which retires the workaround
described immediately below. `cfbwrite 1.0.0` and `selfiles 1.0.0` are on PyPI
(cfbwrite first — selfiles depends on it), each tagged `v1.0.0` in its own
repository so the source the uploaded artefacts were built from is recorded.
`requirements.txt` and `pyproject.toml` are back to `cfbwrite>=1.0` /
`selfiles>=1.0`, which was always the one-line change. Verified before upload
against the exact artefacts, and again after: `pip install selfiles` into an
empty venv with `--no-cache-dir` pulls `cfbwrite` transitively, finds the 7
packaged relay models, and `pac-ct`'s 647 tests pass against the built wheels
with nothing editable and nothing from git.

**A blocker the plan did not price: neither library was on PyPI.** `cfbwrite`
and `selfiles` were extracted, committed and pushed in Phase 4, and
`requirements.txt` was left saying `cfbwrite>=1.0`. That resolves to nothing.
CI had been **red on every push since Phase 4** (`No matching distribution
found for cfbwrite>=1.0`), a fresh venv could not be built, and
`pip download -r requirements.txt` — which is how `vendor/` is filled — could
not run at all, so Phase 5 could not start on top of it. Both are now PEP 508
direct references pinned to a **commit**, which is the shape `py61850` carried
for the same reason while it was unpublished, and which
`tests/test_requirements.py` already knew how to judge. `pyproject.toml`
carries the identical pin and `tests/test_version.py` fails when the two
disagree. Each becomes `>=1.0` again in one line the day it is published.

**`VERSION` names the release being prepared, and the build adds the `.dev`.**
It went from `1.4.0.dev0` to `1.4.0`. The dev marker belongs to the build,
where the sha is known: `build_version()` stamps `1.4.0.dev0+g<sha>` into the
bundle's own `VERSION`, so an installed snapshot reports the commit it came
from rather than the release it was heading towards. A `VERSION` that already
said `.dev0` would make `--release` unbuildable and every snapshot read
`.dev0.dev0`.

**The snapshot is spelled the PEP 440 way.** `1.4.0-dev+g<sha>` (as §5.1 wrote
it) and `1.4.0.dev0+g<sha>` are the *same version* — PEP 440 normalises the
first into the second — and the second is what `pip` and
`importlib.metadata` report back. Writing the first would guarantee the bundle
and the installed package disagree on screen for no gain.

**The zip has a top-level `pac-ct-<version>/`**, which §5.2's sketch did not
show. It is what makes unzipping safe in whatever directory the engineer is
standing in, and it is the directory the updater stages into. `unpack()`
refuses an archive with any other shape — including one whose members climb
out of the destination, which is a real concern for a file fetched over the
internet and extracted with the caller's rights.

**The two halves of `requirements.txt` are fetched by different commands.**
`pip download --only-binary=:all:` refuses a direct reference (it would have to
build one), so `build_dist.py` splits by shape: direct references go through
`pip wheel --no-deps`, the rest through `pip download` with the platform flags.
Both libraries are pure Python, so the `py3-none-any` wheel built on Linux is
the same wheel the Windows bundle carries.

**The pre-push hook builds `--no-vendor`.** Fetching a wheel for every
dependency on every push needs the network and costs a minute, and the wheels
are not what breaks; what the hook is for is proving the bundle still *builds*
from this tree before a tag finds out. `PACCT_PREPUSH_VENDOR=1` asks for the
full one. CI runs the same source-only build as a step, because hooks are not
versioned and a machine that never ran `tools/install_hooks.sh` must still not
be able to cut a release whose bundle does not build.

**The repository has its own `.venv` now.** Until this phase it borrowed the
archive repo's, which is where `cfbwrite` and `selfiles` were installed
editable — the only reason anything ran at all while `requirements.txt` was
unresolvable. It is built from the pins, exactly as CI builds one, which is
also what proved the pins work: 639 tests, `ruff` and `mypy` green in it with
the two libraries installed as regular wheels rather than editable checkouts.
When working *on* a library, `pip install -e ../selfiles` is one command away.

**`dist/snapshot/` is where the hook writes.** `dist/manifest.json` is the one
path under `dist/` that git tracks, and it describes a *published* release; a
hook that rewrote it on every push would leave the tree dirty immediately after
each push, describing a zip that exists on one machine.

What Phase 5 added, by file:

| File | What it is |
|---|---|
| `src/pacct/version.py` | The SemVer rules for *this* project, the snapshot spelling, and `is_newer` — which is the whole of rule 4 |
| `src/pacct/update.py` | `Layout` (versions/current/userdata), check, download, verify, unpack, venv, swap, rollback, restart |
| `tools/build_dist.py` | The offline bundle, `manifest.json` and `SHA256SUMS` |
| `tools/dist/` | `pac-ct.sh`, `pac-ct.cmd`, `INSTALAR.txt` — what a bundle carries for the engineer |
| `tools/hooks/pre-push`, `tools/install_hooks.sh` | The build trigger, and the installer hooks need because git does not version them |
| `.github/workflows/release.yml` | A `v*` tag builds both bundles and publishes them; the tag must match `VERSION` |
| `app.py` | `--versao`, `--offline`, `--instalar`, `--atualizar`, `--reverter` |
| `tests/test_version.py`, `test_build_dist.py`, `test_update.py` | 44 tests; every one of the updater's five rules is pinned |

`paths.py` needed **no change at all** — the three-root split done in Phase 1
(`PACKAGE_DIR` / `PROJECT_ROOT` / `DATA_ROOT`, with `PACCT_ROOT` and
`PACCT_DATA_DIR`) is exactly what the launcher sets, and
`tests/test_update.py::test_the_paths_module_reads_the_two_variables_the_launcher_sets`
is now the thing that says so out loud.

**Python 3.13 and 3.14 were added to the matrix, and measured rather than
assumed.** CI ran 3.10–3.12; the floor `requires-python` declares is 3.10 and
the newest release is 3.14, so three of five supported versions were covered.
3.13 is the one that had to be there: it is where `telnetlib` was removed from
the standard library, and the vendored `selprotopy` still does a bare
`import telnetlib` — `pacct/compat.py` covers it, and a matrix jumping 3.12 to
3.14 would never exercise the boundary where that starts mattering. Measured
locally on **3.14.6**: 647 tests, `ruff`, `mypy`, a bundle build and all eleven
screens, all green — and the `DeprecationWarning` 3.12 emits is simply absent
there, because the shim takes the backport path instead of the stdlib one.
Three tests now keep the matrix, `requires-python` and the classifiers from
drifting apart, including one that refuses a *hole* in the matrix.

The one part that cannot be verified here: the Windows half — the `mklink /J`
junction, `pac-ct.cmd`, and a venv built from `win_amd64` wheels — has been
written against the documented behaviour and reviewed, but there is no Windows
machine in this environment. It needs one pass on Windows before 1.4.0 is
tagged.

---

## Phase 6 — Verification

The gates, in order. Nothing proceeds past a red one.

1. `pytest` — 828 passing, both before and after each phase.
2. The golden SVG (`tests/fixtures/render_page.svg`) — regenerate only with
   `SEL_UPDATE_GOLDEN=1`, and **read the diff**. Phase 3's D1 fix changes it by
   exactly one CSS rule; S1 later will change it more, and that diff is the
   review.
3. `set_dnp.parse(b).serialize() == b` over every SET_D in the reference RDBs
   (`tools/check_set_dnp_roundtrip.py`).
4. `ole_rebuild` / `cfbwrite` self-verification on a real 140 MB RDB, before
   and after extraction, comparing stream-by-stream.
5. `tests/test_relay_models.py::test_the_two_model_registries_agree_or_say_why_not`
   — the three data registries must still agree, or say why not.
6. Manual, because the web tools have no unit tests (S2 is why): launch
   `python3 app.py --web` and exercise each of the seven screens once.
7. Needs a bench relay, and cannot be verified otherwise — say so rather than
   claim it: GLV telnet polling (4xx/7xx/3xx), MMS connect and read, and the
   D3 clock fix under a real Fast Message deadline.
