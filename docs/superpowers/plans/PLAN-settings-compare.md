# Settings Compare — Implementation Plan

Tool name: **Comparador de Ajustes** (Settings Compare)
URL slug: `settings-compare`
Status: planned, not started

## Goal

Add a fifth dashboard tool that compares SEL relay settings across up to 5 relays
within the same family. Input is one or more RDB files (existing in `rdbs/` or freshly
uploaded). Output is a tabbed diff view (one tab per settings group) with four verdicts
per variable: `EQUAL`, `EQUAL_LOGIC_DIFF_COMMENT`, `EQUIVALENT`, `DIFFERENT`.

Cross-family compare is blocked at the relay-picker step.

## Scope

### In scope
- Families: **3xx** (SEL-311C, SEL-311L), **4xx** (SEL-411L, SEL-487E), **7xx** (SEL-751, SEL-787).
- Whole-group selection up front; per-variable filter inside each tab; "differences only" toggle.
- Logic equivalence via dialect-aware parser + canonical AST + truth-table fallback (≤16 atoms).
- Numeric/enum compare with float tolerance.
- Display dormant settings (gated by an OFF enable flag) with a "dormant" badge.

### Out of scope (v1)
- Cross-reference chasing (if `PLT04 := f(PSV13)` and `PSV13` differs, we still report
  `PLT04` text-level only).
- SEL-2414 / SEL-2440 (listed but disabled for compare; tooltip explains).
- Editing/patching settings — read-only diff.
- Export of diff to Excel/PDF.

## Family coverage matrix

| Family | Logic grammar | Latch encoding | Comment syntax |
|---|---|---|---|
| 3xx | `*` AND, `+` OR, `!` NOT, `/X` rising-edge, `\X` falling-edge, no `:=` | `SET1`/`RST1` → `LT1` (slot) | none |
| 4xx | `AND`/`OR`/`NOT`/`R_TRIG`/`F_TRIG`, `:=` | `PLT01S`/`PLT01R` → `PLT01` (LHS-grouped) | `# ...` inside string |
| 7xx | same as 4xx (keyword grammar) | `SET01`/`RST01` → `LT01` (slot) | `# ...` |

### Settings group catalog (per family — these become tabs)

- **3xx (311C)**: `1`–`6`, `L1`–`L6`, `G`, `M`, `R`, `T`, `P1`–`P5`, `PF`, `D1`–`D3`
- **3xx (311L)**: `1`–`6`, `L1`–`L6`, `G`, `R`, `T`, `P1`–`P5`, `PF`, `DNPA`, `DNPB`, `X`, `Y`
- **4xx**: `S1`–`S6`, `L1`–`L6` (PROTSEL), `A1`–`A10` (AUTO), `G1`, `R1`, `D1`–`D5`, `P1`–`P5`, `B1`, `F1`, `T1`, `PF`, `N1`, `O1`
- **7xx**: `1`–`4`, `L1`–`L4`, `G`, `M`, `R`, `F`, `I`, `P1`–`P4`, `D1`–`D3`, `E1`–`E3`, `PF`, `HMI`

Groups present per relay are derived from the actual `SET_*.TXT` / `set_*.txt` files in the
extraction. Missing groups → tab hidden for that relay (or shown with "—" cells).

## Architecture

```
sellib/
  parsers/
    rdb.py                       (existing — reused)
    sel_settings.py              (NEW: line-level INI parser, family-agnostic)
  core/
    selogic_parser.py            (NEW: dialect-aware lexer + AST)
    logic_compare.py             (NEW: 4-bucket comparator)
    settings_model.py            (NEW: family normalizer — raw lines → Variable map)
    settings_catalog.py          (NEW: per-family group catalog + variable identity rules)
  web/
    settings_compare.py          (NEW: routes + HTML + JS)
    dashboard.py                 (EDIT: tile + dispatcher entry)
data/relay_models/
  SEL-311C.json                  (NEW: minimal — just enough for compare to recognise it)
  SEL-311L.json                  (NEW: same)
```

