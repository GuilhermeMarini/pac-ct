#!/usr/bin/env python3
"""Synthetic stand-in for the four fixtures ``tools/capture_mms_fixtures.py``
records from a live relay. Written because the bench (203.0.113.61 and the
three others) was unreachable on both port 23 and 102 for this entire
session, and Tasks 5/6 need something to test against in the meantime.

    python3 tools/synth_mms_fixtures.py

Every fixture this writes carries a top-level ``provenance`` field that says,
in plain words, that it is NOT a real capture -- so a stand-in cannot be
silently mistaken for the real thing (``tests/test_mms_fixtures_provenance.py``
pins that the field exists and is non-empty in all four). Re-running
``capture_mms_fixtures.py`` against a live relay overwrites these files with
the same shape and a ``"provenance": "relay <host> <fid>"`` string instead.

WHAT IS GROUNDED, and WHAT IS EXTRAPOLATED
-------------------------------------------
Grounded in a live SEL-451-5, R331 (FID ``SEL-451-5-R331-V1-Z033014-D20250919``),
captured earlier in the same session that could not reach the bench again:

  * the IED name (``QPC1_TFE_UPC1``) is the common prefix of every logical
    device, and the LD suffixes are ``ANN``/``CFG``/``CON``/``MET``/``PRO``;
  * ``PLT1GGIO1$ST`` has exactly 133 directory entries: the container itself,
    plus 33 children (``Beh``, ``Ind01``..``Ind32``) x 4 -- the DO name plus
    its three DAs (``$q``, ``$stVal``, ``$t``). The arithmetic (1 + 33*4 = 133)
    is what confirms the flattening rule below;
  * ``get_data_definition`` decoded ``Beh`` as an 8-bit integer and every
    ``Ind##`` as boolean, each a ``{stVal, q, t}`` structure in that field
    order; a read came back as a list of ``[value, "0000000000000",
    {"utc": ..., "quality": 63}]`` per child -- reproduced here verbatim
    (quality bit-string all-clear, UtcTime TimeQuality octet 63).

Grounded in ``data/mms_map/451-010.json`` (Task 3's shipped ICD table, itself
generated from the vendor DNP profile, not invented here): which ``Ind##``
children exist under each of the five containers this synthesises
(``PLT1GGIO1$ST``/``ALT1GGIO1$ST``/``VB1XGGIO1$ST`` have 32; ``IN1XGGIO1$ST``
has 7; ``OUT1XGGIO1$ST`` has 8 -- read off the table, not assumed uniform).

EXTRAPOLATED -- re-verify when the bench is back:

  1. That the container-flattening rule (DO + {q, stVal, t}) holds for EVERY
     container, not just the one measured (``PLT1GGIO1$ST``). Applied here to
     every table leaf whose path is ``LN$FC$DO$stVal`` (the shape the table
     overwhelmingly uses), because that is the exact shape verified -- but
     for anything else (CO/MX/SP items, deeper SDI paths) this only adds the
     item's own ancestor path components, never invented sibling DAs.
  2. ``Beh`` on the four containers that were not read live. It is a
     mandatory LN0/GGIO attribute under IEC 61850's Mode common data class
     (present on every logical node regardless of which Relay Word bits a
     relay exposes), so its presence is standard, not guessed -- but its
     TYPE WIDTH and VALUES here are placeholders, not measured on those four.
  3. The 0.8% of table items a real relay does not serve is NOT modelled:
     this generator assumes every table item exists. Task 5's "drop what the
     relay does not confirm" logic is meant to be exercised by tests that
     remove entries explicitly, not by fabricated dropouts here.

One more honest gap: the real ANN directory measured ~12 735 entries; this
synthetic one is far smaller, because the shipped table only records items
that map to a Relay Word bit -- not the LD's full addressable variable set
(analog channels, settings, reports, ...). Matching bit-mapped ANN coverage
was the point; matching the raw entry count was never a goal.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from py61850.core import ber  # noqa: E402
from py61850.core import data as mms_data  # noqa: E402
from selfiles.scl import mms_tables  # noqa: E402

from pacct.paths import PROJECT_ROOT  # noqa: E402

OUT = PROJECT_ROOT / "tests" / "fixtures" / "mms"

IED = "QPC1_TFE_UPC1"
LD_SUFFIXES = ("ANN", "CFG", "CON", "MET", "PRO")
FID = "SEL-451-5-R331-V1-Z033014-D20250919"

PROVENANCE = (
    "SYNTHETIC -- not a real capture. Derived from data/mms_map/451-010.json "
    "(the shipped ICD table) plus the directory shape observed on a live "
    f"SEL-451-5 R331 ({FID}). Run tools/capture_mms_fixtures.py against a "
    "live relay to replace this stand-in; see that tool's module docstring."
)

CONTAINERS = ["PLT1GGIO1$ST", "ALT1GGIO1$ST", "VB1XGGIO1$ST",
              "IN1XGGIO1$ST", "OUT1GGIO1$ST"]

# The exact q/t shape read back from PLT1GGIO1$ST$Ind01 on the bench: quality
# bit-string all-clear (13 zero bits -- "good", core/quality.py), UtcTime
# TimeQuality octet 63. Reused for every synthetic child; only the stVal
# payload varies per child.
Q_GOOD = "0000000000000"
SYNTH_EPOCH = 1788022677.2473
SYNTH_TIME_QUALITY = 63


def _encode_field(name: str, value) -> bytes:
    """Encode one DA with the library's own type-specific encoder.

    `q` needs `encode_bitstring` explicitly -- a plain Python string would
    infer as VISIBLE-STRING through the generic `encode_data`. `t` needs
    `encode_utc`. `stVal` is inferred correctly by `encode_data` alone
    (bool -> boolean, int -> integer), which is all this stand-in ever needs.
    """
    if name == "q":
        return mms_data.encode_bitstring(value)
    if name == "t":
        return mms_data.encode_utc(value["utc"], value.get("quality", 0))
    return mms_data.encode_data(value)


def _encode_struct(*member_bytes: bytes) -> bytes:
    """`ber.tlv` + the STRUCTURE tag -- exactly what `encode_structure` does
    internally. Used directly (not `encode_structure`) because our members
    are already pre-encoded per-field; `encode_structure`'s type inference
    cannot tell a bit-string or a utc-time apart from a plain value."""
    return ber.tlv(mms_data.STRUCTURE, b"".join(member_bytes))


def _child_bytes(stval) -> bytes:
    """One DO's wire bytes: a `{stVal, q, t}` structure, field order as
    observed on the bench."""
    return _encode_struct(
        _encode_field("stVal", stval),
        _encode_field("q", Q_GOOD),
        _encode_field("t", {"utc": SYNTH_EPOCH, "quality": SYNTH_TIME_QUALITY}),
    )


def _read_response_bytes(children_bytes: list) -> bytes:
    """A genuine MMS read-Response [4] PDU wrapping one container structure --
    the same shape `MmsClient.read()` returns, decodable for real by
    `pdu.decode_read_response`."""
    container = _encode_struct(*children_bytes)
    access_result = ber.tlv(0xA1, container)           # listOfAccessResult [1]
    return ber.tlv(0xA4, access_result)                # read-Response [4]


def _child_type(name: str, stval_type) -> dict:
    return {"name": name, "type": {"structure": [
        {"name": "stVal", "type": stval_type},
        {"name": "q", "type": {"bit-string": -13}},
        {"name": "t", "type": "utc-time"},
    ]}}


def _children_of(container: str, table) -> list:
    """DO names under `container`, read off the shipped table's own `stVal`
    leaves -- grounded in real (generated) data, not invented per-container.
    `Beh` is prepended for every one: see the module docstring's point (2)."""
    inds = set()
    for _bit, (_ld, item) in table.bits.items():
        parts = item.split("$")
        if len(parts) == 4 and "$".join(parts[:2]) == container and parts[-1] == "stVal":
            inds.add(parts[2])
    return ["Beh"] + sorted(inds)


