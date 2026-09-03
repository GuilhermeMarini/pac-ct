# v2.0 — Relay Driver Migration Plan

**Status:** draft / pre-approval
**Drafted:** 2026-05-22
**Owner:** GuilhermeMarini

## Motivation

The app is steadily moving toward family-specific behavior (SEL-3xx / 4xx / 7xx)
diverging across nearly every tool. Today that divergence is expressed by
`if family == "4xx" ...` branches and a `Family` literal type threaded through
~14 files. Adding a new relay family — or even a new variant within a family —
requires touching code in many unrelated modules.

The v2.0 goal: each relay family owns a **driver module**. The rest of the app
calls a stable interface and never knows what family it is talking to. Changes
inside a family stay inside that family's directory.

## Audit — where family branching lives today

Branching is concentrated in **four capability layers**, not sprawled randomly.

### A. Settings normalization
`pacct/core/settings_model.py`, `pacct/core/settings_catalog.py`, consumed by
`pacct/web/settings_compare.py`.

- `family_from_relaytype()` — regex on RELAYTYPE → `"3xx" | "4xx" | "7xx"`.
- `dialect_for_family()` — symbolic (3xx) vs keyword (4xx/7xx).
- `groups_for_family()` — which Cfg.txt files exist for the family.
- `_SITM_RE` / `_SER_RE` (4xx only), `_ALIAS_RE` (7xx only).
- `_NORMALIZERS = {"3xx": ..., "4xx": ..., "7xx": ...}` — **already a
  strategy table keyed by family**.
- Three `_Nxx_normalize_section()` functions plus `_4xx_role_of()` (4xx-only).

### B. Live relay communication
`pacct/core/target_region.py`, `pacct/web/dashboard.py`,
`pacct/core/relay_models.py:RelayModel.fast_read`.

- `fast_read = "target_region"` (4xx Fast Message) vs `"fast_meter"`
  (7xx A5D1 banks).
- `AsciiTargetReader` is 4xx-only; the Fast Meter path is inline in
  `dashboard.py`.
- `RelayModel.uses_target_region()` is the **existing seam** — the
  dashboard already branches through it.
- Analog name aliasing (SEL-487E `IAS → IA1`) is already routed through
  `RelayModel.resolve_analog_name()`.

### C. GLE bit naming + block visuals
`pacct/parsers/gle.py`, `pacct/web/gle_exporter.py`.

- Block-type tables hard-code both families: `PLT/PCNDTIMER/PCN/ALT/AST`
  (4xx) and `LATCH/TIMER/COUNTER/PSV` (7xx).
- Output bit conventions: `PLT04` (4xx, with `Q` suffix) vs `LT04`
  (7xx, no `Q`).
- **Already ~95% data-driven** — `relay_model.derived_bit_for()`,
  `port_label()`, `block_for_xml_type()` all route through the JSON.
  Hard-coded tables in `gle.py` only carry visual sizing per block type,
  which is the same per family across models.

### D. RDB ingestion
`pacct/parsers/rdb.py`, `pacct/matchers/relay_scd.py`.

- `_FAMILY_DEFAULT_SETTINGS = {"4xx": "set_p5.txt", "7xx": "set_p1.txt"}`
  — fallback when no JSON match for the model.
- Used as a fallback only; JSON wins when available.

### Key insight

`pacct/core/relay_models.py:RelayModel` is **already the driver seam — it's
just incomplete.** It owns layers C and parts of A. What's missing:

- **Live comms (B)** is split between `AsciiTargetReader` and inline
  dashboard code, branched at the caller via `fast_read`.
- **Settings normalization (A)** lives in a parallel module keyed on a
  `Family` literal instead of routed through the model.

This is not a greenfield architecture. It's completing a seam that's ~60%
there.

## Proposed `RelayDriver` interface

~12 methods across three capability groups.

```python
class RelayDriver(ABC):
    family: ClassVar[Literal["3xx", "4xx", "7xx"]]
    relay_model: RelayModel  # the loaded JSON for *this specific* model

    @classmethod
    @abstractmethod
    def matches(cls, relaytype: str) -> bool: ...           # "SEL-411L-A" → 4xx
    @classmethod
    @abstractmethod
    def for_model(cls, relaytype: str) -> "RelayDriver": ...

    # --- Settings layer (was settings_model.py family branches) ---
    def settings_dialect(self) -> Dialect: ...
    def settings_groups(self) -> tuple[Group, ...]: ...
    def normalize_section(self, ps, group, relay_name) -> GroupModel: ...
    def rdb_default_settings_file(self) -> str: ...         # set_p5.txt / set_p1.txt

    # --- Live comms layer (was AsciiTargetReader vs inline Fast Meter) ---
    def open_reader(self, host, port, creds) -> LiveReader: ...
    def resolve_analog_name(self, gle_name: str) -> str: ...

    # --- GLE / logic layer (delegates to relay_model JSON) ---
    def derived_bit_for(self, xml_type, instance, port) -> Optional[str]: ...
    def port_label(self, xml_type, side, index) -> Optional[str]: ...
    def block_for_xml_type(self, xml_type) -> Optional[BlockDef]: ...
    def is_analog_symbol(self, name) -> bool: ...
    def identifier_sources(self) -> dict[str, IdentifierSource]: ...
```

Plus a narrow `LiveReader` protocol — `read_bits(names) -> dict[str, bool]`
and `read_analogs(names) -> dict[str, float]`. Everything else stays
driver-internal (TARGET parsing and Fast Meter framing have nothing in
common at the wire level — that is fine; an abstraction is a *contract*,
not a *similarity*).