## Backend tasks (atomic, in order)

1. **`sel_settings.py`** — parse `KEY,"VALUE"` (and bare `KEY,VALUE`) lines under `[SECTION]` headers. Return `ParsedSettings(file, section, info, lines=[Line(key, raw_value, source)])`. No family logic. Handle both `SET_*.TXT` (uppercase) and `set_*.txt` (lowercase) filenames. Strip BOMs; tolerate `latin-1`.

2. **`selogic_parser.py`** — lexer with `dialect` param (`"symbolic"` | `"keyword"`):
   - Symbolic: `*`/`+`/`!`/`(`/`)`/`/IDENT`/`\IDENT`/atoms (alnum + `_` + digits).
   - Keyword: `AND`/`OR`/`NOT`/`R_TRIG`/`F_TRIG`/`(`/`)`/atoms; also `:=`, `#` comment.
   - Recursive descent → AST (`AndNode`, `OrNode`, `NotNode`, `EdgeNode(kind, atom)`, `AtomNode(name)`, `LiteralNode(value)`).
   - Precedence (both dialects): parens > NOT/edge > AND > OR.
   - Reject math expressions in the boolean parser; expose a `looks_like_math()` helper for the caller.

3. **`logic_compare.py`** — public API `compare(a_raw, b_raw, dialect, kind) -> Verdict`:
   - `Verdict = Literal["EQUAL", "EQUAL_LOGIC_DIFF_COMMENT", "EQUIVALENT", "DIFFERENT"]` + optional `note`.
   - For `kind="logic"`:
     1. Strip whitespace and `# comment`. If both bodies + comments match → `EQUAL`.
     2. If logic bodies match (without comment) but comments differ → `EQUAL_LOGIC_DIFF_COMMENT`.
     3. Parse both with `dialect`. Canonicalize AST (sort commutative chains alphabetically by string repr, fold double-NOT, flatten chains). If canonical forms match → `EQUIVALENT`.
     4. Else collect atoms. If `|atoms_union| ≤ 16` → build truth tables and compare → `EQUIVALENT` or `DIFFERENT`.
     5. Else → `DIFFERENT` with `note="not exhaustively verified (>16 atoms)"`.
   - For `kind="number"`: parse both as float, compare with `abs(a-b) ≤ max(1e-6, 1e-6*max(|a|,|b|))`.
   - For `kind="enum"` / `kind="string"`: trimmed string equality.

4. **`settings_catalog.py`** — for each family, a list of `Group(key, label, file_glob, variable_rules)`. `variable_rules` tells the normalizer how to group lines:
   - 3xx: `{kind: "direct", key_is_var: True}` for most lines; `{kind: "latch_slot", set="SET<N>", reset="RST<N>", out="LT<N>"}` inside `L*` files; `{kind: "sv_slot", input="SV<N>", pu="SV<N>PU", do="SV<N>DO", out="SV<N>"}`.
   - 4xx: `{kind: "lhs_grouped"}` — parse `LHS := RHS # comment`, group by LHS pattern (e.g., `PLT\d+[SR]` → `PLT\d+`).
   - 7xx: same as 3xx but with keyword grammar.

5. **`settings_model.py`** — `normalize(family, relay_extract_dir) -> dict[group_key, dict[var_name, Variable]]`. Walks all settings files for the relay; emits the `Variable` model described in the discussion (kind/expressions/value/comment/sources). Caches per `(rdb_sha, relay_name)`.

6. **`data/relay_models/SEL-311C.json` and `SEL-311L.json`** — only the fields the existing registry actually reads (model, model_aliases, ip_address.file, ip_address.key, relay_id, mac_address: null, fast_read: null). No GLE block conventions needed for this tool. Use the `add-relay-model` skill if it makes scaffolding easier.

