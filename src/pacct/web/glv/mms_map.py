"""Relay Word bit -> MMS item, checked against the relay itself.

Two sources, in this order: the project SCD (the map AS BUILT) and the factory
table derived from the ICD. Neither of them is discovered on the relay -- the
bit's name lives in the SCL's `sAddr`, which the relay does NOT serve over
MMS. There is no equivalent of telnet's `TAR <name>` here, and there never
will be.

The FC does come from the relay: we match `LN$*$DO$DA` against the
GetLogicalDeviceDirectory. That resolves the FC and checks the entry at once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from selfiles.scl.mms_tables import (
    da_parts,
    fc_rank,
    is_boolean_status,
    is_enum_do,
    is_enum_status,
)


@dataclass(frozen=True)
class MmsPoint:
    bit: str
    ld: str            # full logical device name, e.g. QPC1_TFE_UPC1ANN
    container: str     # LN$FC -- the read unit
    child: str         # the DO inside the container
    item: str          # LN$FC$DO$DA
    # The path TO THE LEAF inside the child, e.g. ("stVal",) or
    # ("general",). Without it the decoder only knew how to read `stVal`, and
    # an ACD/ACT point -- 43 of the 222 addressable bits of LT2_UPC1 measured
    # on the corpus, `TRIP` among them -- entered the coverage and was NEVER
    # read. It has no default on purpose: a point built without saying what it
    # reads is exactly the bug.
    leaf: tuple
    # How to pull THIS bit out of the item's value, when the item carries
    # more than one. `None` on a boolean point (most of them), and then the
    # value read is the bit. On a decorated point -- `db:52A|52B?0:1:2:3` on a
    # `Pos$stVal` -- it is the `BitRule` that `mms_tables.decode_bit` applies.
    # The polling loop never does `int(bool(...))` on a point with a rule: a
    # Dbpos comes back from py61850 as the string "10", and `bool("00")` is
    # True.
    rule: object | None = None


@dataclass(frozen=True)
class Coverage:
    total: int
    mapped: int
    missing: tuple

    @property
    def fraction(self) -> float:
        return (self.mapped / self.total) if self.total else 0.0


@dataclass
class MmsMap:
    points: dict = field(default_factory=dict)
    source: str = ""

    def containers(self) -> dict:
        """`(ld, LN$FC) -> [MmsPoint]` -- how the map spreads over the relay.

        It used to be the read plan: grouping by container was what made the
        polling fit the budget when each reading cost one request (30 req /
        ~180 ms for the whole diagram against 170 / 739 ms bit by bit,
        measured on the bench 451). With py61850's `read_refs` -- one Read
        naming several leaves -- the cost stopped being counted in containers,
        and the plan is the list of leaves (see `transport/mms.py`). This here
        is left over as DIAGNOSTICS: how many `LN$FC` the map touches, and
        which.
        """
        out: dict = {}
        for p in self.points.values():
            out.setdefault((p.ld, p.container), []).append(p)
        return out

    def coverage(self, wanted) -> Coverage:
        wanted = {b.upper() for b in wanted}
        missing = tuple(sorted(b for b in wanted if b not in self.points))
        return Coverage(total=len(wanted), mapped=len(wanted) - len(missing),
                        missing=missing)


def ld_suffixes(lds, suffixes=None) -> dict:
    """`suffix -> full name`.

    When `suffixes` is given -- the SCD's `ld_inst` and/or the fallback
    table's `ld_suffix`, that is, exactly the suffixes the caller already
    knows it is looking for -- it matches each LD by `endswith`, longest
    suffix first. The order matters: without it, `CON` would be shadowed
    by `ON` whenever both are in the list of known suffixes.

    Without `suffixes` -- no caller uses this path today, kept only so as
    not to return rubbish -- it falls back to the common prefix across
    several LDs, and for a single LD it returns the identity: there is no
    second name to compare against, and guessing would be lying.
    """
    lds = list(lds)
    if not lds:
        return {}
    if suffixes:
        ordered = sorted({s for s in suffixes if s}, key=len, reverse=True)
        out: dict = {}
        for ld in lds:
            for suf in ordered:
                if ld.endswith(suf):
                    out[suf] = ld
                    break
        return out
    if len(lds) == 1:
        return {lds[0]: lds[0]}
    prefix = os.path.commonprefix(lds)
    return {ld[len(prefix):] or ld: ld for ld in lds}


def _fc_index(names) -> dict:
    """`(ln, "do$da_with_$")  -> [fc, ...]` -- an index of the whole
    directory, built once per set of names.

    Matching against a fixed list of FCs (what the original Task 5 did)
    only finds the FCs that are in that list; the directory is what rules.
    `SG` (setting group, 186 points in the corpus) would never be in
    FC_PREFERENCE and is still a real FC the relay serves -- this inversion
    resolves any FC present in the directory, catalogued or not.
    """
    index: dict = {}
    for name in names:
        parts = name.split("$")
        if len(parts) < 4:
            continue  # only a container (LN$FC) or LN$FC$DO, no DA
        ln, fc = parts[0], parts[1]
        rest = "$".join(parts[2:])  # "DO$DA" or "DO$SDI$DA" flattened
        index.setdefault((ln, rest), []).append(fc)
    return index


def _readable(sp) -> bool:
    """Can this `ScdPoint` be read as a bit?

    With no rule, the leaf has to be a boolean status. With a rule, an
    enumerated status -- it is the enumeration's value that carries the bits.
    A rule does not rescue a float or a command: the decoration says HOW to
    decode an enumeration, it does not turn a non-reading into a reading.
    """
    rule = getattr(sp, "rule", None)
    if rule is None:
        # `is_enum_do` is what stops a `Pos$stVal` coming in through the
        # boolean path: the DA alone does not separate an SPS from a DPS.
        return is_boolean_status(sp.da) and not is_enum_do(sp.do)
    return is_enum_status(sp.da)


def _rule_from_table(entry):
    """The `BitRule` of a factory-table row, or `None`.

    The row is JSON, so the rule arrives as a list -- `[alternatives, index,
    nbits]` -- and becomes again the same `BitRule` the SCD produces. A
    malformed row is `None`, that is, the point falls to the boolean gate
    instead of becoming an invented reading.
    """
    if len(entry) < 3 or not entry[2]:
        return None
    from selfiles.scl.mms_tables import BitRule
    try:
        alternatives, index, nbits = entry[2]
        return BitRule(alternatives=tuple(int(a) for a in alternatives),
                       index=int(index), nbits=int(nbits))
    except (TypeError, ValueError):
        return None


def resolve_map(*, wanted, directory, ld_by_suffix, scd_points=None,
                table=None) -> MmsMap:
    """Builds the map of the requested bits. Only what the relay confirms
    it serves gets in."""
    wanted = {b.upper() for b in wanted}
    scd_points = scd_points or {}
    points: dict = {}
    used_scd = used_table = False
    fc_index_by_suffix: dict = {}

    def _fc_candidates(ld_inst: str, ln: str, do: str, da_path: str) -> list:
        idx = fc_index_by_suffix.get(ld_inst)
        if idx is None:
            idx = _fc_index(directory.get(ld_inst) or ())
            fc_index_by_suffix[ld_inst] = idx
        return idx.get((ln, f"{do}${da_path}"), [])

    for bit in wanted:
        sp = scd_points.get(bit)
        # Only what can be PAINTED as a bit gets in. With no rule, the
        # leaf has to be a BOOLEAN status: an `instMag.f` is a float, an
        # `actVal` is a counter, an `Oper.ctlVal` is a COMMAND --
        # `int(bool(x))` of any of them is not a bit reading, it is
        # invention. Beyond that the coverage would lie twice: counting the
        # point as covered and leaving `state.error` stuck at the partial-read
        # message while everything is fine.
        #
        # WITH a rule (`db:52A|52B?0:1:2:3`), the leaf has to be an
        # ENUMERATED status -- it is the enumeration's value that carries the
        # bits. Demanding the rule is the gate: `Pos$stVal` also passes
        # `is_boolean_status`, so a position point with no rule would come in
        # through the boolean path and come out as a breaker closed for ever.
        if sp is not None and _readable(sp):
            ld = ld_by_suffix.get(sp.ld_inst)
            # `sAddr`'s `da` uses '.' to descend into a nested SDI (Task
            # 2, e.g. "Oper.ctlVal"); MMS joins EVERY level with '$'. Without
            # this swap "RBGGIO1$CO$SPCSO01$Oper.ctlVal" never matches the
            # directory, and the drop would be indistinguishable from "the
            # relay does not serve it". Today the boolean filter above only
            # lets a ONE-level leaf through, so the join is the identity -- it
            # stays because what can change is the filter, and not the rule of
            # how MMS spells a name.
            da_path = "$".join(da_parts(sp.da))
            candidates = _fc_candidates(sp.ld_inst, sp.ln, sp.do,
                                        da_path) if ld else []
            fc = min(candidates, key=fc_rank) if candidates else None
            if fc:
                points[bit] = MmsPoint(
                    bit=bit, ld=ld, container=f"{sp.ln}${fc}", child=sp.do,
                    item=f"{sp.ln}${fc}${sp.do}${da_path}",
                    leaf=da_parts(sp.da), rule=getattr(sp, "rule", None))
                used_scd = True
                continue
        # A bit whose SCD address is not readable may still have a usable
        # address in the factory table: falling back to it beats discarding
        # the bit because of the first source.
        if table is None:
            continue
        entry = table.bits.get(bit)
        if entry is None:
            continue
        # The table holds `[suffix, item]` or, on a decorated point,
        # `[suffix, item, [alternatives, index, nbits]]`. The two-element rows
        # are the overwhelming majority and go on being valid as they are --
        # the third is optional so as not to invalidate the published tables.
        suffix, item = entry[0], entry[1]
        rule = _rule_from_table(entry)
        ld = ld_by_suffix.get(suffix)
        names = directory.get(suffix) or ()
        if not ld or item not in names:
            continue
        parts = item.split("$")
        if len(parts) < 4:
            continue                      # with no DA there is nothing to read
        ln, fc, do = parts[0], parts[1], parts[2]
        leaf = tuple(parts[3:])
        if not (is_enum_status(leaf) if rule is not None
                else (is_boolean_status(leaf) and not is_enum_do(do))):
            continue
        points[bit] = MmsPoint(bit=bit, ld=ld, container=f"{ln}${fc}",
                               child=do, item=item, leaf=leaf, rule=rule)
        used_table = True

    source = ("scd+tabela" if used_scd and used_table
              else "scd" if used_scd else "tabela" if used_table else "")
    return MmsMap(points=points, source=source)
