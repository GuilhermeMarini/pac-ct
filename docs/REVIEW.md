# PAC CT — code review before the public migration

Reviewed: the whole first-party tree at `dnp-map-copiar` (`f975396`) — 24.312
lines across 95 `pacct/` modules, `app.py` and `tools/`. `selprotopy/` is
vendored and hook-protected, so it was read but not judged.

**Baseline:** 828 tests pass (`.venv/bin/python -m pytest tests/`, 14.9 s).

The code is in good shape. There are **no bare `except:`**, no wildcard
imports, no mutable default arguments, no module-level mutable singletons in
tool state, 3 uses of `os.path`, and zero relative imports. The layering is
real: `core/`, `parsers/` and `matchers/` import nothing from `web/`. Most of
what follows is consistency, duplication and typing — plus five defects, one of
them visible on screen today.

Findings are ranked by consequence, and every measured claim says how it was
measured.

---

## 0. Gate before anything is published

### G1 — The two sample files carry a real substation's identity

`samples/substation_demo.rdb` (41 MB) and `samples/substation_demo.scd`
(22 MB) are **not anonymised**, and the `.gitignore` comment that calls the SCD
"um SCD anonimizado" is inaccurate.

Measured by opening both:

| | |
|---|---|
| Relays / IEDs | 30 in the RDB, 32 in the SCD |
| Names | `QPC1_LT2_UPC1_UHE_ALFA`, `QPC1_LT3_UPC1_BETA`, `QPC1_LT1_UPC1_SECC_GAMA` — these name installations |
| Addresses | 30 distinct real control-network IPs (`192.0.2.7`…`192.0.2.90`, `203.0.113.44/45`) |
| Credentials | **None.** The only password-shaped keys are `EACC,0` / `EACC,1`, which are flags |

Publishing them publishes a utility's bay naming convention and the IP plan of
its relay control network. That is client engineering data; whether it may be
published is not a call this review can make.

It is not confined to the two files. The same names appear in tracked source:
`pacct/core/relay_conn.py`, `tests/test_rdb_write.py`,
`data/relay_models/SEL-311C.json`, `fixtures/gle_sem_61850.txt`,
`fixtures/model_missing.txt`, `docs/superpowers/**`, and
`cache/highlights_QPC1_TR1_UPC1.json` (tracked, and it is one relay's
annotations).

And they are load-bearing: **8 test modules** read `samples/` —
`test_gle_render`, `test_rdb_extract`, `test_rdb_write`, `test_scd`,
`test_scd_saddr`, `test_mms_map`, `test_relay_scd_match`, `gle_fixtures`.
Dropping the files silently costs a large part of the suite.

Three ways out, in `MIGRATION.md` §1 as a decision:

1. **Anonymise** — rewrite relay names and IPs to RFC 5737 documentation
   addresses. The tooling exists in this very repo (`parsers/ole_rebuild.py`
   rebuilds an RDB at any size and verifies its own output), and the config
   model already uses `192.0.2.10` for exactly this reason. Cost: the tests
   that assert on names need updating in the same commit.
2. **Leave them out of the public repo** and skip the tests that need them —
   the pattern `tests/test_mms_tables.py` already uses for the ICD corpus
   (`pytest.skip("fixtures/ICD files/... indisponivel localmente")`).
3. **Publish as-is** — only if that data is yours to publish.

Also excluded from the public repo regardless: `docs/*.pdf` (102 MB of SEL
instruction manuals) and `docs/*.zip` (16 vendor DNP3 profile bundles). Those
are SEL's copyrighted material. The *derived* `data/wordbits/*.json` is ours
and stays.

---

## 1. Defects

> **All five are fixed** in this repository, each with a test that fails
> without the fix (verified by reverting it). See `MIGRATION.md` Phase 3.

### D1 — Counter blocks render as black boxes *(confirmed)*

`parsers/gle.py` draws `PCN` (4xx counters) and `COUNTER` (7xx SELOGIC
counters) with `css_class="element-counter"` (lines 777 and 824). That class is
**defined nowhere** — not in `gle.py`'s `CSS`, not in any template, not
anywhere in the repo. An SVG `<rect>` with no `fill` declared defaults to
**black**, and the label drawn over it is `fill: #222`.

Confirmed by rendering a real corpus page:

```
rdbs/extracted/SE EXEMPLO III_R0a_180826/.../QPC4_TR1_UPC2/Misc/GL1.gle
page "CONTADORES EST 1"
<rect class="element-counter" x="720" y="96" width="66" height="78"/>
'.element-counter' in CSS -> False
```

**8 of the 418 corpus GLEs** contain `type="COUNTER"`, and the page above
exists to show counters. `data/relay_models/SEL-751.json` also declares
`"css_class": "element-counter"` for its `COUNTER` block, so both sources agree
on a name that has no rule — which is finding S1 made visible.

