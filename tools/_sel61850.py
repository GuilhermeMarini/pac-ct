"""Shared half of the two 61850-coverage generators in `tools/`.

Both `relay_word_vs_61850.py` and `gle_vs_61850.py` answer the same question --
"which SEL Relay Word names have no IEC 61850 address?" -- and differ only in
the baseline they subtract from. Everything below is the part they share: the
two map sources, the part-key normalisation that reconciles them, and the
family grouping the reports print.

The name of a Relay Word bit reaches 61850 only through the SCL attribute
`sAddr="db:NAME"`. That attribute is NOT served over MMS -- verified against a
live SEL-451-5 R331, where `<LN>$DC$<DO>$d` answers `object-non-existent` -- so
the map can only come from a file. There is no equivalent of the telnet path's
`TAR <name>` discovery.

**The sAddr walk and the part-key normalisation live in `pacct`, not here.**
`pacct/parsers/scd.py:sel_short_addresses` and
`pacct/core/mms_tables.py:norm_part` are the same two things the GLV's MMS
transport uses on the live path, and this module used to carry its own copy of
both. Two copies of the rule that decides which bit has an address is exactly
how a report and a running tool come to disagree about the same SCD -- and the
reports under `fixtures/` are the evidence base for the coverage numbers in the
spec. Verified over the whole corpus before the copies were deleted: identical
bit sets, and both reports reproduced byte for byte.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from pacct.core.mms_tables import norm_part  # noqa: F401  (re-exportado)
from pacct.parsers.scd import load_scd as _ied_infos
from pacct.parsers.scd import sel_short_addresses


def load_scd(path: Path) -> dict:
    """Parse an SCD/CID. Returns a dict of by-IED maps.

    `bits[ied]` is every Relay Word name that IED gives a 61850 address to;
    `part[ied]` is the ICD part its configVersion declares, so a caller can
    fall back to the factory map for the same part. Keys are `norm_part` of
    the IED name -- the ICD file writes `311C-1`, the configVersion writes
    `311C1` and the RDB writes its own; folding them is what stopped the 311C
    matching nothing and reporting 100% missing.

    Two passes over the file, deliberately: ~0.7 s on the 23 MB corpus SCD,
    against having one implementation of the sAddr walk instead of two.
    """
    bits, part, cfgver, name_of = {}, {}, {}, {}
    for ied_name, points in sel_short_addresses(Path(path)).items():
        key = norm_part(ied_name)
        name_of[key] = ied_name
        bits[key] = set(points)
    for ied in _ied_infos(Path(path)):
        key = norm_part(ied.name)
        cv = ied.config_version or ""
        cfgver[key] = cv
        m = re.match(r"ICD-([A-Z0-9]+)-", cv)
        part[key] = norm_part(m.group(1)) if m else None
    return {"bits": bits, "part": part, "cfgver": cfgver, "name": name_of}


def load_icd(path: Path) -> tuple[dict, dict]:
    """Load the ICD-derived map. Returns `(part -> bits, part -> [keys])`.

    Only entries that actually carry bits count: the older ICD groups parse to
    an empty `bits` list, and treating those as "a map with nothing in it"
    would report every bit of that model as unaddressable.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))["models"]
    bits: dict = collections.defaultdict(set)
    groups: dict = collections.defaultdict(list)
    for key, entry in raw.items():
        if not entry.get("bits"):
            continue
        part, _group = key.split("/", 1)
        bits[norm_part(part)] |= {b["bit"].upper() for b in entry["bits"]}
        groups[norm_part(part)].append(key)
    return dict(bits), {k: sorted(v) for k, v in groups.items()}


# Families, most specific first -- `PCT01Q` must not fall into "elemento ANSI"
# just because a later pattern would also match it.
FAMILIES: tuple = (
    ("saidas de bloco SELOGIC (sufixo Q/R)", r"^(P|A)(CT|CN|ST)\d+[QR]$"),
    ("contadores/temporizadores SELOGIC 7xx", r"^S[CV]\d+[A-Z]+$"),
    ("latch bits (LB_)",                      r"^LB_"),
    ("botoeiras (PB)",                        r"^PB"),
    ("alvos de LED",                          r".*LED"),
    ("seccionadora (89*)",                    r"^89"),
    ("Fast Operate (FOP)",                    r"^FOP"),
    ("RTD",                                   r"^RTD"),
    ("elemento ANSI",                         r"^\d"),
)


def family_of(bit: str) -> str:
    for label, pattern in FAMILIES:
        if re.match(pattern, bit):
            return label
    return "outros"


def by_family(bits) -> list:
    """Group names by family, biggest group first."""
    grouped: dict = collections.defaultdict(list)
    for b in bits:
        grouped[family_of(b)].append(b)
    return sorted(((f, sorted(v)) for f, v in grouped.items()),
                  key=lambda kv: (-len(kv[1]), kv[0]))


def columns(names, per_line: int = 6, width: int = 13, indent: str = "     ") -> list:
    out = []
    names = list(names)
    for i in range(0, len(names), per_line):
        row = "".join(f"{n:<{width}}" for n in names[i:i + per_line])
        out.append(indent + row.rstrip())
    return out
