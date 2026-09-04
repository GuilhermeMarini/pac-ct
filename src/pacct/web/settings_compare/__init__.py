"""
Settings Compare (Comparador de Ajustes): side-by-side diff of SEL relay
settings extracted from RDBs.

User flow:

  1. Pick one or more RDBs (fresh uploads or already extracted in `rdbs/`).
  2. Choose up to 7 relays of the SAME family (3xx/4xx/7xx).
  3. Choose settings groups (tabs: L1, S1, A1, etc.).
  4. Read the diff per tab; each variable shows a per-row verdict:
     EQUAL / EQUAL_LOGIC_DIFF_COMMENT / EQUIVALENT / DIFFERENT.

Shares the visual style with `vb_updater.py` and `vlan_mapper.py`. State per
process (singleton); the user clicks "<- Menu" to go back.

    templates/  index.html
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from selfiles.rdb import RdbInfo
from selfiles.rdb import short_sha as _short_sha
from selfiles.selogic.catalog import (
    Dialect,
    Family,
    family_from_relaytype,
    groups_for_family,
    is_relay_device,
)
from selfiles.selogic.compare import Kind as CmpKind
from selfiles.selogic.compare import compare
from selfiles.selogic.model import (
    RelayModel,
    Variable,
    normalize_relay,
)

from pacct.paths import SETTINGS_COMPARE_TEMPLATES_DIR
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionHandler

_logger = logging.getLogger(__name__)


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (SETTINGS_COMPARE_TEMPLATES_DIR / name).read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------

@dataclass
class _Session:
    """RDBs the user loaded (referenced by their short sha256)."""
    rdbs: dict[str, RdbInfo] = field(default_factory=dict)
    # Cache of the normalised RelayModel by (rdb_sha, relay_name).
    relay_cache: dict[tuple[str, str], RelayModel] = field(default_factory=dict)




# -----------------------------------------------------------------------------
# Discovery helpers
# -----------------------------------------------------------------------------

def _register_rdb(st: _Session, lock, info: RdbInfo) -> str:
    """Save the RdbInfo into the session state; returns the short sha."""
    key = _short_sha(info.sha256)
    with lock:
        st.rdbs[key] = info
    return key


def _get_or_normalize_relay(
    st: _Session, lock, rdb_key: str, relay_name: str,
) -> RelayModel | None:
    """Read and normalise one relay of an RDB (cache key (rdb_sha, name))."""
    with lock:
        info = st.rdbs.get(rdb_key)
    if info is None:
        return None
    cache_key = (info.sha256, relay_name)
    with lock:
        cached = st.relay_cache.get(cache_key)
    if cached is not None:
        return cached

    # Find the matching RelayEntry and infer the family.
    target_entry = None
    for r in info.relays:
        if r.name == relay_name:
            target_entry = r
            break
    if target_entry is None:
        return None
    fam = family_from_relaytype(target_entry.model)
    if fam is None:
        return None
    relay_dir = info.extract_dir / "Relays" / relay_name
    if not relay_dir.is_dir():
        return None
    model = normalize_relay(relay_dir, fam, relay_name=relay_name)
    with lock:
        st.relay_cache[cache_key] = model
    return model


def _rdb_summary(rdb_key: str, info: RdbInfo) -> dict:
    """Summary of one RDB for the frontend payload."""
    relays = []
    for r in info.relays:
        fam = family_from_relaytype(r.model)
        relays.append({
            "name": r.name,
            "model": r.model,
            "ip": r.ip,
            "family": fam,                  # None if it is not a protection relay
            "is_relay": is_relay_device(r.model),
        })
    return {
        "key": rdb_key,
        "sha256": info.sha256,
        "filename": info.display_name,
        "reused": info.reused,
        "relays": relays,
    }


def _list_groups_for_relays(
    st: _Session, lock,
    rdb_relays: list[tuple[str, str]],
) -> tuple[Family | None, list[dict]]:
    """Given a set of (rdb_key, relay_name), return the common family and
    the list of groups (catalogue) with `present` telling whether the group
    exists in every selected relay."""
    if not rdb_relays:
        return None, []
    models: list[RelayModel] = []
    for rdb_key, relay_name in rdb_relays:
        m = _get_or_normalize_relay(st, lock, rdb_key, relay_name)
        if m is None:
            return None, []
        models.append(m)
    fams = {m.family for m in models}
    if len(fams) != 1:
        return None, []
    fam = next(iter(fams))

    catalog = groups_for_family(fam)
    out = []
    for g in catalog:
        present_in = [g.key in m.groups for m in models]
        out.append({
            "key": g.key,
            "label": g.label,
            "file": g.file_basename,
            "has_logic": g.has_logic,
            "present_in_all": all(present_in),
            "present_in_any": any(present_in),
            "present_count": sum(present_in),
            "total": len(models),
        })
    return fam, out


# -----------------------------------------------------------------------------
# Diff computation
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class _CellPayload:
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
class _FieldRow:
    field_name: str               # 'set' / 'reset' / 'input' / 'value' / etc.
    kind: str                     # 'logic' / 'number' / 'enum' / 'string'
    cells: list[_CellPayload]
    verdict: str                  # EQUAL / EQUAL_LOGIC_DIFF_COMMENT / EQUIVALENT / DIFFERENT / MISSING
    note: str | None = None


@dataclass(frozen=True)
class _VariableRow:
    name: str                     # 'PLT11' / 'LT01' / 'TR' / etc.
    var_kind: str                 # 'latch' / 'timer' / 'direct' / etc.
    fields: list[_FieldRow]
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
class _Section:
    key: str
    label: str
    order: int
    exact: frozenset[str] = frozenset()       # exact match by name
    prefix: tuple[str, ...] = ()              # startswith
    kinds: frozenset[str] = frozenset()       # match by Variable.kind


_DEFAULT_SECTION = _Section("_other", "Outros", 9999)


_R_SECTIONS: dict[tuple[str, str], tuple[_Section, ...]] = {
    ("4xx", "R1"): (
        _Section("ser_chatter", "SER Chatter Criteria", 10,
                 exact=frozenset({"ESERDEL", "SRDLCNT", "SRDLTIM"})),
        _Section("ser_points", "SER Points e Aliases", 20,
                 kinds=frozenset({"ser_item"})),
        _Section("signal_profile_analog",
                 "Signal Profile - Quantidades Analogicas", 30,
                 prefix=("SPAQ",)),
        _Section("signal_profile", "Signal Profile (logica)", 40,
                 prefix=("SPAR", "SPEN")),
        _Section("event_reporting", "Event Reporting", 50,
                 exact=frozenset({"ERDIG", "SRATE", "LER", "PRE"})),
        _Section("event_reporting_analog",
                 "Event Reporting - Quantidades Analogicas", 60,
                 prefix=("ERAQ",)),
        _Section("event_reporting_digital",
                 "Event Reporting - Elementos Digitais", 70,
                 prefix=("ERDG",)),
    ),
    ("7xx", "R"): (
        _Section("ser_chatter", "SER Chatter Criteria", 10,
                 exact=frozenset({"ESERDEL", "SRDLCNT", "SRDLTIM"})),
        _Section("ser_triggers", "SER Trigger Lists", 20,
                 exact=frozenset({"SER1", "SER2", "SER3", "SER4"})),
        _Section("ser_aliases", "Relay Word Bit Aliases", 30,
                 exact=frozenset({"EALIAS"}),
                 kinds=frozenset({"ser_item"})),
        _Section("event_report", "Event Report", 40,
                 exact=frozenset({"ER", "LER", "PRE"})),
        _Section("hif_event_reporting", "HIF Event Reporting", 50,
                 exact=frozenset({"HIFLER", "HIFPRE"})),
        _Section("fast_message_read", "Fast Message Read", 60,
                 prefix=("FMR",)),
        _Section("fast_message_remote_analog",
                 "Fast Message Remote Analog", 70,
                 prefix=("RA",)),
        _Section("load_profile", "Load Profile", 80,
                 exact=frozenset({"LDLIST", "LDAR"})),
    ),
    ("3xx", "R"): (
        _Section("ser_lists", "SER", 10,
                 exact=frozenset({"SER1", "SER2", "SER3"})),
    ),
}


def _classify_variable(
    family: str, group_key: str, var_name: str, var_kind: str,
) -> _Section:
    """Decide which section a variable belongs to inside the tab. A
    variable with no section defined falls into `_DEFAULT_SECTION`."""
    sections = _R_SECTIONS.get((family, group_key))
    if not sections:
        return _DEFAULT_SECTION
    for s in sections:
        if var_name in s.exact:
            return s
        if var_kind in s.kinds:
            return s
        if s.prefix and any(var_name.startswith(p) for p in s.prefix):
            return s
    return _DEFAULT_SECTION


def _compute_field_row(
    field_name: str,
    kind: CmpKind,
    cells: list[_CellPayload],
    dialect: Dialect,
) -> _FieldRow:
    """Verdict across the N cells present. If any cell is absent, the
    verdict is MISSING (the present ones are not compared).

    Downgrades applied here:
      - `slot` field with a diff -> DISPLACED (bit recorded at another spot)
      - diff only in VB### tokens -> VB_DIFF (Virtual Bits renumbering)
    """
    presents = [c for c in cells if c.present]
    if len(presents) < len(cells):
        return _FieldRow(
            field_name=field_name, kind=kind, cells=cells,
            verdict="MISSING",
            note=f"presente em {len(presents)} de {len(cells)} reles",
        )
    if len(presents) <= 1:
        return _FieldRow(
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

    return _FieldRow(
        field_name=field_name, kind=kind, cells=cells,
        verdict=worst, note=note,
    )


def _collect_variables(
    models: list[tuple[str, RelayModel]],   # (relay_key, RelayModel)
    group_key: str,
) -> list[_VariableRow]:
    """Union of the variables present in the N relays for the given group,
    building one `_VariableRow` per name with comparative `_FieldRow`s."""
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
    rows: list[_VariableRow] = []
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

        field_rows: list[_FieldRow] = []
        for fn in ordered:
            cells: list[_CellPayload] = []
            kind_seen: CmpKind | None = None
            for relay_key, vs, model in per_relay:
                v = vs.get(vname)
                fld = v.get_field(fn) if v else None
                if fld is None:
                    cells.append(_CellPayload(
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
                    cells.append(_CellPayload(
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

        rows.append(_VariableRow(
            name=vname, var_kind=var_kind_seen or "direct",
            fields=field_rows, worst_verdict=worst,
        ))
    return rows


def _compute_diff_payload(
    st: _Session, lock,
    relay_refs: list[dict],                # [{rdb_key, relay_name}]
    group_keys: list[str],
    on_progress=None,
) -> dict:
    """Run the full diff and return a JSON-serialisable payload.

    `on_progress(feitos, total, etapa)` feeds the client's bar: normalising
    each relay's settings is the expensive part, and there are up to 5 relays
    per comparison.
    """
    models: list[tuple[str, RelayModel]] = []
    for i, ref in enumerate(relay_refs):
        rdb_key = ref["rdb_key"]
        relay_name = ref["relay_name"]
        if on_progress is not None:
            on_progress(i, len(relay_refs) or 1, f"Lendo ajustes: {relay_name}")
        m = _get_or_normalize_relay(st, lock, rdb_key, relay_name)
        if m is None:
            return {"error": f"rele nao encontrado: {rdb_key}/{relay_name}"}
        key = f"{rdb_key}|{relay_name}"
        models.append((key, m))

    # Common family (should already have been validated in the UI)
    fams = {m.family for _, m in models}
    if len(fams) != 1:
        return {"error": "selecao de reles cruza familias diferentes"}
    fam = next(iter(fams))

    # Fill rdb_filename in the cells -- we need the RDB info
    with lock:
        rdb_filenames = {
            rk: st.rdbs[rk].display_name if rk in st.rdbs else ""
            for rk in {ref["rdb_key"] for ref in relay_refs}
        }

    def _var_payload(vr: _VariableRow) -> dict:
        return {
            "name": vr.name,
            "var_kind": vr.var_kind,
            "worst_verdict": vr.worst_verdict,
            "fields": [
                {
                    "name": fr.field_name,
                    "kind": fr.kind,
                    "verdict": fr.verdict,
                    "note": fr.note,
                    "cells": [
                        {
                            "relay": c.relay,
                            "label": c.label,
                            "rdb_filename": rdb_filenames.get(
                                c.relay.split("|")[0], ""
                            ),
                            "value": c.value,
                            "body": c.body,
                            "comment": c.comment,
                            "source_file": c.source_file,
                            "source_lineno": c.source_lineno,
                            "present": c.present,
                        }
                        for c in fr.cells
                    ],
                }
                for fr in vr.fields
            ],
        }

    groups_out = []
    for gk in group_keys:
        rows = _collect_variables(models, gk)

        # Group variables by section. Families/groups with no section
        # defined all fall into a single "_default" section (empty label --
        # the frontend renders no header for that one).
        sections_meta = _R_SECTIONS.get((fam, gk))
        if sections_meta:
            buckets: dict[str, list[_VariableRow]] = {s.key: [] for s in sections_meta}
            buckets[_DEFAULT_SECTION.key] = []
            for vr in rows:
                sec = _classify_variable(fam, gk, vr.name, vr.var_kind)
                buckets[sec.key].append(vr)
            ordered_sections = list(sections_meta) + [_DEFAULT_SECTION]
            sections_out = [
                {
                    "key": sec.key,
                    "label": sec.label,
                    "variables": [_var_payload(vr) for vr in buckets[sec.key]],
                }
                for sec in ordered_sections
                if buckets[sec.key]
            ]
        else:
            sections_out = [{
                "key": "_default",
                "label": "",
                "variables": [_var_payload(vr) for vr in rows],
            }]

        groups_out.append({"key": gk, "sections": sections_out})

    # Also returns the group labels so the UI can show a pretty name.
    catalog = {g.key: g.label for g in groups_for_family(fam)}
    return {
        "family": fam,
        "dialect": models[0][1].dialect,
        "relays": [
            {
                "key": k,
                "label": m.relay_name,
                "relaytype": m.relaytype,
                "rdb_filename": rdb_filenames.get(k.split("|")[0], ""),
            }
            for k, m in models
        ],
        "group_labels": catalog,
        "groups": groups_out,
    }


# -----------------------------------------------------------------------------
# HTML / JS / CSS
# -----------------------------------------------------------------------------

INDEX_HTML = load_template("index.html")

# The numbered navigation is the same on the nine screens -- lives in theme.py.


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------

def build_settings_compare_handler(logger: logging.Logger, sessions) -> type:
    """Return the Settings Compare handler class.

    Opens no server: serving is done by the single dispatcher of
    `pacct.web.mount`, which mounts this handler at `/settings-compare/`.
    State and uploads are per session (`self.sess()` / `self.sdir()`), not
    per process -- cleanup included: the session's whole directory goes away
    when it expires.
    """

    class Handler(SessionHandler):
        session_key = "settings-compare"
        state_factory = _Session
        server_sessions = sessions

        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw or b"{}")
            except (json.JSONDecodeError, ValueError):
                return None

        def _ensure_rdbs(self, keys) -> None:
            """Ensure every short key is in `st.rdbs`.

            A safety net on the same principle as `/state`: the RDB is in
            the visitor's library, so no route has to demand that the page
            "adopted" it first. Without this, a tab left open while the
            session lost its state would answer "rele nao encontrado" for a
            file that is right there.
            """
            st = self.sess()
            for key in keys:
                with self.require_session().lock:
                    if key in st.rdbs:
                        continue
                entry = self.library_entry(key, filelib.KIND_RDB)
                if entry is not None and entry.rdb is not None:
                    with self.require_session().lock:
                        st.rdbs[_short_sha(entry.rdb.sha256)] = entry.rdb

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML, "text/html; charset=utf-8")
                return
            if path == "/settings-state":
                # Sentinel the home uses to detect that this tool is up.
                self._send_json(200, {"ok": True})
                return
            if path == "/state":
                # The PROJECT's library, not "the RDBs this tool has
                # adopted". There used to be two lists on screen -- the
                # project's files in a selector and the adopted ones in a
                # list beside it -- and a "Usar" button that only copied a
                # pointer from one to the other. It was the leftover of when
                # the comparator had an upload of its own. Listing also
                # registers into `st.rdbs`, which is what makes choosing a
                # file a single click: `/groups` and `/diff` still find the
                # RDB by its short key with no step at all.
                st = self.sess()
                lib = filelib.library_for(sessions, self.require_session())
                with self.require_session().lock:
                    entries = [e for e in lib.list(filelib.KIND_RDB)
                               if e.rdb is not None]
                    for e in entries:
                        st.rdbs[_short_sha(e.require_rdb().sha256)] = e.require_rdb()
                    rdbs = [_rdb_summary(_short_sha(e.require_rdb().sha256), e.require_rdb())
                            for e in entries]
                rdbs.reverse()
                self._send_json(200, {"rdbs": rdbs})
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path

            if path == "/groups":
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON invalido"})
                    return
                refs = body.get("relays") or []
                pairs = [(r["rdb_key"], r["relay_name"]) for r in refs]
                self._ensure_rdbs({k for k, _ in pairs})
                fam, groups = _list_groups_for_relays(self.sess(), self.require_session().lock, pairs)
                if fam is None:
                    self._send_json(400, {"error": "familia inconsistente ou rele invalido"})
                    return
                self._send_json(200, {"family": fam, "groups": groups})
                return

            if path == "/diff":
                job = self.job()
                job.stage("Lendo ajustes dos reles", 10)
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "JSON invalido"})
                    return
                refs = body.get("relays") or []
                group_keys = body.get("groups") or []
                self._ensure_rdbs({r.get("rdb_key") for r in refs
                                   if r.get("rdb_key")})
                try:
                    payload = _compute_diff_payload(
                        self.sess(), self.require_session().lock, refs, group_keys,
                        on_progress=lambda d, t, st: job.fraction(st, d, t),
                    )
                except Exception as e:
                    logger.exception("[settings-compare] erro computando diff")
                    job.fail(str(e))
                    self._send_json(500, {"error": f"erro interno: {e}"})
                    return
                if "error" in payload:
                    job.fail(payload["error"])
                    self._send_json(400, payload)
                    return
                job.finish("Diff pronto")
                self._send_json(200, payload)
                return

            self._send(404, "not found", "text/plain")

    return Handler