### D2 — One malformed MMS table breaks every model, then lies *(confirmed)*

`core/mms_tables._load()` does `json.loads(path.read_text())` and `raw["part"]`
with no error handling, unlike `wordbits._load_one` and
`relay_models._load_one`, whose docstrings state the policy: *"one bad file
must never take the rest of the registry down with it."*

Worse than raising once: `_load()` guards with `if _CACHE:` (truthiness, not a
loaded flag), so the entries read before the bad file stay cached.

Reproduced with two real tables and a broken file sorting between them:

```
1st call RAISED: JSONDecodeError
2nd call, 411L -> found
2nd call, 751  -> None        <-- silently missing
cache keys after the failure: ['411L']
```

So the first GLV MMS connect fails with a stack trace, and every one after it
comes up with a half-loaded registry and no warning — the 751 simply has no
table any more. Fix: per-file try/except with a warning (matching the other two
registries) and a `_LOADED` flag instead of truthiness.

### D3 — The CLI poller still measures time on the wall clock

`ENGINEERING-NOTES.md` documents this bug class in detail, with the measurement: on this
machine the WSL wall clock sat **82,5 s** behind the host and resynced, so
`time.time()` deltas went negative and a poll loop slept 82 seconds while
Wireshark showed a perfect metronome. Every duration in the web polling path
was moved to `time.monotonic()`, and `tests/test_mms_transport.py` pins it.

`pacct/cli/runner.py` was not: **~20 sites**, all durations, all wall clock —
`deadline = time.time() + max_drain`, `while time.time() < deadline`,
`t_fm = time.time() - t0`, `if not chunk and time.time() - last_data > 0.15`.
A backward jump makes a Fast Message read wait ~82 s past its deadline; a
forward jump expires it instantly.

It is the CLI path, which needs a bench relay to exercise end to end — but the
substitution itself is mechanical, and the rule from ENGINEERING-NOTES.md is unambiguous:
the wall clock survives only where it means an hour of the day.

### D4 — `rdb_cache._LOCKS` grows without bound

`lock_for(sha)` memoises a `threading.Lock` per sha256 and nothing ever removes
one — not even `sweep()`, which deletes the cache entry the lock guarded. A
long-running server accumulates one lock object per RDB ever uploaded. Small,
but it is the only unbounded structure in the process.

### D5 — Request bodies are read with no ceiling

`session._read_json_body`, `mount._serve_theme_choice` and six routes in
`vb_updater` / `gle_exporter` / `settings_compare` / `dnp_map` read
`Content-Length` bytes straight into memory with no maximum. `project_files`
got this right (chunked, with the 500 MB / 200 MB ceilings from
`library.py`). On a substation LAN the exposure is low, but the fix is one
shared helper.

---

## 2. Efficiency, measured

### E1 — The VLAN Mapper parses the same 22 MB SCD three times

`parsers/scd.py` exposes five entry points and **each one re-parses the file**.
`vlan_mapper.compute_ied_vlan_rows` calls three of them on the same path:

| call | cost |
|---|---|
| `load_scd` | 624 ms |
| `extract_gse_communication_map` | 429 ms |
| `extract_goose_subscriptions_by_ied` | 476 ms |
| **total** | **1 529 ms** |
| bare `ET.parse` of the same file | 363 ms |

So ~1,1 s of the 1,5 s is the same bytes parsed twice more.
`tools/mms_tables_from_wordbits.py` pays it twice (`sel_da_fcs` 975 ms +
`sel_short_addresses` 1 104 ms). A `ScdDocument` holding the parsed root, with
the five functions as methods, removes it without changing any caller's
semantics.

### E2 — `gle.element_info()` is recomputed about four times per element

Per rendered page, `element_info(el)` runs in `page_bounds`, again in
`group_bounds` for grouped elements, again in `render_page`'s port-modifier
pass, and again in its element pass. It re-reads the XML attributes and rebuilds
four dicts each time. `ENGINEERING-NOTES.md` measured a full client repaint at 0,80 ms and
deliberately left client waste alone; this is the server side of the same page
and one `dict` per element would fix it. Low priority — no user waits on it
today.

---

## 3. Structure

### S1 — Two sources of truth for how a GLE block is drawn

`data/relay_models/*.json` declares, per block: `css_class`, `geometry`
(`min_width`, `min_height`, `port_first_offset_y`, `port_spacing_y`),
`default_ports`, `min_ports`, `sublabels`, `output_sublabels`, `label_glyph`,
`label_source`, `output_bit_pattern` and `evaluation`.

