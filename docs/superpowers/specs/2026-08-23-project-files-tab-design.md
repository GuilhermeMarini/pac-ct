# Arquivos do Projeto — a single upload surface for the toolkit

*2026-08-23 — design*

## Problem

Every tool in the toolkit owns its own upload panel. Six screens carry a
near-identical `.upload-zone` (markup, CSS and JS), six handlers carry a
near-identical `/rdb-upload` or `/scd-upload`, and each keeps its own idea of
which file the visitor is working on. The consequences:

- **The same file is uploaded once per tool.** Comparing settings and then
  editing the DNP map of the same substation means sending the same 40–140 MB
  RDB twice, over a substation LAN. The extraction is reused
  (`cache/rdb/<sha256>/` is content-addressed), but the *transfer* is not, and
  neither is the visitor's patience.
- **SCDs are not deduped at all.** `vb_updater` and `vlan_mapper` each write
  the uploaded SCD into their own `cache/sessions/<sid>/<tool>-scd/`, keyed by
  filename. Two uploads of the same SCD are two files.
- **There is no place that answers "what is this project made of?"** The set of
  files in play is scattered across six tool states.
- **The upload panel is copied code.** Six copies of the drop zone, six copies
  of the size ceiling, six copies of the `X-Filename` decode dance — and every
  new tool copies them again.

## Solution

One tab, **Arquivos do Projeto**, mounted at `/arquivos/`, placed in the
navigation immediately after Menu. It is the only screen in the toolkit that
accepts an RDB or an SCD. The other six tools lose their upload panels and gain
a picker over what the tab already holds.

### Decisions taken up front

| Question | Decision |
|---|---|
| Lifetime and reach | **Per-visitor session**, exactly like today: 8 h idle TTL, wiped at boot, invisible to other visitors. Reuses `cache/sessions/<sid>/` and the existing sweeper. |
| File kinds | **RDB and SCD only.** |
| How tools get a file | **Per-tool picker** where the upload zone was. Each tool keeps its own selection. |
| Place in the catalogue | **Navigation entry, like Menu** — not a tenth entry in `items.TOOLS`. It is the input surface, not a commissioning tool. |
| Structure | **New package** `sellib/web/project_files/`, mounted at `/arquivos/`; `mount.py` serves `GET <prefix>/library` for every mount. |

Deliberately **out of scope**: the XLSX round-trips (`gle_exporter:/import`,
`vb_updater:/import-descriptions`) and `dnp_map:/import-profile`. The first two
take back a spreadsheet the tool itself just produced and the visitor edited —
a one-shot file, not a project input; routing it through the library would add
a save-and-re-upload step for no gain. The third writes project-wide reference
data into `data/wordbits/`, which is not session state at all.

## Architecture

```
sellib/web/project_files/
    __init__.py          re-exports build_project_files_handler
    library.py           FileLibrary, FileEntry — pure logic, no HTTP
    handler.py           GET /  ·  POST /upload  ·  POST /remove
    templates/library.html
```

`handler.py` exposes `build_project_files_handler(logger, sessions) -> type`
and opens no socket, like every other tool; `dashboard.py:main()` adds one
`Mount("/arquivos", ..., "Arquivos do Projeto")` to the list it already builds.
The handler's own `session_key` is `"arquivos"` — the same key the library
lives under, which is intentional: the tab's state *is* the library.

Four shared pieces change:

- `sellib/web/mount.py` — serves `GET <prefix>/library` for every mount, beside
  the existing `/progress`, `/theme.css` and `/static/...`; injects the
  `SelLibrary` client runtime the way `inject_progress_runtime` already injects
  `SelProgress`.
- `sellib/web/session.py` — `SessionHandler.library()`, the shared accessor.
- `sellib/web/themes/items.py` — `FILES_ITEM`, beside `MENU_ITEM`; the three
  direction modules render it.

### `library.py` — the model

```python
@dataclass
class FileEntry:
    sha256: str
    kind: str              # "rdb" | "scd"
    display_name: str      # the name THIS upload carried
    size: int
    uploaded_at: float
    rdb: "RdbInfo | None"  # kind == "rdb"
    scd_path: "Path | None"  # kind == "scd"
    detail: str            # "12 relés" / "31 IEDs" — for the listing
```