## What stays JSON vs. moves to code

**Stays in `data/relay_models/*.json`** (pure data, same shape per family):

- GLE bit-name conventions, port labels, block→XML-type mappings
- Analog group definitions and aliases
- Fast Meter bank layouts
- Identifier source list

**Moves to driver code** (behavior, regex, parsing logic):

- Settings normalization (regex patterns genuinely differ by family)
- Live reader implementation (TARGET vs Fast Meter)
- RDB default-settings filename fallback
- The `_NORMALIZERS` table — disappears, becomes `driver.normalize_section()`

So `SEL-411L` and `SEL-487E` both use `Sel4xxDriver` but load different
JSONs. `SEL-751` and `SEL-787` would both use `Sel7xxDriver`. Adding
`SEL-351` later = if it is a new family, one new directory; if it is a
4xx variant, one new JSON.

## Target layout

```
pacct/relays/
  __init__.py          # registry: get_driver(relaytype) -> RelayDriver
  base.py              # RelayDriver ABC + LiveReader protocol
  sel_3xx/
    __init__.py        # Sel3xxDriver
    settings.py        # 3xx settings normalization
  sel_4xx/
    __init__.py        # Sel4xxDriver
    settings.py        # 4xx settings normalization (SITM/SER/SHMI)
    live.py            # AsciiTargetReader, moved from core/target_region.py
  sel_7xx/
    __init__.py        # Sel7xxDriver
    settings.py        # 7xx settings normalization (ALIAS)
    live.py            # Fast Meter reader, extracted from dashboard.py
```

`data/relay_models/*.json` stays where it is — it is configuration, not code.

## Migration plan — 4 phases, ~1000 LOC touched of 14,483 total

### Phase 1 — Define the interface, no behavior change (~200 LOC new)

- Create `pacct/relays/base.py` with the ABC + `LiveReader` protocol.
- Create `pacct/relays/sel_3xx/`, `sel_4xx/`, `sel_7xx/` with stub driver
  classes that **delegate to existing code** (`_NORMALIZERS`,
  `AsciiTargetReader`, `RelayModel`).
- Create `pacct/relays/__init__.py:get_driver(relaytype)` registry.
- **Zero call sites change.** This phase just proves the interface is
  complete by making it work as a pass-through.

**Verify:** launch `python3 app.py --web`, confirm everything still works.

### Phase 2 — Migrate settings layer (~250 LOC moved, 5 callsites)

- `_3xx/4xx/7xx_normalize_section` functions move into their respective
  driver classes.
- `_NORMALIZERS` dict deleted; `normalize_relay()` calls
  `get_driver(relaytype).normalize_section(...)`.
- `settings_compare.py` calls `driver.settings_groups()` /
  `driver.settings_dialect()` instead of `groups_for_family(fam)`.
- Most contained layer — best to start here.

**Verify:** open Settings Compare, confirm 3xx/4xx/7xx all render correctly.

### Phase 3 — Migrate live comms (~400 LOC moved, 3 callsites)

- `AsciiTargetReader` moves into `sel_4xx/live.py`, exposed via
  `Sel4xxDriver.open_reader()`.
- Fast Meter code in `dashboard.py` extracts into `sel_7xx/live.py`.
- `dashboard.py` becomes driver-agnostic — calls
  `driver.open_reader(host, ...)` once at startup.
- Riskiest phase (needs a real relay or a sample RDB to verify).
  Plan time for live testing.

**Verify:** connect to a real SEL-411L and SEL-751, confirm the dashboard
streams bits and analogs correctly.

### Phase 4 — Clean up (~150 LOC deleted)

- `Family` literal type stays inside drivers but disappears from the
  public API.
- `family_from_relaytype()` becomes a thin wrapper over
  `get_driver(relaytype).family`.
- RDB default-settings fallback moves to
  `driver.rdb_default_settings_file()`.
- `gle.py` keeps its visual-sizing tables (they are per-block-type, not
  per-family — the driver does not need to own these).

## Things explicitly out of scope for v2.0

- **No plugin discovery** (entry points, decorators). Three families,
  explicit registry, done.
- **No deep `LiveReader` abstraction.** Beyond `read_bits()` /
  `read_analogs()`, do not force a shared interface.
- **JSON files stay in `data/relay_models/`.** They are configuration,
  not code.
- **`selprotopy` stays untouched.** Drivers call into it; the
  vendored-patched constraint does not change.
- **No new test framework, no formatter, no type checker added "while
  we are at it."** Separate decision.

## Risks

1. **3xx settings normalization is a stub today**
   (`_3xx_normalize_section` is barely populated). The driver pattern
   does not fix that — it gives a clean place to fill it in. If 3xx
   support matters for v2.0, scope that as a separate phase.
2. **Live verification needs hardware.** Phase 3 should be staged so it
   can be tested against actual SEL-411L and SEL-751 before merging.
   Sample RDBs verify *parsing*, not live comms.
3. **`RelayModel` class-name collision** — one in
   `core/relay_models.py` (GLE/JSON model), another in
   `core/settings_model.py` (normalized settings). The migration is a
   good moment to rename one. Suggested: settings one becomes
   `NormalizedRelay` or `SettingsTree`.

## Open questions

- Should v2.0 include filling out 3xx settings normalization, or is
  that v2.1?
- Is there a third family on the horizon (SEL-351, SEL-487B, etc.)
  whose requirements should shape the interface now rather than later?
- Rename `RelayModel` (settings) — what is the preferred new name?