`parsers/gle.py` hardcodes the same knowledge in `PORT_FIRST_OFFSET`,
`ELEMENT_MIN_SIZE`, `DEFAULT_PORTS` and a 30-branch `render_element()`
if-chain, with one `render_*` function per block type repeating the bit-suffix
rule (`name + "Q"`, `name + "T"`, `name + "QU"`, `name.lstrip("_")`) that
`output_bit_pattern` already states.

`relay_models.BlockDef` even carries the JSON's extra fields — and its own
docstring says they are kept *"pra leitura informacional ou refactor futuro"*.
Nothing consumes them. `render_page()` receives the `relay_model` and uses it
only for `analog_group_for()`.

D1 is the drift between the two arriving on screen. This is the highest-value
refactor in the review: one `BlockDef`-driven renderer, the if-chain becomes a
dispatch table for the handful of genuinely custom shapes, and the golden SVG
(`tests/fixtures/render_page.svg`) is the guard rail that makes it safe.

### S2 — The routes live inside closures, which is why the web tools have no tests

Every tool exposes `build_*_handler(logger, sessions) -> type`, and the handler
class is declared *inside* that function:

| module | lines | in the factory closure |
|---|---|---|
| `dnp_map/handler.py` | 689 | **633** (92%) |
| `glv/handler.py` | 683 | 599 |
| `vb_updater/__init__.py` | 1 439 | 482 |
| `gle_exporter/__init__.py` | 935 | 256 |
| `settings_compare/__init__.py` | 748 | 127 |