7. **`sellib/web/settings_compare.py`** — copy structure from `vb_updater.py` and `vlan_mapper.py`. Endpoints (relative to `/`):
   - `GET /settings-state` — current state for frontend (selected RDBs/relays/groups).
   - `POST /settings-upload-rdb` — accept RDB upload, delegate to `rdb.process_upload`.
   - `GET /settings-rdbs` — list existing RDBs from `rdbs/` with relay summaries.
   - `POST /settings-pick-relays` — body `{rdb_sha, relays: [...]}`; server validates same-family.
   - `GET /settings-groups?relays=...` — returns group catalog intersected with what each relay actually has.
   - `POST /settings-diff` — body `{relays, groups}`; returns `{group_key: [{var_name, kind, cells: [{relay, value, comment, dormant, source}, ...], verdict, note}]}`.
   - `GET /settings-back` — signal "return to home menu" (matches existing tools).

8. **`dashboard.py` edits** — three insertions:
   - Add tile button in `HOME_HTML` around line 2783 (next to gle-exporter).
   - Add `'settings-compare': '/settings-state'` to the state-URL map around line 2853.
   - Add `"settings-compare"` to the valid-tools set around line 2932.
   - Add dispatch branch in the home-loop around line 3762 calling `run_settings_compare(args.port, logger)`.

## Frontend tasks (single HTML page served by `settings_compare.py`)

