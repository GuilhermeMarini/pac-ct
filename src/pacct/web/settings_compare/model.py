"""The comparison itself: N relays of one family, variable by variable.

A verdict is SELOGIC equivalence and not a text diff -- `PSV01 := IN101 AND
IN102` and `PSV01:=IN102*IN101` are the same setting written twice, and
`sellib.selogic.compare` is what says so. What this module adds on top is the
part that only makes sense with more than two relays in hand: the union of the
variables the N relays declare, one row per field with the worst verdict
across the cells, and the two downgrades that keep a real diff from shouting
(`DISPLACED` for a bit recorded in another slot, `VB_DIFF` for a Virtual Bit
renumbering).

Imports nothing from `pacct.web`, and nothing from `state.py` either: it takes
normalised `RelayModel`s and gives back rows. Which RDBs a visitor has open,
and the cache of normalised relays, are `state.py`'s business -- nothing here
knows a request exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sellib.selogic.catalog import Dialect
from sellib.selogic.compare import Kind as CmpKind
from sellib.selogic.compare import compare
from sellib.selogic.model import RelayModel, Variable

# -----------------------------------------------------------------------------
# Diff computation
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CellPayload:
    relay: str       # "rdb_key|relay_name"
    label: str       # friendly name for the UI ("relay_name")
    rdb_filename: str
    value: str       # the field's `raw`
    body: str
    comment: str
    source_file: str
    source_lineno: int
    present: bool    # whether the field exists in this relay


@dataclass(frozen=True)
class FieldRow:
    field_name: str               # 'set' / 'reset' / 'input' / 'value' / etc.
    kind: str                     # 'logic' / 'number' / 'enum' / 'string'
    cells: list[CellPayload]
    verdict: str                  # EQUAL / EQUAL_LOGIC_DIFF_COMMENT / EQUIVALENT / DIFFERENT / MISSING
    note: str | None = None


@dataclass(frozen=True)
class VariableRow:
    name: str                     # 'PLT11' / 'LT01' / 'TR' / etc.
    var_kind: str                 # 'latch' / 'timer' / 'direct' / etc.
    fields: list[FieldRow]
    worst_verdict: str            # the worst across the fields


# DISPLACED = every comparable field is EQUAL/EQUAL_LOGIC_DIFF_COMMENT, but
# the "slot" field (position metadata in SITM<n>/ALIAS<n>) differs. The bit
# is recorded in every relay with the same content, only the position moved.
#
# VB_DIFF = the values differ only in VB### (Virtual Bits) token
# substitutions. Identical structure on both sides, only the VB number
# changed. Typical in SER (SITM=VB042 vs VB055) or in logic (PSV01 := VB001
# OR IN101 vs PSV01 := VB042 OR IN101). It can be a benign renumbering OR a
# completely different signal -- it needs human review, but it is not
# "definitely different".
def _verdict_severity(v: str) -> int:
    return {
        "EQUAL": 0,
        "EQUAL_LOGIC_DIFF_COMMENT": 1,
        "DISPLACED": 2,
        "EQUIVALENT": 3,
        "VB_DIFF": 4,
        "DIFFERENT": 5,
        "MISSING": 6,   # present in some relays, absent in others
    }.get(v, 7)


_VB_TOKEN_RE = re.compile(r'\bVB\d+\b', re.IGNORECASE)


def _is_vb_only_diff(a: str, b: str) -> bool:
    """True when `a` and `b` differ only in VB### token substitutions.

    Replaces each `VB\\d+` with a placeholder; if the normalised texts are equal
    AND there was at least one VB on either side, it is a VB-only diff.
    """
    if a == b:
        return False
    norm_a = _VB_TOKEN_RE.sub('VB#', a)
    norm_b = _VB_TOKEN_RE.sub('VB#', b)
    if norm_a != norm_b:
        return False
    return bool(_VB_TOKEN_RE.search(a) or _VB_TOKEN_RE.search(b))


# -----------------------------------------------------------------------------
# Sections -- subdividing the Relatorios tabs by kind of setting
# -----------------------------------------------------------------------------
#
# The "Relatorios" tab mixes settings of very different natures: SER
# chatter, SOE points/aliases, Signal Profile, Event Reporting (digital +
# analog), Fast Message Read, etc. Here we split that tab into semantic
# sections (one per family) so the user finds things faster.
#
# Variables matching no defined section fall into "Outros" at the foot of
# the tab -- nothing is lost from the diff.

@dataclass(frozen=True)
class Section:
    key: str
    label: str
    order: int
    exact: frozenset[str] = frozenset()       # exact match by name
    prefix: tuple[str, ...] = ()              # startswith
    kinds: frozenset[str] = frozenset()       # match by Variable.kind


DEFAULT_SECTION = Section("_other", "Outros", 9999)


R_SECTIONS: dict[tuple[str, str], tuple[Section, ...]] = {
    ("4xx", "R1"): (
        Section("ser_chatter", "SER Chatter Criteria", 10,
                 exact=frozenset({"ESERDEL", "SRDLCNT", "SRDLTIM"})),
        Section("ser_points", "SER Points e Aliases", 20,
                 kinds=frozenset({"ser_item"})),
        Section("signal_profile_analog",
                 "Signal Profile - Quantidades Analogicas", 30,
                 prefix=("SPAQ",)),
        Section("signal_profile", "Signal Profile (logica)", 40,
                 prefix=("SPAR", "SPEN")),
        Section("event_reporting", "Event Reporting", 50,
                 exact=frozenset({"ERDIG", "SRATE", "LER", "PRE"})),
        Section("event_reporting_analog",
                 "Event Reporting - Quantidades Analogicas", 60,
                 prefix=("ERAQ",)),
        Section("event_reporting_digital",
                 "Event Reporting - Elementos Digitais", 70,
                 prefix=("ERDG",)),
    ),
    ("7xx", "R"): (
        Section("ser_chatter", "SER Chatter Criteria", 10,
                 exact=frozenset({"ESERDEL", "SRDLCNT", "SRDLTIM"})),
        Section("ser_triggers", "SER Trigger Lists", 20,
                 exact=frozenset({"SER1", "SER2", "SER3", "SER4"})),
        Section("ser_aliases", "Relay Word Bit Aliases", 30,
                 exact=frozenset({"EALIAS"}),
                 kinds=frozenset({"ser_item"})),
        Section("event_report", "Event Report", 40,
                 exact=frozenset({"ER", "LER", "PRE"})),
        Section("hif_event_reporting", "HIF Event Reporting", 50,
                 exact=frozenset({"HIFLER", "HIFPRE"})),
        Section("fast_message_read", "Fast Message Read", 60,
                 prefix=("FMR",)),
        Section("fast_message_remote_analog",
                 "Fast Message Remote Analog", 70,
                 prefix=("RA",)),
        Section("load_profile", "Load Profile", 80,
                 exact=frozenset({"LDLIST", "LDAR"})),
    ),
    ("3xx", "R"): (
        Section("ser_lists", "SER", 10,
                 exact=frozenset({"SER1", "SER2", "SER3"})),
    ),
}


def classify_variable(
    family: str, group_key: str, var_name: str, var_kind: str,
) -> Section:
    """Decide which section a variable belongs to inside the tab. A
    variable with no section defined falls into `DEFAULT_SECTION`."""
    sections = R_SECTIONS.get((family, group_key))
    if not sections:
        return DEFAULT_SECTION
    for s in sections:
        if var_name in s.exact:
            return s
        if var_kind in s.kinds:
            return s
        if s.prefix and any(var_name.startswith(p) for p in s.prefix):
            return s
    return DEFAULT_SECTION


def _compute_field_row(
    field_name: str,
    kind: CmpKind,
    cells: list[CellPayload],
    dialect: Dialect,
) -> FieldRow:
    """Verdict across the N cells present. If any cell is absent, the
    verdict is MISSING (the present ones are not compared).

    Downgrades applied here:
      - `slot` field with a diff -> DISPLACED (bit recorded at another spot)
      - diff only in VB### tokens -> VB_DIFF (Virtual Bits renumbering)
    """
    presents = [c for c in cells if c.present]
    if len(presents) < len(cells):
        return FieldRow(
            field_name=field_name, kind=kind, cells=cells,
            verdict="MISSING",
            note=f"presente em {len(presents)} de {len(cells)} reles",
        )
    if len(presents) <= 1:
        return FieldRow(
            field_name=field_name, kind=kind, cells=cells,
            verdict="EQUAL",
        )

    # Pairwise verdict against the first one; keep the worst.
    worst = "EQUAL"
    note: str | None = None
    a_val = presents[0].value
    a_body = presents[0].body or a_val
    saw_real_diff = False
    all_diffs_vb_only = True
    for c in presents[1:]:
        r = compare(a_val, c.value, kind=kind, dialect=dialect)
        if r.verdict not in ("EQUAL", "EQUAL_LOGIC_DIFF_COMMENT"):
            saw_real_diff = True
            b_body = c.body or c.value
            if not _is_vb_only_diff(a_body, b_body):
                all_diffs_vb_only = False
        if _verdict_severity(r.verdict) > _verdict_severity(worst):
            worst = r.verdict
            note = r.note

    if field_name == "slot" and worst == "DIFFERENT":
        worst = "DISPLACED"
    elif worst == "DIFFERENT" and saw_real_diff and all_diffs_vb_only:
        worst = "VB_DIFF"

    return FieldRow(
        field_name=field_name, kind=kind, cells=cells,
        verdict=worst, note=note,
    )


def collect_variables(
    models: list[tuple[str, RelayModel]],   # (relay_key, RelayModel)
    group_key: str,
) -> list[VariableRow]:
    """Union of the variables present in the N relays for the given group,
    building one `VariableRow` per name with comparative `FieldRow`s."""
    # Index variables by (relay_key) -> dict[var_name -> Variable]
    per_relay: list[tuple[str, dict[str, Variable], RelayModel]] = []
    for relay_key, model in models:
        gm = model.groups.get(group_key)
        per_relay.append((
            relay_key,
            (gm.variables if gm else {}),
            model,
        ))

    all_var_names = sorted({n for _, vs, _ in per_relay for n in vs.keys()})

    dialect = models[0][1].dialect if models else "keyword"
    rows: list[VariableRow] = []
    for vname in all_var_names:
        # For each variable, gather every field_name that exists in any
        # relay (in canonical order per kind).
        var_kind_seen = None
        all_field_names: list[str] = []
        # preferred order; the rest goes at the end alphabetically
        preferred_order = (
            "set", "reset", "input", "pickup", "dropout",
            "count_up", "count_down", "load", "preset",
            "fail_safe", "value",
        )
        seen: set[str] = set()
        for _, vs, _ in per_relay:
            v = vs.get(vname)
            if v is None:
                continue
            if var_kind_seen is None:
                var_kind_seen = v.kind
            for f in v.fields:
                if f.name not in seen:
                    seen.add(f.name)
                    all_field_names.append(f.name)
        # Reorder per preferred order
        ordered = [n for n in preferred_order if n in seen]
        ordered += sorted(n for n in all_field_names if n not in ordered)

        field_rows: list[FieldRow] = []
        for fn in ordered:
            cells: list[CellPayload] = []
            kind_seen: CmpKind | None = None
            for relay_key, vs, model in per_relay:
                v = vs.get(vname)
                fld = v.get_field(fn) if v else None
                if fld is None:
                    cells.append(CellPayload(
                        relay=relay_key,
                        label=model.relay_name,
                        rdb_filename="",
                        value="",
                        body="",
                        comment="",
                        source_file="",
                        source_lineno=0,
                        present=False,
                    ))
                else:
                    if kind_seen is None:
                        kind_seen = fld.kind
                    cells.append(CellPayload(
                        relay=relay_key,
                        label=model.relay_name,
                        rdb_filename="",
                        value=fld.raw,
                        body=fld.body,
                        comment=fld.comment,
                        source_file=fld.source.file if fld.source else "",
                        source_lineno=fld.source.lineno if fld.source else 0,
                        present=True,
                    ))
            fr = _compute_field_row(fn, kind_seen or "string", cells, dialect)
            field_rows.append(fr)

        # The variable's verdict = worst of its fields. DISPLACED/VB_DIFF
        # were already applied in _compute_field_row.
        worst = "EQUAL"
        for fr in field_rows:
            if _verdict_severity(fr.verdict) > _verdict_severity(worst):
                worst = fr.verdict

        rows.append(VariableRow(
            name=vname, var_kind=var_kind_seen or "direct",
            fields=field_rows, worst_verdict=worst,
        ))
    return rows