`FileLibrary` holds `dict[str, FileEntry]` keyed by sha256, in insertion order,
and lives in `Session.data` under the key `"arquivos"` — **shared by every
tool**, which is the point. It is reached through
`library_for(sessions, session)`, a function taking the session; never a
module-level singleton (`sellib/web/session.py`'s standing rule: module-level
singletons are per-process, not per-visitor, and that is a bug).

### Storage

- **RDB bytes are never copied into the session.** `rdb.process_upload()`
  already writes `cache/rdb/<sha256>/source.rdb` and extracts beside it; the
  entry holds the returned `RdbInfo`. A second visitor uploading the same bytes
  gets their own `FileEntry` over the same shared extraction, and their own
  `display_name` — which is why `display_name` lives on the entry and not in
  the cache.
- **SCD bytes go to `session.subdir("files")/<sha12>.scd`.** Note
  `Session.subdir`, **not** `SessionHandler.sdir` — the latter prefixes the
  caller's `session_key`, which would hand each tool a different directory and
  defeat the whole design.
- Removal deletes the SCD file and drops the entry. The RDB cache entry is left
  alone: it has no owner, is shared between visitors, and is swept by age
  (`rdb_cache.sweep()`).

### Dedup

sha256 over the raw request body, computed before anything is written.

- **Same sha already in the library** → no second entry. The response is
  `{"ok": true, "duplicate": true, "entry": {...}}` and the page highlights the
  existing row: *"já está no projeto (enviado como `<nome>`)"*. The first name
  wins; a re-upload never renames an entry another tool may already be
  displaying.
- **Same name, different bytes** → two entries. The listing disambiguates by
  sha prefix and size, so the visitor can tell `projeto.rdb` from
  `projeto.rdb`.

For RDBs this is a second dedup layer, not a duplicate one: `process_upload`
dedups the *extraction* across visitors and restarts; the library dedups the
*entry* within one visitor's project.

### Validation

Kind comes from the extension (`.rdb` → rdb; `.scd`/`.xml` → scd) and is then
confirmed by actually parsing: `process_upload` for an RDB (it raises on a
non-OLE file), `scd_loader.load_scd` for an SCD (plus the existing "nenhum IED
encontrado" check). A file that fails validation is rejected and never enters
the library — no half-entries.

The size ceilings move here as their single definition (`RDB_MAX_BYTES` 500 MB,
`SCD_MAX_BYTES` 200 MB); the six per-tool copies leave with their handlers.

### The screen

One table — nome, tipo, tamanho, sha curto, conteúdo (`N relés` / `N IEDs`),
enviar, remover — under a drop zone that accepts both kinds.

Upload goes through `SelProgress.upload()`, never `fetch()`: `fetch` cannot
report upload progress and an RDB is 40–140 MB. `process_upload`'s
`on_progress` drives the stages, as it does today in all six tools.

The page markup carries `<!--NAV:arquivos-->` as the **first child of
`<div class="shell">`** — not inside `<header>`. In régua, `.shell` is a
two-column grid whose first column *is* the nav; a marker left in the header
collapses the page to ~200 px.

### Navigation in the three directions

`items.py` gains, beside `MENU_ITEM`:

```python
FILES_ITEM = ("arquivos", "/arquivos/", "Arquivos do Projeto",
              "Arquivos", "RDB e SCD do projeto")
```

Tool numbering stays `1..9` in all three directions. This is not cosmetic:
régua's home cards read "Borne *i* · ligado" and must keep matching the strip,
and the home renderers index `TOOLS` from 1. So Menu keeps `0` and Arquivos
takes **`A`** in the number slot:

- **folha** — `.toc`, after the Menu link: `<span class="n">A</span>Arquivos do
  Projeto`. `_link()` grows a `str` number slot.
- **caderno** — `.tabs`, after the Menu tab, `<span class="n">A</span>Arquivos`.
  `_tab()` likewise; `{n:02d}` keeps formatting the integers.
- **régua** — régua has **no Menu borne by design** (home is reached through
  "← Menu" in the top bar), so without special handling the tab would be
  unreachable there. It gets an `A` borne at the top of `.strip`, above borne 1,
  using the existing `.borne` markup.

The home gains a one-line pointer to the tab, since Arquivos is not one of the
cards.

## The six tools

`mount.py` serves `GET <prefix>/library` → the session's entries as JSON,
filtered by `?kind=`. Serving it centrally, like `/progress`, means no tool
needs a new GET route, and it answers under every mount prefix.

A `SelLibrary` client runtime is injected into every page beside `SelProgress`,
exposing `SelLibrary.picker(el, {kind, multi, onPick})`. One picker
implementation, not six: the empty state ("Nenhum RDB no projeto — envie em
Arquivos do Projeto", linking to `../arquivos/` — a **relative** link, since the
`fetch` shim cannot reach a cross-page `href`) is written once, and a seventh
tool gets it free.

Each tool loses its upload zone — markup, CSS and JS — and swaps one POST:

| Tool | Removed | Added |
|---|---|---|
| `settings_compare` | `/rdb-upload` | `/select-rdb {sha}` → `_register_rdb(...)`; picker is multi-add (up to 7) |
| `vb_updater` | `/rdb-upload`, `/scd-upload` | `/select-rdb`, `/select-scd`, both ending in `_maybe_match(st)` |
| `vlan_mapper` | `/scd-upload` | `/select-scd` → keeps `_build_payload(path, name)`, which is the tool's real work |
| `gle_exporter` | `/rdb-upload` | `/select-rdb` |
| `dnp_map` | `/upload` | `/select-rdb` |
| `glv` (`/glv/novo`) | `/rdb-upload` | `/select-rdb` |

Every `/select-*` body is the **tail of the old upload handler** — everything
after `process_upload` / `load_scd` returned. The per-tool diff is therefore a
deletion plus a sha lookup, and each tool's own state (`st.rdb`, `st.rdbs`,
`st.scd_path`, `st.payload`) keeps exactly the shape it has today. Extraction
already happened at upload time, so selection is fast; the job reporter stays
where a tool still does real work on select (vlan_mapper's payload, vb_updater's
match).