UI flow as a three-step wizard inside one page (matches existing tools' feel):

- **Step 1 — RDBs & relays**:
  - Two columns: existing RDBs (from `rdbs/`) on the left, upload dropzone on the right.
  - Click an RDB → relay list appears with family/model/IP badge. Multi-select up to 5.
  - SEL-2414/2440 listed but disabled with tooltip "Não é relé de proteção".
  - Family validator runs on each click; mixed-family selection disables the Next button with explainer text.
- **Step 2 — Settings groups**:
  - Per-family catalog. Group cards with checkboxes. Disabled card if not present on at least one selected relay.
  - "Selecionar todos" / "Limpar" buttons.
- **Step 3 — Results**:
  - Tab strip across the top, one tab per selected group.
  - Inside each tab: a sticky-first-column table.
    - Column 1: variable name + kind icon (latch/timer/var/direct/enum/number).
    - Columns 2…N: one per selected relay; cell shows current value + verdict-tinted background.
    - For latches: expand row shows `set:` / `reset:` subrows.
    - For timers: expand row shows `input:` / `PU:` / `DO:` subrows.
  - Toolbar: "Apenas diferenças" toggle, free-text variable filter, "Exportar resumo" (deferred — note as not implemented for v1).
  - Cell hover shows source `(file, line)`.

UI strings in Portuguese (per docs/ENGINEERING-NOTES.md). Code identifiers in English.

## Verification approach

No automated test suite (per docs/ENGINEERING-NOTES.md). Manual verification:

1. Launch `python3 app.py --web`; navigate to home → click Settings Compare tile.
2. **Smoke**: pick `rdbs/substation_demo.rdb`. List relays — confirm models display correctly (487E, 411L, 311C, 311L, 751, plus 2414/2440 grayed out).
3. **Same-family within one RDB**: select two `QPC2_TR1_UPC*` 7xx relays. Pick group `L1`. Confirm diff shows.
4. **Across-RDB compare**: pick `_R1e` and `_R1g` revisions of the same site, same relay name. Diff `L1` and `S1`. Expect many `EQUAL`, a few `DIFFERENT` reflecting genuine revision deltas.
5. **Mixed-family reject**: select a 487E and a 751 → Next button disabled, message visible.
6. **Equivalence**: hand-edit a copy of an extracted `set_L1.txt` so one variable swaps operand order (e.g. `A OR B` → `B OR A`); confirm verdict is `EQUIVALENT`, not `DIFFERENT`. (For this test, the RDB isn't repacked — we'd need to point the parser at the extraction directory. Alternative: write a tiny smoke script that calls the comparator on two literal strings.)
7. **Comment-only diff**: edit a copy so one equation has a different `# comment`; expect `EQUAL_LOGIC_DIFF_COMMENT`.
8. **3xx grammar**: pick two 311C or 311L relays, diff `L1`. Confirm operators `*`/`+`/`!`/`/` are parsed correctly (canonicalization should produce the same AST regardless of operand order).
9. **Atom-overflow path**: not easy to test on real data (rare). Manual unit-style script with a 17-atom equation; confirm we return `DIFFERENT` with the `not exhaustively verified` note rather than hanging.

A `tools/check_settings_compare.py` smoke script (not a test framework — just `if __name__ == "__main__"`) is fine to commit alongside the modules to make 6/7/9 reproducible.

## Caveats and known gaps

1. **3xx variable identity**: 311C/L use short names. We treat `SET1`/`RST1` as defining `LT1` and group them. But 311L 87L logic exposes `R1X`/`R2X` (MIRRORED BITS-style remote bits) inside `set_X.txt` / `set_Y.txt` — these are atomic relay-word bits, not assignments. The X/Y files mostly hold k/v config (line params, CT ratios), not boolean equations. Treat as plain k/v group.
2. **PCT timers in 4xx span automation + protection**: a `PCT07` may have `PCT07IN`/`PCT07PU`/`PCT07DO` lines across the same SET_L file. The LHS-grouped normalizer handles this — group by stripped suffix.
3. **`R_TRIG`/`F_TRIG` is atomic**: do NOT descend into the argument. `R_TRIG (A OR B)` is invalid SELOGIC per the 751 manual p.365 anyway.
4. **Operator precedence in canonical AST**: SEL evaluates left-to-right within a precedence class. Our AST is associative for AND/OR (we flatten), which matches SEL semantics.
5. **Float tolerance**: 3xx settings often display as `0.00`; 4xx/7xx as `0.000000`. Both compare equal via the numeric path.
6. **Settings absent from one relay**: render `—` in that cell, exclude from verdict (treat verdict for that variable as `DIFFERENT (only in some relays)`).
7. **3xx has no inline comments**: the `EQUAL_LOGIC_DIFF_COMMENT` bucket can only fire on 4xx/7xx. Document this in the UI legend.
8. **Math expressions in 4xx/7xx**: e.g., `AMV003 := (IASFMC + IBSFMC + ICSFMC) / 3`. Comparator path is "kind=number"-like — string equal after whitespace normalization with float tolerance for any embedded numerals. No symbolic algebra.
9. **Performance**: per-relay normalization caches by `(rdb_sha, relay_name)` in-process. First diff click for a freshly-uploaded RDB will be slow; subsequent diffs are instant.
10. **selprotopy**: not touched. Block-on-edit hook still applies.

## Deferred (out of v1, captured for backlog)

- Export diff to Excel using `openpyxl` (already a dep).
- Cross-reference chase: if `A` references `B` and `B` differs, escalate `A` to `EQUIVALENT (referenced atom differs)`.
- Multi-relay alignment beyond 5 (probably never needed).
- Side-by-side raw text view per variable (popover with rendered diff).
- Settings-history view: pick one relay across many RDB revisions, walk through changes.
- Optional: include 3xx grammar variants if SEL-351 RDBs ever show up (same grammar, slightly different group catalog).

## File touchpoints summary

| File | Action | LOC estimate |
|---|---|---|
| `sellib/parsers/sel_settings.py` | NEW | ~150 |
| `sellib/core/selogic_parser.py` | NEW | ~250 |
| `sellib/core/logic_compare.py` | NEW | ~200 |
| `sellib/core/settings_catalog.py` | NEW | ~120 |
| `sellib/core/settings_model.py` | NEW | ~250 |
| `sellib/web/settings_compare.py` | NEW | ~800 (mostly HTML/JS) |
| `sellib/web/dashboard.py` | EDIT | +10 |
| `data/relay_models/SEL-311C.json` | NEW | ~30 |
| `data/relay_models/SEL-311L.json` | NEW | ~30 |
| `tools/check_settings_compare.py` (optional smoke script) | NEW | ~80 |

Total ~1900 LOC across ~10 files. Roughly one focused session of work end-to-end,
with the parser+comparator (~600 LOC of logic) being the load-bearing piece and the
web module being mostly HTML/JS plumbing.