Nothing inside is importable, so no route can be unit-tested — which is exactly
what `ENGINEERING-NOTES.md` records as a limitation ("the web tools themselves have no unit
tests"). It is not inherent: `Mount` already injects `mount_prefix` as a class
attribute, so `logger`/`sessions`/`defaults` can be injected the same way and
the classes can live at module level. The factory keeps its signature.

### S3 — Three tools never got the package split the other three did

`glv/`, `dnp_map/` and `project_files/` are packages with the concerns
separated (`model.py`, `export.py`, `handler.py`, `templates/`). The other
three are single files mixing GLE parsing, SCD parsing, XLSX build/parse, RDB
writing, session state, HTML rendering and routes:

- `vb_updater/__init__.py` — 1 439 lines, 27 top-level defs, 5 concerns
- `gle_exporter/__init__.py` — 935 lines
- `settings_compare/__init__.py` — 748 lines

The recommendation is to finish the pattern already chosen, not to invent one.

### S4 — `session.py` and `mount.py` import each other

`session.py` does `from pacct.web.mount import inject_head` at module level;
`mount.py` does `from pacct.web.session import build_cookie` **inside**
`_dispatch()` to break the cycle at runtime. It works, and it is a sign the
seam is in the wrong place: `inject_head`, `_resolve_markup` and the two
injected scripts are HTML rendering, not routing. Moving them to
`pacct/web/inject.py` removes the cycle and shrinks `mount.py` to dispatching.

### S5 — Three per-model registries, three hand-rolled loaders

`relay_models`, `wordbits` and `mms_tables` all do: glob a directory, parse
JSON per model, normalise a RELAYTYPE into lookup keys, memoise, expose
`lookup()` / `invalidate()`. Each implements all five differently — including
**two different `_key_variants()`** with different semantics
(`relay_models` strips one suffix group by regex; `wordbits` strips
progressively). `ENGINEERING-NOTES.md` documents the *data* drift and
`tests/test_relay_models.py` guards it; the *code* drift is unguarded, and D2 is
one registry lacking a robustness rule the other two state explicitly.

A shared `pacct.core.registry` (load, key-normalise, cache, invalidate) with
three thin subclasses removes about 120 duplicated lines and makes a policy fix
apply once. `set_dnp.same_model`'s deliberate strictness stays exactly as it is
— that one is a documented, measured difference, not drift.

### S6 — `pacct.paths` is the only thing blocking library extraction

Measured from the AST import graph: **22 modules import nothing first-party**,
and everything in `core/`, `parsers/` and `matchers/` is web-free. The whole
coupling to the application is that the registries reach for
`pacct.paths.WORDBITS_DIR` / `RELAY_MODELS_DIR` / `MMS_MAP_DIR` / `RDB_CACHE_DIR`
at import time. `relay_models.load_relay_models(models_dir=...)` already takes
the directory as a parameter and shows the shape: the library accepts a path,
the application supplies it.

### S7 — Stale names in code

- `app.py:224` — `description="Launcher SELProtoPy (venv + deps + CLI/dashboard)"`
- `cli/runner.py:2` and `:525` — "Exemplo de uso da biblioteca SELProtoPy", "CLI do SELProtoPy"
- `examples/relay_models/` in four docstrings (`core/relay_models.py` ×2,
  `matchers/relay_scd.py`, `parsers/rdb.py`) — the directory is
  `data/relay_models/`
- `pacct/__init__.py` — `__version__ = "0.1.0"` and "SEL commissioning toolkit"

`corrections_plan.md`, `docs/superpowers/**` and `mockups/` also predate the
rename and are deliberately left alone, as ENGINEERING-NOTES.md says.

### S8 — Modernisation backlog (the "full modernisation" scope)

- **49** `logger.info(f"...")` calls format eagerly (ruff `G004`).
- `Optional[X]` and `"X | None"` are mixed, and quoted annotations are
  everywhere despite `from __future__ import annotations` making them
  unnecessary.
- Dataclass fields typed as bare `dict` / `tuple` / `object`:
  `RelayModel.analog_groups`, `analog_name_aliases`, `WordbitSet.kinds`,
  `check_kinds`, `MmsTable.bits`, `ScdPoint.rule`, `BitRule.alternatives`,
  and `scd.sel_da_fcs() -> dict`. These are the main mypy blockers.
- `element_info()` returns an untyped `dict` threaded through eight functions
  in two modules — the natural target of S1's refactor.
- `parsers/gle.py` ends in an `argparse` `main()`; in a library that becomes a
  console-script entry point.
- `relay_models._VALID_FAST_READ` is defined inside `_load_one()`;
  `wordbits.entry_from_profiles` imports `datetime` inside the function;
  `mount.serve` imports `threading` inside the function.
- No `pyproject.toml`, no `py.typed`, no lint or type configuration, no CI.

---

## 4. What should become a library

From the import graph, the honest boundaries:

### L1 — `cfbwrite` (from `parsers/ole_rebuild.py`) — strongest candidate

511 lines, one dependency (`olefile`), **zero domain knowledge**, 326 lines of
tests already. It writes a complete MS-CFB v3 container, verifies its own
output stream-by-stream before handing it over, and writes atomically. It
exists because `olefile.write_stream` can only replace a stream with one of
exactly the same size — a limitation anyone touching `.doc`, `.xls`, `.msi` or
`.rdb` files in Python runs into. The one piece here with an audience outside
this project.

### L2 — `selfiles` (SEL AcSELerator file formats) — the domain library

`parsers/{rdb, rdb_cache, sel_settings, set_dnp, gle, dnp_profile}` +
`core/{relay_models, settings_model, settings_catalog, selogic_parser,
logic_compare, wordbits}` + `matchers/relay_scd`. ~5 500 lines, all web-free
today. Depends on L1 for the write path. Needs S6 (inject the data directory)
and, ideally, S1 first — extracting a renderer that contradicts its own
registry would publish the contradiction.

### L3 — `sclread` (IEC 61850 SCL) — real, and I recommend deferring it

`parsers/scd.py` + `core/mms_tables.py`, ~770 lines: SCL reading, GOOSE/VLAN
extraction, ExtRef subscriptions, `DataTypeTemplates` FC resolution, and the
`sAddr` grammar. Genuinely vendor-neutral, which is what the toolkit already
claims for its 61850 tools. Deferred because the `db:` `sAddr` grammar is SEL's
convention inside SCL, so a clean split needs a vendor seam designed first —
and because a fourth repo is a fourth release process for one maintainer.
Ships as `selfiles.scl` now, extractable later without moving code again if the
subpackage boundary is respected.

### Not a library

`web/{progress, session, mount, themes}` is a competent little framework
(~1 700 lines) and it is reusable — but only inside this project. Extracting it
buys a release process and costs the ability to change it freely. `web/glv/` is
an application, not a library.

---

## 5. What is already right, and should not be "improved"

Worth stating, because a modernisation pass can easily undo these:

- `set_dnp.parse(b).serialize() == b` — byte-for-byte round-trip on a file that
  goes back into a protection relay. `RawLine` keeps the raw bytes and the
  `0x1C` terminator for exactly this. Do not "simplify" it into a dict.
- `ole_rebuild.rebuild()` verifying its own output, and `rdb_write` refusing to
  pad XML to fit. `tests/test_rdb_write.py::TestNoPaddingPathCameBack` guards
  the removed path.
- `mms_tables.decode_bit` returning `None` instead of a value for an
  unrecognised Dbpos: `bool("00")` is `True`, and the alternative is a breaker
  painted closed while open.
- `wordbits.check()` returning `"ok"` for any block the data cannot judge, and
  the measured `check_kinds` policy behind it.
- The lock split in `glv/link.py` (`_lock` lifecycle vs `_discovery_lock`), and
  `_poll_gave_up` taking `_lock` with a 0,25 s timeout instead of blocking.
- Infrastructure routes answering via `SessionManager.peek()` so a stylesheet
  never mints a session.
