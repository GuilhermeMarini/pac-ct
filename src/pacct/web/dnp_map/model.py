"""Per-session edit state for the DNP map editor.

The state holds DIFFS, never documents: ``{relay: {session: {key: value}}}``.
A relay's SET_D is ~17 KB and a visitor may touch a dozen relays before
exporting; keeping parsed copies around would pin megabytes per session for
edits that are a handful of strings. It also means leaving the editor and
coming back loses nothing, and the batch export gets its input already shaped.

An edit that restores the original value is not an edit -- it is removed. That
keeps "pending changes" honest: a relay someone typed in and undid does not
show up as dirty.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from selfiles.dnp_map import DnpRelay, SetDnpFile
from selfiles.rdb import RdbInfo


@dataclass
class DnpMapState:
    """What one visitor has open and has changed."""

    # short sha -> RdbInfo, as in settings_compare
    rdbs: dict[str, RdbInfo] = field(default_factory=dict)
    # rdb_key -> relay -> session -> {key: value}
    edits: dict[str, dict[str, dict[str, dict[str, str]]]] = field(
        default_factory=dict)
    # rdb_key -> set_dnp.discover() result, cached because discover() walks
    # and parses every SET_D file of every relay in the whole RDB -- without
    # this, /map and /edit would redo that on every keystroke just to look up
    # one relay's sessions. Caching it forever (never invalidated) is sound
    # because rdb_key names a sha256-addressed extraction directory
    # (cache/rdb/<sha256>/): the files under it cannot change while that key
    # exists, since any change in content would be a different sha256 and
    # therefore a different key. A stale entry is only possible against a key
    # that no longer names this RDB, which is not a key we would look up.
    relay_cache: dict[str, list[DnpRelay]] = field(default_factory=dict)


def edits_for(st: DnpMapState, rdb_key: str, relay: str,
              session: str) -> dict[str, str]:
    return dict(st.edits.get(rdb_key, {}).get(relay, {}).get(session, {}))


def apply_edits(parsed: SetDnpFile, edits: dict[str, str]) -> SetDnpFile:
    """A copy of ``parsed`` with ``edits`` applied. Unknown keys are skipped.

    Skipping instead of raising is deliberate here (unlike ``set_value``): the
    edits come from a browser and may name a key from a session the visitor has
    since navigated away from. Dropping one is right; refusing the export is
    not.
    """
    out = copy.deepcopy(parsed)
    for key, value in edits.items():
        try:
            out.set_value(key, value)
        except KeyError:
            continue
    return out


def _bucket(st: DnpMapState, rdb_key: str, relay: str,
            session: str) -> dict[str, str]:
    return (st.edits.setdefault(rdb_key, {})
                    .setdefault(relay, {})
                    .setdefault(session, {}))


def _prune(st: DnpMapState, rdb_key: str, relay: str, session: str) -> None:
    """Drop empty levels so ``dirty_summary`` never reports a clean relay."""
    by_relay = st.edits.get(rdb_key)
    if not by_relay:
        return
    by_session = by_relay.get(relay)
    if by_session is None:
        return
    if not by_session.get(session):
        by_session.pop(session, None)
    if not by_session:
        by_relay.pop(relay, None)
    if not by_relay:
        st.edits.pop(rdb_key, None)


def record_edits(st: DnpMapState, lock, rdb_key: str, relay: str, session: str,
                 changes: dict[str, str], original: SetDnpFile) -> int:
    """Store ``changes`` as diffs against ``original``. Returns how many stuck."""
    stored = 0
    with lock:
        bucket = _bucket(st, rdb_key, relay, session)
        for key, value in changes.items():
            try:
                base = original.get_value(key)
            except KeyError:
                continue
            if value == base:
                bucket.pop(key, None)
            else:
                bucket[key] = value
                stored += 1
        _prune(st, rdb_key, relay, session)
    return stored


def current_value(st: DnpMapState, rdb_key: str, relay: str, session: str,
                  key: str, original: SetDnpFile) -> str:
    """What the field shows now: the pending edit, else the original."""
    pending = st.edits.get(rdb_key, {}).get(relay, {}).get(session, {})
    if key in pending:
        return pending[key]
    return original.get_value(key)


def swap(st: DnpMapState, lock, rdb_key: str, relay: str, session: str,
         key_a: str, key_b: str, original: SetDnpFile) -> None:
    """Exchange two points as a unit: value, plus scale and deadband.

    Swap and not insert-and-shift: a DNP index is a contract with the SCADA
    master, and inserting would silently renumber every point in between.

    The scale is an attribute of the mapped quantity, not of the DNP index --
    ``IA_MAG`` needs ``IA_MAG``'s scale wherever it ends up. So besides the
    base value, ``AI_SCAn``/``AI_DBDn``/``CO_DBDn`` travel with their point
    when both sides of the swap have one. A BI/BO row has neither: it swaps
    exactly as before. A point that has a scale but lands on an index whose
    file has no matching SCA/DBD line for it (no such row exists to hold the
    value) leaves that attribute where it is rather than inventing a key.

    Reading the current values and writing the swap happen under one lock
    acquisition: two concurrent swaps on the same bucket (a double-clicked
    drag, or two browser tabs) must not both read stale values and then have
    the second write clobber the first with data that was already out of
    date. The lock is an ``RLock``, so ``record_edits`` re-acquiring it here
    is safe.
    """
    with lock:
        points_by_key = {p.key: p for p in original.points()}
        point_a = points_by_key.get(key_a)
        point_b = points_by_key.get(key_b)

        pairs = [(key_a, key_b)]
        if point_a is not None and point_b is not None:
            for attr in ("sca_key", "dbd_key"):
                ka = getattr(point_a, attr)
                kb = getattr(point_b, attr)
                if ka is not None and kb is not None:
                    pairs.append((ka, kb))

        changes: dict[str, str] = {}
        for ka, kb in pairs:
            changes[ka] = current_value(st, rdb_key, relay, session, kb,
                                        original)
            changes[kb] = current_value(st, rdb_key, relay, session, ka,
                                        original)
        record_edits(st, lock, rdb_key, relay, session, changes, original)


@dataclass(frozen=True)
class CopyOutcome:
    """What one target session got out of a copy, for the screen to report.

    ``touched`` is how many of its fields actually changed -- a target that
    already read like the source gets 0 and no edits at all. ``missing`` and
    ``extra`` are the two ways two files can fail to line up: a key the source
    has and the target does not (its value had nowhere to land) and a point the
    target has and the source never mentions (it kept whatever it held). Both
    are 0 between two files of the same relay model, which is the only copy
    this tool offers -- they are counted anyway so a firmware that disagrees
    says so instead of producing a quietly half-copied map.
    """

    relay: str
    session: str
    touched: int
    missing: int
    extra: int


def _map_values(st: DnpMapState, rdb_key: str, relay: str, session: str,
                src: SetDnpFile,
                point_keys: set[str] | None = None) -> dict[str, str]:
    """The source map as it reads NOW: disk values under any pending edits.

    Only points -- ``src.points()`` and each point's ``sca_key``/``dbd_key`` --
    never ``src.extras()``. Fields like MINDIST/MAXDIST configure the DNP
    session, not the map, and are shown read-only in the editor; copying them
    would silently rewrite a target's own configuration whenever it differed.

    ``point_keys`` restricts the copy to those BASE point keys (``BI_00``,
    ``AI_3``); ``None`` means the whole map. The filter is on the base key
    only, and a chosen point still carries its ``AI_SCAn``/``AI_DBDn``: the
    scale is an attribute of the mapped quantity, not a point of its own, so
    copying ``IA_MAG`` without its scale would land a value the target reads
    at the wrong magnitude -- the same reason a drag in the editor takes them
    along.
    """
    src_edits = edits_for(st, rdb_key, relay, session)
    wanted: dict[str, str] = {}
    for point in src.points():
        if point_keys is not None and point.key not in point_keys:
            continue
        for key in (point.key, point.sca_key, point.dbd_key):
            if key is None:
                continue
            wanted[key] = src_edits.get(key, src.get_value(key))
    return wanted


def copy_map_to(st: DnpMapState, lock, src_rdb: str, src_relay: str,
                src_session: str, src_file: SetDnpFile, dst_rdb: str,
                targets: list[tuple[str, str]],
                parsed_by_relay: dict[str, dict[str, SetDnpFile]],
                point_keys: set[str] | None = None,
                ) -> list[CopyOutcome]:
    """Make each ``(relay, session)`` of ``dst_rdb`` read like the source.

    The source is ``src_file`` -- one parsed SET_D, addressed by
    ``(src_rdb, src_relay, src_session)`` only so that its own pending edits
    count as part of what is being copied. ``parsed_by_relay`` maps a relay of
    the DESTINATION rdb to its parsed sessions.

    Source and destination are separate rdb keys because a commissioning
    engineer's two files are the interesting case: the map was fixed in last
    week's RDB and the bank of relays that needs it lives in this week's. The
    edits are recorded under ``dst_rdb``, so they belong to that RDB's own
    "alterações pendentes" and leave in that RDB's own export; nothing is
    written into the source. When the two keys are equal the source session
    is skipped, which is what makes the single-relay ``copy_session`` below
    the same function.

    Each target is diffed against its OWN original, so a target that already
    matched gains no edits at all -- that is what keeps the pending count
    honest after a copy onto a bank of identical relays. A target the caller
    supplied no parsed file for is skipped.

    ``point_keys`` copies only part of the map (see ``_map_values``): a bank
    of identical relays often shares the binaries and diverges on the
    analogues, or needs only the block that was just corrected. What is left
    out is left ALONE in the target -- a copy never blanks a point.

    Whether two relays are the same model is not decided here: the route
    checks it (``set_dnp.same_model``) before building the target list,
    because refusing needs to name the two models in the error and this layer
    only sees files.

    The whole copy runs under one acquisition of ``lock`` (an ``RLock``, so
    the nested ``record_edits`` is fine): reading the source and writing every
    target has to be one step, or a concurrent ``/edit`` on the source lands
    in some targets and not others.
    """
    with lock:
        wanted = _map_values(st, src_rdb, src_relay, src_session, src_file,
                             point_keys)

        out: list[CopyOutcome] = []
        for relay_name, session_name in targets:
            if (dst_rdb == src_rdb and relay_name == src_relay
                    and session_name == src_session):
                continue
            target = parsed_by_relay.get(relay_name, {}).get(session_name)
            if target is None:
                continue
            keys = target.point_keys()
            touched = record_edits(st, lock, dst_rdb, relay_name, session_name,
                                   wanted, target)
            out.append(CopyOutcome(
                relay=relay_name,
                session=session_name,
                touched=touched,
                # Ambos sobre o que foi PEDIDO. Com uma selecao parcial, os
                # pontos que o usuario deixou de fora nao sao "sobrando no
                # destino" -- ele decidiu que ficam como estao, e contar isso
                # como desencontro transformaria uma copia parcial deliberada
                # num aviso amarelo em todo destino.
                missing=sum(1 for k in wanted if k not in keys),
                extra=(0 if point_keys is not None
                       else sum(1 for k in keys if k not in wanted)),
            ))
        return out


def copy_session(st: DnpMapState, lock, rdb_key: str, relay: str,
                 src_session: str, target_sessions: list[str],
                 parsed: dict[str, SetDnpFile]) -> int:
    """Make the other sessions OF ONE RELAY read like ``src_session``.

    The single-relay, single-RDB case of ``copy_map_to``: one relay's sessions
    all come from one firmware, so there is no model to check and nothing to
    report per target -- the total is the whole answer.
    """
    outcomes = copy_map_to(
        st, lock, rdb_key, relay, src_session, parsed[src_session], rdb_key,
        [(relay, name) for name in target_sessions], {relay: parsed})
    return sum(o.touched for o in outcomes)


def dirty_summary(st: DnpMapState, rdb_key: str, lock) -> list[dict]:
    """Relays with pending edits, for the 'pending changes' panel.

    Takes ``lock`` for the whole read: without it, a concurrent ``/edit``
    can run ``_prune`` between ``sorted(st.edits.get(rdb_key, {}))`` and
    indexing ``st.edits[rdb_key][relay]`` and empty that level out from
    under this function, which would otherwise raise ``KeyError``.
    """
    with lock:
        out: list[dict] = []
        for relay in sorted(st.edits.get(rdb_key, {})):
            sessions = st.edits[rdb_key][relay]
            counts = {name: len(sessions[name]) for name in sorted(sessions)
                      if sessions[name]}
            if counts:
                out.append({
                    "relay": relay,
                    "sessions": counts,
                    "total": sum(counts.values()),
                })
        return out
