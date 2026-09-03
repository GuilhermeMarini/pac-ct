#!/usr/bin/env python3
"""Seed data/wordbits/<MODEL>.json from what the GLV already harvested.

Two sources, because the cache holds two shapes and neither is available for
every relay:

* ``cache/<FID>.json`` -- the GLV's name -> (row, bit) map. Its ``bit_to_pos``
  keys are every named bit that firmware reports.
* ``cache/<FID>_DNA.txt`` -- the raw Relay Word dump: a header line, then rows
  of eight quoted bit names plus a checksum, with ``*`` for an unnamed slot.

    python3 tools/wordbits_from_glv_cache.py cache/SEL-411L-A-R133-....json \\
        --model 411L --alias 411L-A
    python3 tools/wordbits_from_glv_cache.py cache/SEL-751-R402-..._DNA.txt \\
        --model 751

Merges into an existing file (union of ``bits``), so hand edits survive a
re-harvest.

This tool owns ONE half of a wordbits file. The other half -- the per-block
name domains for BO/AI/AO/CO -- comes from the SEL DNP3 device profile via
``tools/wordbits_from_dnp_profile.py``, and the two are deliberately not
interchangeable: a profile documents the factory DEFAULT point map, while a BI
point can be mapped to any Relay Word bit, so only what this tool harvests can
judge the BI block. Measured across the real RDB corpus: the profile alone
leaves 28.6% of BI values unrecognised; the Relay Word leaves 0%. Neither tool
overwrites the other's half.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfiles.models.wordbits import (  # noqa: E402
    DEFAULT_ALWAYS_VALID,
    KINDS,
    check_kinds_for,
)

from pacct.paths import WORDBITS_DIR  # noqa: E402

_QUOTED = re.compile(r'"([^"]*)"')


def _clean(names) -> set:
    """Upper-case, drop blanks, drop the `*` that marks an unnamed slot."""
    return {
        str(n).strip().upper()
        for n in names
        if str(n).strip() and str(n).strip() != "*"
    }


def harvest_json(path: Path) -> tuple[set, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _clean(raw.get("bit_to_pos", {})), str(raw.get("fid", ""))


def harvest_dna(path: Path) -> tuple[set, str]:
    """Read a Relay Word dump: eight quoted names per row, then a checksum."""
    bits: set = set()
    for line in path.read_text(encoding="latin-1").splitlines():
        tokens = _QUOTED.findall(line)
        if len(tokens) < 2:
            continue
        bits |= _clean(tokens[:-1])          # the last token is the checksum
    # The file name is "<FID>_DNA.txt".
    return bits, path.name[: -len("_DNA.txt")] if path.name.endswith("_DNA.txt") else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cache_file", type=Path,
                    help="cache/<FID>.json or cache/<FID>_DNA.txt from the GLV")
    ap.add_argument("--model", required=True, help="e.g.: 411L")
    ap.add_argument("--alias", action="append", default=[],
                    help="model alias; may repeat")
    args = ap.parse_args()

    if args.cache_file.suffix.lower() == ".json":
        bits, fid = harvest_json(args.cache_file)
    else:
        bits, fid = harvest_dna(args.cache_file)
    harvested = sorted(bits)
    if not harvested:
        print(f"no bits found in {args.cache_file}", file=sys.stderr)
        return 1

    WORDBITS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WORDBITS_DIR / f"SEL-{args.model}.json"

    if out_path.is_file():
        doc = json.loads(out_path.read_text(encoding="utf-8"))
        before = len(doc.get("bits", []))
    else:
        doc = {
            "schema_version": 2,
            "model": args.model,
            "model_aliases": [],
            "always_valid": sorted(DEFAULT_ALWAYS_VALID),
            "check_kinds": [],
            "kinds": {k: [] for k in KINDS},
            "bits": [],
            "patterns": [],
        }
        before = 0

    doc["model_aliases"] = sorted(set(doc.get("model_aliases", [])) | set(args.alias))
    doc["bits"] = sorted(set(doc.get("bits", [])) | set(harvested))

    # The first Relay Word harvest is what makes BI judgeable; recompute
    # rather than assume, so the same policy applies from either generator.
    kinds = {k: set(v) for k, v in (doc.get("kinds") or {}).items()}
    doc["check_kinds"] = check_kinds_for(kinds, set(doc["bits"]))

    # Write UNDER `source`, never over it: `source.dnp_profiles` records where
    # the other half of this file came from, and clobbering it would leave the
    # profile-derived name lists with no provenance at all.
    source = doc.get("source")
    if not isinstance(source, dict) or "dnp_profiles" not in source:
        # A schema-1 file whose whole `source` block was this tool's own.
        source = {"dnp_profiles": (source or {}).get("dnp_profiles", [])}
    source["relay_word"] = {"fid": fid,
                            "harvested_at": date.today().isoformat()}
    source.setdefault("generated", date.today().isoformat())
    doc["source"] = source

    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"{out_path}: {before} -> {len(doc['bits'])} bits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