`vb_updater` and `vlan_mapper` set `st.scd_path` to the library's
`<sha12>.scd`. Both only ever read it, so sharing one path between two tools is
safe; derived outputs continue to go to `self.sdir("out")`, and `/download`
stays sandboxed there.

**Removal vs. live state.** A tool holds a resolved `RdbInfo` / path, not a
lookup through the library. Removing a file from the tab therefore changes the
listing, not a tool that already selected it — a page mid-comparison does not
break under the visitor. The file returns to the tab with one upload.

## Error handling

| Case | Behaviour |
|---|---|
| Empty body / over the ceiling | 400 / 413, nothing written, nothing registered |
| Unknown extension | 400, "tipo não reconhecido — envie .rdb ou .scd" |
| Extension says RDB, bytes are not OLE | 400 with the parser's message; no entry |
| SCD parses but has no IED | 400, "nenhum IED encontrado no SCD" (today's message) |
| Duplicate sha | 200 `duplicate: true`, existing row highlighted |
| `/select-* ` with an unknown sha | 404, "arquivo não está mais no projeto" |
| Two visitors upload the same RDB at once | `rdb_cache.lock_for(sha)` already serialises the extraction; the second reuses it |

## Testing

`tests/test_project_files.py` covers `library.py` as pure logic — no HTTP, no
session, matching how `tests/` already treats `dnp_map`'s model:

- a second upload of identical bytes returns the existing entry and does not
  grow the library;
- the same name with different bytes yields two entries;
- kind detection by extension, and rejection of an unknown extension;
- an invalid RDB and an IED-less SCD leave the library empty;
- removal drops the entry and unlinks the SCD file, and leaves the RDB cache
  entry in place;
- `library_for` returns the same object for two different `session_key`s on one
  session, and different objects for different sessions.

The web layer has no unit tests here, per the project's convention. It is
verified by launching `python3 app.py --web` and exercising, in all three
themes: upload of an RDB and an SCD, a duplicate upload of each, removal, and
then a pick in each of the six tools. GLV's picker is verifiable without
hardware; GLV's polling is not, and is untouched by this work.

## Documentation

`docs/ENGINEERING-NOTES.md` gotchas currently describe the per-tool upload panels and the
landing page's RDB upload. They are updated in the same change: the upload
surface is one screen, `/library` joins `/progress` and `/theme.css` as a
dispatcher-served route, and `Session.subdir` vs `SessionHandler.sdir` earns a
line of its own, since getting it wrong silently un-shares the library.
