"""What one visitor has open, and the payloads the routes answer with.

Separate from `model.py` on the same grounds `gle_tabs` separates its two
halves: that module is `RelayModel`s in and rows out, and would lift out of
this repository unchanged. This one holds the per-session registry of RDBs and
the cache of normalised relays, so every function here takes the session's
lock along with the state.

It still imports nothing from `pacct.web` -- a lock and a dataclass are not
the web layer. The routes that call these are in `handler.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sellib.rdb import RdbInfo
from sellib.selogic.catalog import (
    Family,
    family_from_relaytype,
    groups_for_family,
    is_relay_device,
)
from sellib.selogic.model import RelayModel, normalize_relay

from pacct.web.settings_compare import model


@dataclass
class SettingsCompareState:
    """RDBs the user loaded (referenced by their short sha256)."""
    rdbs: dict[str, RdbInfo] = field(default_factory=dict)
    # Cache of the normalised RelayModel by (rdb_sha, relay_name).
    relay_cache: dict[tuple[str, str], RelayModel] = field(default_factory=dict)



# -----------------------------------------------------------------------------
# Discovery helpers
# -----------------------------------------------------------------------------

def _get_or_normalize_relay(
    st: SettingsCompareState, lock, rdb_key: str, relay_name: str,
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
    # `normalized`, not `model`: at module scope that name is now the sibling
    # module, and a local shadowing it here would be a trap for the next edit.
    normalized = normalize_relay(relay_dir, fam, relay_name=relay_name)
    with lock:
        st.relay_cache[cache_key] = normalized
    return normalized


def rdb_summary(rdb_key: str, info: RdbInfo) -> dict:
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


def list_groups_for_relays(
    st: SettingsCompareState, lock,
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


def compute_diff_payload(
    st: SettingsCompareState, lock,
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

    def _var_payload(vr: model.VariableRow) -> dict:
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
        rows = model.collect_variables(models, gk)

        # Group variables by section. Families/groups with no section
        # defined all fall into a single "_default" section (empty label --
        # the frontend renders no header for that one).
        sections_meta = model.R_SECTIONS.get((fam, gk))
        if sections_meta:
            buckets: dict[str, list[model.VariableRow]] = {s.key: [] for s in sections_meta}
            buckets[model.DEFAULT_SECTION.key] = []
            for vr in rows:
                sec = model.classify_variable(fam, gk, vr.name, vr.var_kind)
                buckets[sec.key].append(vr)
            ordered_sections = list(sections_meta) + [model.DEFAULT_SECTION]
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