def _directory_for_ld(ld_suffix: str, table) -> set:
    """Expand the table's recorded leaves into a flat directory listing.

    See the module docstring for exactly what is confirmed (the container +
    DO + {q, stVal, t} flattening, verified on PLT1GGIO1$ST) versus what is
    conservative-by-design (every other item keeps only its own ancestor path
    components, never an invented sibling DA).
    """
    entries: set = set()
    for _bit, (ld, item) in table.bits.items():
        if ld != ld_suffix:
            continue
        parts = item.split("$")
        entries.add(item)
        for i in range(2, len(parts)):
            entries.add("$".join(parts[:i]))
        if len(parts) == 4 and parts[-1] == "stVal":
            do = "$".join(parts[:3])
            entries.add(do + "$q")
            entries.add(do + "$t")
    return entries


def _write(path: Path, key: str, payload) -> None:
    path.write_text(json.dumps({"provenance": PROVENANCE, key: payload}, indent=1))


def main() -> int:
    table = mms_tables.lookup("451")
    if table is None:
        raise SystemExit("data/mms_map has no 451 table -- run its generator first")

    OUT.mkdir(parents=True, exist_ok=True)

    ann_directory = sorted(_directory_for_ld("ANN", table))
    _write(OUT / "451_ann_directory.json", "directory", ann_directory)

    defs, reads, expected = {}, {}, {}
    for container in CONTAINERS:
        children = _children_of(container, table)
        stvals = [1 if name == "Beh" else bool(i % 2)
                  for i, name in enumerate(children)]
        defs[container] = {"mmsDeletable": False, "type": {"structure": [
            _child_type(name, {"integer": 8} if name == "Beh" else "boolean")
            for name in children
        ]}}
        raw = _read_response_bytes([_child_bytes(v) for v in stvals])
        reads[container] = base64.b64encode(raw).decode()
        expected[container] = dict(zip(children, stvals, strict=True))

    _write(OUT / "451_datadefs.json", "datadefs", defs)
    _write(OUT / "451_reads_b64.json", "reads", reads)
    _write(OUT / "451_expected_stvals.json", "expected", expected)

    print(f"sintetico gravado em {OUT} ({len(ann_directory)} entradas em ANN)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
